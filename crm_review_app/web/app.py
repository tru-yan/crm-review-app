"""The review app: a queue of proposals, one page per proposal, the change history and the pipeline runs.

Only the two POST routes can change anything, and only through review.approve and review.reject.
"""
import secrets
import threading
from pathlib import Path
from urllib.parse import quote, urlencode, urlsplit

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader
from starlette.exceptions import HTTPException  # the base class, so the handler below also catches routing 404s
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .. import config, store
from ..crm import CrmClient
from . import present, review

HERE = Path(__file__).parent
PORT = 8000
ORIGINS = {f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}"}  # pages posting a form must come from here
LOCAL_HOSTS = ["127.0.0.1", "localhost"]  # the only host names the app answers to
TABS = ("pending", "applied", "rejected", "failed", "superseded", "all")


def create_app(conn, client, meridian_base: str) -> FastAPI:
    """meridian_base: the CRM browser's address up to /accounts. It holds the token, so pages only link to it
    through the /meridian redirect. Every approval is confirmed before anything is written to the sandbox."""
    app = FastAPI(title="CRM Review App", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.csrf_token = secrets.token_urlsafe(32)
    lock = threading.Lock()  # requests run in worker threads; they take turns on the one SQLite connection
    templates = Jinja2Templates(env=Environment(loader=FileSystemLoader(HERE / "templates"), autoescape=True))
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    # only this machine's own names: a page fetched under another host name (DNS rebinding) never gets token links
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=LOCAL_HOSTS)

    @app.middleware("http")
    async def no_framing(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Frame-Options"] = "DENY"  # no clickjacking: another site can't frame these pages
        response.headers["Content-Security-Policy"] = "frame-ancestors 'none'"
        return response

    meridian = meridian_base.rstrip("/")

    def meridian_url(account_id) -> str | None:
        """A link to the account in the CRM browser, through /meridian here (which redirects). The CRM browser's
        address holds the token, so it never appears in a page, where hovering a link would show it on screen."""
        return f"/meridian/accounts/{quote(str(account_id), safe='')}" if account_id else None

    templates.env.globals.update(meridian_url=meridian_url, kind_label=present.kind_label, type_of=present.type_of,
                                 field_label=present.field_label, when=present.when, show=present.show)

    def page(request: Request, template: str, status_code: int = 200, /, **context):
        context |= {"pending": store.counts(conn).get("pending", 0), "csrf_token": app.state.csrf_token}
        return templates.TemplateResponse(request, template, context, status_code=status_code)

    def everything():
        rows = [present.parse(r) for r in store.list_proposals(conn)]
        return rows, present.account_names(rows)

    def banner(rows, names, last: str, outcome: str):
        p = next((r for r in rows if r["key"] == last), None)
        return present.banner(p, outcome, names) if p else None

    def not_found(request: Request, key: str):
        return page(request, "error.html", 404, heading="Proposal not found", nav="queue",
                    message=f"There is no proposal with the key {key}.")

    def check_post(request: Request, token: str) -> None:
        sent = request.headers.get("origin")
        if not sent and request.headers.get("referer"):
            parts = urlsplit(request.headers["referer"])
            sent = f"{parts.scheme}://{parts.netloc}"
        if sent and sent not in ORIGINS:
            raise HTTPException(403, "This request came from another site, so it was refused.")
        if not secrets.compare_digest(token.encode(), app.state.csrf_token.encode()):
            raise HTTPException(403, "This form is out of date. Reload the page and try again.")

    def after_decision(key: str, outcome: str) -> RedirectResponse:
        rows, _ = everything()
        target = present.next_pending(rows, key)
        path = f"/proposals/{target}" if target else "/queue"
        return RedirectResponse(f"{path}?{urlencode({'last': key, 'outcome': outcome})}", status_code=303)

    @app.exception_handler(HTTPException)
    def refused(request: Request, exc: HTTPException):
        """403, 404 and 405 as a page with a way back, not raw JSON (an app restart makes open forms stale)."""
        with lock:
            response = page(request, "error.html", exc.status_code, message=exc.detail)
        response.headers.update(exc.headers or {})
        return response

    @app.get("/")
    def home():
        return RedirectResponse("/queue", status_code=303)

    @app.get("/meridian/accounts")
    @app.get("/meridian/accounts/{account_id}")
    def to_meridian(account_id: str = ""):
        """Open the CRM browser. This redirect is the only place its token-bearing address appears."""
        path = f"/accounts/{quote(account_id, safe='')}" if account_id else "/accounts"
        return RedirectResponse(f"{meridian}{path}", status_code=303)

    @app.get("/queue", response_class=HTMLResponse)
    def queue(request: Request, status: str = "pending", kind: str = "", q: str = "", sort: str = "ar",
              order: str = "", last: str = "", outcome: str = ""):
        """order: asc or desc; empty for the sort's usual direction. Clicking the sorted column's header flips it."""
        status = status if status in TABS else "pending"
        sort = sort if sort in present.SORTS else present.SORTS[0]
        order = order if order in ("asc", "desc") else ""
        descending = order == "desc" if order else sort in present.DESCENDING
        with lock:
            rows, names = everything()
            counts = store.counts(conn)
            run = store.latest_run(conn)
            shown = [p for p in rows if (status == "all" or p["status"] == status)
                     and (not kind or present.type_of(p["kind"]) == kind) and present.matches(p, q)]
            items = [{"p": p, "name": present.display_name(p), "place": present.place(p),
                      "summary": present.summary(p, names), **present.billing_cells(p)}
                     for p in present.queue_order(shown, sort, descending, names)]
            tabs = [(t, t.capitalize(), sum(counts.values()) if t == "all" else counts.get(t, 0)) for t in TABS]
            return page(request, "queue.html", nav="queue", items=items, tabs=tabs, status=status, kind=kind, q=q,
                        sort=sort, order=order, descending=descending, kinds=present.KIND_LABELS, run=present.run_view(run) if run else None,
                        banner=banner(rows, names, last, outcome))

    @app.get("/proposals/{key}", response_class=HTMLResponse)
    def proposal(request: Request, key: str, last: str = "", outcome: str = ""):
        with lock:
            rows, names = everything()
            p = next((r for r in rows if r["key"] == key), None)
            if p is None:
                return not_found(request, key)
            decided = p["status"] not in store.UNDECIDED
            return page(request, "proposal.html", nav="queue", p=p, name=present.display_name(p),
                        rows=present.change_rows(p, names), side=present.side_by_side(p, names),
                        sig=present.signals(p), sop=present.sop(p), position=present.position(rows, key),
                        linked=present.linked_account(p), failure=present.failure(p),
                        mismatches=present.mismatches(p["result"]) if p["status"] == "applied" else [],
                        history=store.change_history(conn, proposal_key=key) if decided else [],
                        names=names, next_key=present.next_pending(rows, key), applying_hint=present.APPLYING_HINT,
                        banner=banner(rows, names, last, outcome))

    @app.post("/proposals/{key}/approve")
    def approve(request: Request, key: str, comment: str = Form(""), confirmed: str = Form(""),
                csrf_token: str = Form("")):
        check_post(request, csrf_token)
        with lock:
            if store.get(conn, key) is None:
                return not_found(request, key)
            outcome = review.approve(conn, client, key, comment, confirmed=confirmed == "yes")
            if outcome.kind == review.NEEDS_CONFIRMATION:
                rows, names = everything()
                p = next(r for r in rows if r["key"] == key)
                return page(request, "confirm.html", nav="queue", p=p, name=present.display_name(p),
                            rows=present.change_rows(p, names), comment=comment)
            return after_decision(key, outcome.kind)

    @app.post("/proposals/{key}/reject")
    def reject(request: Request, key: str, comment: str = Form(""), csrf_token: str = Form("")):
        check_post(request, csrf_token)
        with lock:
            if store.get(conn, key) is None:
                return not_found(request, key)
            return after_decision(key, review.reject(conn, key, comment).kind)

    @app.get("/history", response_class=HTMLResponse)
    def history(request: Request, q: str = ""):
        with lock:
            _, names = everything()
            needle = q.strip().casefold()
            entries = []
            for h in reversed(store.change_history(conn)):
                name = names.get(h["account_id"], h["account_id"])
                if needle and needle not in name.casefold() and needle not in h["account_id"].casefold():
                    continue
                entries.append(dict(h) | {"account_name": name})
            return page(request, "history.html", nav="history", entries=entries, q=q, names=names)

    @app.get("/runs", response_class=HTMLResponse)
    def runs(request: Request):
        with lock:
            return page(request, "runs.html", nav="runs", runs=[present.run_view(r) for r in store.list_runs(conn)])

    return app


def build() -> FastAPI:
    """The app for uvicorn, from the settings in .env: the database, the sandbox and its CRM browser."""
    conn = store.connect(config.DB_PATH)
    return create_app(conn, CrmClient(), f"{config.SITE_BASE}/crm/{config.api_token()}")
