"""Write one approved proposal to the CRM. This is the only code that calls CRM write methods.

Safety, in order:
- claim the proposal, which works only for pending or failed ones, so two clicks can't both write
- re-read the account, and refuse if it changed or the business rule no longer holds
- create each account at most once
- log every attempt, successful or not, field by field
"""
import json

from . import store
from .classify import already_resolved
from .crm import CrmError
from .sop import must_preserve_old_account

PATCH_KINDS = ("update", "duplicate", "not_on_website")
KINDS = PATCH_KINDS + ("create", "chow")


class StaleProposal(Exception):
    """The CRM record changed after the proposal was made. Re-run the pipeline instead of writing."""

    def __init__(self, drift: dict):
        self.drift = drift
        super().__init__("; ".join(f"{f} is now {d['actual']!r} (the proposal expected {d['expected']!r})"
                                   for f, d in drift.items()))


class AlreadyHandled(Exception):
    """The proposal isn't pending or failed: it was decided already, or another click is applying it."""


def _mismatches(sent: dict, returned: dict) -> dict:
    return {k: {"sent": v, "returned": returned.get(k)} for k, v in sent.items() if returned.get(k) != v}


def _call(conn, key: str, method: str, path: str, body: dict, send):
    """Run one API write and log it, failures included: a write that timed out may still have landed."""
    try:
        response = send()
    except Exception as e:
        store.log_call(conn, key, method, path, body, {"error": f"{type(e).__name__}: {e}"})
        raise
    return response, store.log_call(conn, key, method, path, body, response)


def _read(client, account_id: str) -> dict:
    current = client.get_account(account_id)
    if current.get("account_id") != account_id:
        raise CrmError(f"GET /accounts/{account_id} returned a different record ({current.get('account_id')!r})")
    return current


def _check_rules(kind: str, current: dict) -> None:
    """Re-check the decision against the account as it is now, not only the fields the write changes."""
    if kind == "chow" and not must_preserve_old_account(current):
        raise StaleProposal({"billing": {
            "expected": "revenue and outstanding AR both above 0",
            "actual": f"revenue {current.get('lifetime_revenue')}, AR {current.get('outstanding_ar')}"}})
    if already_resolved(current):
        raise StaleProposal({"resolution": {
            "expected": "not a duplicate and no change of ownership",
            "actual": {f: current.get(f) for f in ("duplicate_of_account", "chow_current_account")}}})


def _check_survivor(client, survivor_id: str) -> None:
    """A duplicate may only point at a live account: Active, and not itself a duplicate or a change of ownership."""
    survivor = _read(client, survivor_id)
    if survivor.get("status") != "Active" or already_resolved(survivor):
        raise StaleProposal({"survivor": {
            "expected": f"{survivor_id} Active, not a duplicate and no change of ownership",
            "actual": {f: survivor.get(f) for f in ("status", "duplicate_of_account", "chow_current_account")}}})


def _find_created(conn, client, key: str, payload: dict) -> dict | None:
    """The account this proposal already created, if any: from our own API log first, then a strict search."""
    for r in conn.execute("SELECT response FROM api_log WHERE proposal_key=? AND method='POST' ORDER BY id", (key,)):
        account_id = json.loads(r["response"]).get("account_id")
        if account_id:
            return _read(client, account_id)
    same = [a for a in client.list_accounts(zip=payload["billing_zip"])
            if a.get("created_by_candidate") and a.get("name") == payload["name"]
            and a.get("billing_street") == payload["billing_street"]]
    if len(same) > 1:
        raise CrmError(f"{len(same)} accounts named {payload['name']!r} at {payload['billing_street']!r} were created "
                       "by this tool already; resolve the extra copies by hand before retrying")
    return same[0] if same else None


