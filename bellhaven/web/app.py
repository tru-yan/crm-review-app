"""The review app: a queue of proposals, one page per proposal, the change history and the pipeline runs.

Only the two POST routes can change anything, and only through review.approve and review.reject.
"""
import secrets
import threading
from pathlib import Path
from urllib.parse import urlencode, urlsplit

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader

from .. import config, store
from ..crm import CrmClient
from . import present, review

HERE = Path(__file__).parent
DEFAULT_ORIGINS = ("http://127.0.0.1:8000", "http://localhost:8000")
TABS = ("pending", "applied", "rejected", "failed", "superseded", "all")


def create_app(conn, client, live: bool, meridian_base: str | None, allowed_origins=None) -> FastAPI:
    """live: the CRM is the graded sandbox, so every approval asks for confirmation first.
    meridian_base: the CRM browser address up to /accounts (it holds the token), or None for no links."""
    app = FastAPI(title="Bellhaven review", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.csrf_token = secrets.token_urlsafe(32)
    origins = set(allowed_origins or DEFAULT_ORIGINS)
    lock = threading.Lock()  # requests run in worker threads; they take turns on the one SQLite connection
    templates = Jinja2Templates(env=Environment(loader=FileSystemLoader(HERE / "templates"), autoescape=True))
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    meridian = meridian_base.rstrip("/") if live and meridian_base else None

    def meridian_url(account_id) -> str | None:
        return f"{meridian}/accounts/{account_id}" if meridian and account_id else None

    templates.env.globals.update(meridian_url=meridian_url, kind_label=present.kind_label,
                                 field_label=present.field_label, when=present.when, show=present.show)

    def page(request: Request, template: str, status_code: int = 200, /, **context):
        context |= {"live": live, "meridian_home": f"{meridian}/accounts" if meridian else None,
                    "pending": store.counts(conn).get("pending", 0), "csrf_token": app.state.csrf_token}
        return templates.TemplateResponse(request, template, context, status_code=status_code)

    def everything():
        rows = [present.parse(r) for r in store.list_proposals(conn)]
        return rows, present.account_names(rows)

    def banner(rows, names, last: str, outcome: str):
        p = next((r for r in rows if r["key"] == last), None)
        return present.banner(p, outcome, names) if p else None

    def not_found(request: Request, key: str):
        return page(request, "notfound.html", 404, key=key, nav="queue")

    def check_post(request: Request, token: str) -> None:
        sent = request.headers.get("origin")
        if not sent and request.headers.get("referer"):
            parts = urlsplit(request.headers["referer"])
            sent = f"{parts.scheme}://{parts.netloc}"
        if sent and sent not in origins:
            raise HTTPException(403, "This request came from another site, so it was refused.")
        if not secrets.compare_digest(token.encode(), app.state.csrf_token.encode()):
            raise HTTPException(403, "This form is out of date. Reload the page and try again.")

    def after_decision(key: str, outcome: str) -> RedirectResponse:
        rows, _ = everything()
        target = present.next_pending(rows, key)
        path = f"/proposals/{target}" if target else "/queue"
        return RedirectResponse(f"{path}?{urlencode({'last': key, 'outcome': outcome})}", status_code=303)

    @app.get("/")
    def home():
        return RedirectResponse("/queue", status_code=303)

    @app.get("/queue", response_class=HTMLResponse)
    def queue(request: Request, status: str = "pending", kind: str = "", q: str = "", last: str = "",
              outcome: str = ""):
        status = status if status in TABS else "pending"
        with lock:
            rows, names = everything()
            counts = store.counts(conn)
            run = store.latest_run(conn)
            shown = [p for p in rows if (status == "all" or p["status"] == status)
                     and (not kind or p["kind"] == kind) and present.matches(p, q)]
            items = [{"p": p, "name": present.display_name(p), "place": present.place(p),
                      "summary": present.summary(p, names)} for p in present.queue_order(shown)]
            tabs = [(t, t.capitalize(), sum(counts.values()) if t == "all" else counts.get(t, 0)) for t in TABS]
            return page(request, "queue.html", nav="queue", items=items, tabs=tabs, status=status, kind=kind, q=q,
                        kinds=present.KIND_LABELS, run=present.run_view(run) if run else None,
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
            outcome = review.approve(conn, client, key, comment, live=live, confirmed=confirmed == "yes")
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
    """The app for uvicorn, from the settings in .env: the database, and the CRM it is tied to."""
    live = not config.USING_MOCK
    conn = store.connect(config.DB_PATH, crm_base=config.API_BASE)
    meridian = f"{config.SITE_BASE}/crm/{config.api_token()}" if live else None
    return create_app(conn, CrmClient(base=config.API_BASE), live, meridian)
