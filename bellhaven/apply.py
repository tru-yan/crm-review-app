"""Write one approved proposal to the CRM. This is the only code that calls CRM write methods."""
import json

from . import store


class StaleProposal(Exception):
    """The CRM record changed after the proposal was made. Re-run the pipeline instead of writing."""


def _fetch_fresh(client, account_id: str, before: dict) -> dict:
    """Re-read the account just before writing. Its values are also the 'old' side of the change log."""
    current = client.get_account(account_id)
    drift = {f: {"expected": v, "actual": current.get(f)} for f, v in before.items() if current.get(f) != v}
    if drift:
        raise StaleProposal(drift)
    return current


def _mismatches(sent: dict, returned: dict) -> dict:
    return {k: {"sent": v, "returned": returned.get(k)} for k, v in sent.items() if returned.get(k) != v}


def _create_once(conn, client, key: str, payload: dict) -> dict:
    """Reuse an account already created for this community (for example after a timeout) instead of a second POST."""
    for a in client.list_accounts(zip=payload["billing_zip"]):
        if a.get("created_by_candidate") and a["name"] == payload["name"] and a["parent_id"] == payload["parent_id"]:
            return a
    created = client.create_account(payload)
    call_id = store.log_call(conn, key, "POST", "/accounts", payload, created)
    store.log_changes(conn, call_id, key, created["account_id"], "create", payload, {}, created)
    return created


def _patch(conn, client, key: str, current: dict, body: dict) -> dict:
    account_id = current["account_id"]
    updated = client.update_account(account_id, body)
    call_id = store.log_call(conn, key, "PATCH", f"/accounts/{account_id}", body, updated)
    store.log_changes(conn, call_id, key, account_id, "update", body, current, updated)
    return updated


def apply_proposal(conn, client, row) -> dict:
    kind, key, account_id = row["kind"], row["key"], row["account_id"]
    changes, before = json.loads(row["changes"]), json.loads(row["before"])
    if kind == "create":
        created = _create_once(conn, client, key, changes)
        return {"created": created["account_id"], "mismatches": _mismatches(changes, created)}
    current = _fetch_fresh(client, account_id, before)
    if kind == "chow":
        new = _create_once(conn, client, key, changes["new_account"])
        link = {"chow_current_account": new["account_id"]}
        old = _patch(conn, client, key, current, link)
        return {"created": new["account_id"], "old_account": account_id, "mismatches": _mismatches(link, old)}
    updated = _patch(conn, client, key, current, changes)
    return {"updated": account_id, "mismatches": _mismatches(changes, updated)}
