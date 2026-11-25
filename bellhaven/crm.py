"""Thin client for the CRM sandbox API. Only bellhaven/apply.py may call create_account / update_account."""
import requests

from .config import API_BASE, api_token


class CrmError(RuntimeError):
    pass


class CrmClient:
    def __init__(self, token: str | None = None, base: str = API_BASE, session=None):
        self.base = base
        self.s = session or requests.Session()
        self.s.headers["Authorization"] = f"Bearer {token or api_token()}"

    def _req(self, method: str, path: str, **kwargs):
        r = self.s.request(method, f"{self.base}{path}", timeout=30, **kwargs)
        if r.status_code >= 400:
            raise CrmError(f"{method} {path} -> {r.status_code}: {r.text[:500]}")
        return r.json()

    def _list(self, path: str, **filters) -> list[dict]:
        out, page = [], 1
        while True:
            body = self._req("GET", path, params={**filters, "page": page, "page_size": 50})
            out += body["data"]
            if not body["data"] or len(out) >= body["total"]:
                return out
            page += 1

    def list_accounts(self, **filters) -> list[dict]:
        return self._list("/accounts", **filters)

    def list_contacts(self, **filters) -> list[dict]:
        return self._list("/contacts", **filters)

    def get_account(self, account_id: str) -> dict:
        return self._req("GET", f"/accounts/{account_id}")

    def create_account(self, body: dict) -> dict:
        return self._req("POST", "/accounts", json=body)

    def update_account(self, account_id: str, body: dict) -> dict:
        return self._req("PATCH", f"/accounts/{account_id}", json=body)
