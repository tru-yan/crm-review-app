"""Local stand-in for the CRM sandbox API, so the whole flow can be tested without touching the graded sandbox.

Run:  $env:BH_MOCK_SEED = "data\\snapshot\\crm.json"; uvicorn mock_crm.server:build --factory --port 8001
State is kept in memory: restart the server to reset it. It is deliberately stricter than the real API, so bugs
show up here rather than on the graded copy. Status codes and the error shape ({"detail": ...}) are guesses;
nothing in the pipeline depends on them.
"""
import copy
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request

VALID_STATUSES = {"Active", "Inactive", "Needs Review"}
TEXT_FIELDS = ("name", "parent_id", "parent_name", "billing_street", "billing_city", "billing_state",
               "billing_zip", "care_type", "phone", "chow_current_account", "duplicate_of_account", "note")
WRITABLE = {"name", "parent_id", "billing_street", "billing_city", "billing_state", "billing_zip", "care_type",
            "status", "phone", "chow_current_account", "duplicate_of_account", "note"}
REFERENCES = ("parent_id", "chow_current_account", "duplicate_of_account")
ACCOUNT_FILTERS = {"q", "city", "state", "zip", "street", "parent_id", "page", "page_size"}
CONTACT_FILTERS = {"account_id", "q", "page", "page_size"}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")


def _paginate(items: list, page: int, page_size: int) -> dict:
    page, page_size = max(page, 1), max(1, min(page_size, 50))
    start = (page - 1) * page_size
    return {"data": items[start:start + page_size], "page": page, "page_size": page_size, "total": len(items)}


def _contains(value, wanted: str) -> bool:
    return wanted.lower() in str(value or "").lower()


def _only(request: Request, allowed: set) -> None:
    unknown = sorted(set(request.query_params) - allowed)
    if unknown:
        raise HTTPException(422, f"Unknown query parameters: {unknown}")


async def _json_body(request: Request):
    try:
        return await request.json()
    except ValueError:
        raise HTTPException(422, "Body is not valid JSON") from None


def create_app(seed: dict) -> FastAPI:
    app = FastAPI(title="Mock Bellhaven CRM (not graded)")
    accounts = {a["account_id"]: copy.deepcopy(a) for a in seed["accounts"]}
    contacts = [copy.deepcopy(c) for c in seed.get("contacts", [])]
    created = {"count": 0}

    def require_token(authorization: str | None) -> None:
        if not authorization or not authorization.startswith("Bearer ") or not authorization[7:].strip():
            raise HTTPException(401, "Missing or invalid token")

    def validate(body, creating: bool, account_id: str | None = None) -> None:
        if not isinstance(body, dict):
            raise HTTPException(422, "Body must be a JSON object")
        if not creating and not body:
            raise HTTPException(422, "Body is empty: nothing to update")
        not_writable = sorted(set(body) - WRITABLE)
        if not_writable:
            raise HTTPException(422, f"Fields not writable: {not_writable}")
        wrong_type = sorted(k for k, v in body.items() if not isinstance(v, str))
        if wrong_type:
            raise HTTPException(422, f"Fields must be strings: {wrong_type}")
        if (creating or "name" in body) and not body.get("name", "").strip():
            raise HTTPException(422, "name is required and cannot be blank")
        if "status" in body and body["status"] not in VALID_STATUSES:
            raise HTTPException(422, f"status must be one of {sorted(VALID_STATUSES)}")
        for ref in REFERENCES:
            if body.get(ref) and body[ref] == account_id:
                raise HTTPException(422, f"{ref} cannot point to the account itself")
            if body.get(ref) and body[ref] not in accounts:
                raise HTTPException(422, f"{ref} {body[ref]!r} does not exist")

    def set_parent_name(account: dict) -> None:
        parent = accounts.get(account["parent_id"]) if account.get("parent_id") else None
        account["parent_name"] = parent["name"] if parent else ""

    @app.get("/api/v1/me")
    def me(authorization: str | None = Header(None)):
        require_token(authorization)
        return {"candidate": "mock", "message": "Mock CRM. Nothing here is graded."}

    @app.get("/api/v1/accounts")
    def list_accounts(request: Request, q: str = "", city: str = "", state: str = "", zip: str = "",
                      street: str = "", parent_id: str = "", page: int = 1, page_size: int = 50,
                      authorization: str | None = Header(None)):
        require_token(authorization)
        _only(request, ACCOUNT_FILTERS)
        items = [a for a in list(accounts.values())
                 if _contains(a.get("name"), q) and _contains(a.get("billing_city"), city)
                 and _contains(a.get("billing_state"), state) and _contains(a.get("billing_zip"), zip)
                 and _contains(a.get("billing_street"), street) and (not parent_id or a.get("parent_id") == parent_id)]
        return _paginate(items, page, page_size)

    @app.get("/api/v1/accounts/{account_id}")
    def get_account(account_id: str, authorization: str | None = Header(None)):
        require_token(authorization)
        if account_id not in accounts:
            raise HTTPException(404, "Account not found")
        return accounts[account_id]

    @app.post("/api/v1/accounts", status_code=201)
    async def create_account(request: Request, authorization: str | None = Header(None)):
        require_token(authorization)
        body = await _json_body(request)
        validate(body, creating=True)
        created["count"] += 1
        account = {f: "" for f in TEXT_FIELDS} | {"status": "Active", "lifetime_revenue": 0, "outstanding_ar": 0} | body
        account |= {"account_id": f"001MOCK{created['count']:011d}", "created_by_candidate": True,
                    "updated_at": _now()}
        set_parent_name(account)
        accounts[account["account_id"]] = account
        return account

    @app.patch("/api/v1/accounts/{account_id}")
    async def update_account(account_id: str, request: Request, authorization: str | None = Header(None)):
        require_token(authorization)
        if account_id not in accounts:
            raise HTTPException(404, "Account not found")
        body = await _json_body(request)
        validate(body, creating=False, account_id=account_id)
        account = accounts[account_id]
        account.update(body)
        account["updated_at"] = _now()
        set_parent_name(account)
        return account

    @app.get("/api/v1/contacts")
    def list_contacts(request: Request, account_id: str = "", q: str = "", page: int = 1, page_size: int = 50,
                      authorization: str | None = Header(None)):
        require_token(authorization)
        _only(request, CONTACT_FILTERS)
        items = [c for c in list(contacts)
                 if (not account_id or c.get("account_id") == account_id) and _contains(c.get("name"), q)]
        return _paginate(items, page, page_size)

    return app


def build() -> FastAPI:
    """Factory for uvicorn: fills the mock from the snapshot file named in BH_MOCK_SEED."""
    path = os.environ.get("BH_MOCK_SEED")
    if not path:
        raise RuntimeError("Set BH_MOCK_SEED to a snapshot made by: python -m mock_crm.snapshot")
    return create_app(json.loads(Path(path).read_text(encoding="utf-8")))