def _create_once(conn, client, key: str, payload: dict, run_id: int) -> dict:
    found = _find_created(conn, client, key, payload)
    if found:
        if found.get("duplicate_of_account") or found.get("status") != "Active":
            raise CrmError(f"account {found['account_id']} was already created for this, but has since been marked "
                           "inactive or a duplicate; resolve it by hand before retrying")
        if not conn.execute("SELECT 1 FROM change_log WHERE account_id=? AND action='create'",
                            (found["account_id"],)).fetchone():
            # created earlier by a POST whose response never arrived: record it now
            call_id = store.log_call(conn, key, "POST (recovered)", "/accounts", payload, found)
            store.log_changes(conn, call_id, key, found["account_id"], "create", payload, {}, found)
        return found
    latest = store.latest_finished_run(conn)
    if latest is not None and run_id < latest:
        # kept only because an earlier attempt was logged; the website may have changed since, so never POST it
        raise StaleProposal({"proposal": {"expected": f"produced by the latest pipeline run ({latest})",
                                          "actual": f"last produced by run {run_id}; re-run the pipeline"}})
    created, call_id = _call(conn, key, "POST", "/accounts", payload, lambda: client.create_account(payload))
    if not created.get("account_id"):
        raise CrmError(f"POST /accounts returned no account_id: {str(created)[:300]}")
    store.log_changes(conn, call_id, key, created["account_id"], "create", payload, {}, created)
    return created


def _patch(conn, client, key: str, current: dict, body: dict) -> dict:
    account_id = current["account_id"]
    updated, call_id = _call(conn, key, "PATCH", f"/accounts/{account_id}", body,
                             lambda: client.update_account(account_id, body))
    store.log_changes(conn, call_id, key, account_id, "update", body, current, updated)
    return updated


def apply_proposal(conn, client, row) -> dict:
    kind, key, account_id = row["kind"], row["key"], row["account_id"]
    if kind not in KINDS:
        raise ValueError(f"unknown proposal kind {kind!r}")
    if not store.claim(conn, key):
        status = (store.get(conn, key) or {"status": "missing"})["status"]
        hint = (": wait for it to finish; if it stays stuck, re-run the pipeline 10 or more minutes from now"
                if status == "applying" else "")
        raise AlreadyHandled(f"'{row['title']}' is {status}, not pending or failed{hint}")
    row = store.get(conn, key)  # the latest stored version: a pipeline run may have refreshed it since
    changes, before = json.loads(row["changes"]), json.loads(row["before"])

    if kind == "create":
        created = _create_once(conn, client, key, changes, row["run_id"])
        return {"created": created["account_id"], "mismatches": _mismatches(changes, created)}

    current = _read(client, account_id)
    if kind in PATCH_KINDS and all(current.get(f) == v for f, v in changes.items()):
        # the write landed earlier but its response was lost (for example a timeout): record it, don't write again
        call_id = store.log_call(conn, key, "PATCH (confirmed)", f"/accounts/{account_id}", changes, current)
        store.log_changes(conn, call_id, key, account_id, "update", changes, before, current)
        return {"updated": account_id, "already_applied": True, "mismatches": {}}
    drift = {f: {"expected": v, "actual": current.get(f)} for f, v in before.items() if current.get(f) != v}
    if drift:
        raise StaleProposal(drift)
    _check_rules(kind, current)
    if kind == "duplicate":
        _check_survivor(client, changes["duplicate_of_account"])

    if kind == "chow":
        new = _create_once(conn, client, key, changes["new_account"], row["run_id"])
        link = {"chow_current_account": new["account_id"]}
        try:
            old = _patch(conn, client, key, current, link)
        except CrmError as e:
            raise CrmError(f"created {new['account_id']} but could not link the old account to it ({e}). "
                           "Retry it now, before the next pipeline run: the new account will be reused, "
                           "not created again.") from e
        return {"created": new["account_id"], "old_account": account_id,
                "mismatches": {"new_account": _mismatches(changes["new_account"], new),
                               "link": _mismatches(link, old)}}

    updated = _patch(conn, client, key, current, changes)
    return {"updated": account_id, "mismatches": _mismatches(changes, updated)}
