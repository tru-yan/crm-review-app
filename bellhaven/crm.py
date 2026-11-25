"""Thin client for the CRM sandbox API. Only bellhaven/apply.py may call create_account / update_account."""
import requests

from .config import API_BASE, api_token


class CrmError(RuntimeError):
    pass


PAGE_SIZE = 50


class CrmClient:
    def __init__(self, token: str | None = None, base: str = API_BASE, session=None):
        self.base = base.rstrip("/")
        self.s = session or requests.Session()
        self.s.headers["Authorization"] = f"Bearer {token or api_token()}"

    def _req(self, method: str, path: str, **kwargs):
        try:
            r = self.s.request(method, f"{self.base}{path}", timeout=30, **kwargs)
        except requests.RequestException as e:
            raise CrmError(f"{method} {path} failed: {type(e).__name__}: {e}") from e
        if r.status_code >= 400:
            raise CrmError(f"{method} {path} -> {r.status_code}: {r.text[:500]}")
        try:
            return r.json()
        except ValueError:
            raise CrmError(f"{method} {path} -> {r.status_code}: response is not JSON") from None

    def _list(self, path: str, **filters) -> list[dict]:
        out, page, total = [], 1, 0
        while True:
            body = self._req("GET", path, params={**filters, "page": page, "page_size": PAGE_SIZE})
            if not isinstance(body, dict) or not isinstance(body.get("data"), list) or "total" not in body:
                raise CrmError(f"GET {path}: unexpected response shape")
            out += body["data"]
            total = body["total"]
            if not body["data"] or len(out) >= total:
                break
            if page > total // PAGE_SIZE + 1:
                raise CrmError(f"GET {path}: still paging after {page} pages for {total} records")
            page += 1
        if len(out) != total:
            raise CrmError(f"GET {path}: read {len(out)} records but the API reported {total}; "
                           "the data may have changed while reading, so retry")
        return out

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
