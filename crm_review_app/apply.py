"""Write one approved proposal to the CRM. This is the only code that calls CRM write methods.

Safety, in order:
- claim the proposal, which works only for pending or failed ones, so two clicks can't both write
- re-read the account (and its contacts), and refuse if it changed or the business rule no longer holds
- create each account and each contact at most once
- log every attempt, successful or not, field by field
"""
import json

from . import store
from .classify import ADMINISTRATOR, already_resolved
from .crm import CrmError
from .normalize import same_text
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


def _check_live(current: dict, status_after: str | None = None) -> None:
    """Contacts change only on a live account: Active (or made Active by the same update), and not resolved."""
    if (status_after or current.get("status")) != "Active" or already_resolved(current):
        raise StaleProposal({"account": {
            "expected": "Active, not a duplicate and no change of ownership",
            "actual": {f: current.get(f) for f in ("status", "duplicate_of_account", "chow_current_account")}}})


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
    _stale_run(conn, run_id)  # never POST a create the latest pipeline run no longer proposes
    created, call_id = _call(conn, key, "POST", "/accounts", payload, lambda: client.create_account(payload))
    if not created.get("account_id"):
        raise CrmError(f"POST /accounts returned no account_id: {str(created)[:300]}")
    store.log_changes(conn, call_id, key, created["account_id"], "create", payload, {}, created)
    return created


def _stale_run(conn, run_id: int) -> None:
    """Refuse to add a record the latest finished pipeline run no longer proposes (the website may have changed)."""
    latest = store.latest_finished_run(conn)
    if latest is not None and run_id < latest:
        raise StaleProposal({"proposal": {"expected": f"produced by the latest pipeline run ({latest})",
                                          "actual": f"last produced by run {run_id}; re-run the pipeline"}})


def _find_contact(conn, key: str, contacts: dict, want: dict) -> dict | None:
    """The contact this proposal already added, if any: from our own API log first, then an active contact with
    the same name on the account (added by a POST whose response was lost, or by someone else)."""
    for r in conn.execute("SELECT response FROM api_log WHERE proposal_key=? AND path='/contacts' AND method='POST'"
                          " ORDER BY id", (key,)):
        contact_id = json.loads(r["response"]).get("contact_id")
        if contact_id:
            if contact_id not in contacts:
                raise CrmError(f"contact {contact_id} was added for this earlier but is no longer on the account; "
                               "resolve it by hand before retrying")
            return contacts[contact_id]
    same = [c for c in contacts.values() if c.get("is_active", True) and same_text(c.get("name"), want["name"])]
    return same[0] if same else None


def _read_contacts(client, account_id: str, change: dict) -> dict:
    """The account's contacts by id. Refuses if a contact the change would make inactive is gone or renamed."""
    contacts = {c["contact_id"]: c for c in client.list_contacts(account_id=account_id)}
    drift = {}
    for old in change["deactivate"]:
        found = contacts.get(old["contact_id"])
        if found is None or not same_text(found.get("name"), old["name"]):
            drift[f"contact {old['contact_id']}"] = {"expected": old["name"], "actual": found and found.get("name")}
    if drift:
        raise StaleProposal(drift)
    return contacts


def _apply_administrator(conn, client, row, changes: dict) -> dict:
    """Add the website's administrator as a contact, then make the old Administrator contacts inactive.

    Adding comes first, so the account is never left without an administrator. A retry after a failure reuses the
    contact already added, and skips contacts already made inactive.
    """
    key, account_id = row["key"], row["account_id"]
    _check_live(_read(client, account_id))
    contacts = _read_contacts(client, account_id, changes)
    want = changes["add"]

    wrote, mismatches = False, {}
    new = _find_contact(conn, key, contacts, want)
    added = conn.execute("SELECT 1 FROM change_log WHERE proposal_key=? AND field='administrator_added'",
                         (key,)).fetchone()
    tried = conn.execute("SELECT 1 FROM api_log WHERE proposal_key=? AND path='/contacts' AND method='POST'",
                         (key,)).fetchone()
    if new is not None and tried and not added:
        # our earlier POST landed but its response was lost: record it now
        call_id = store.log_call(conn, key, "POST (recovered)", "/contacts", want, new)
        store.log_changes(conn, call_id, key, account_id, "create", ["administrator_added"], {},
                          {"administrator_added": new.get("name")})
    if new is None:
        _stale_run(conn, row["run_id"])
        new, call_id = _call(conn, key, "POST", "/contacts", want, lambda: client.create_contact(want))
        if not new.get("contact_id"):
            raise CrmError(f"POST /contacts returned no contact_id: {str(new)[:300]}")
        store.log_changes(conn, call_id, key, account_id, "create", ["administrator_added"], {},
                          {"administrator_added": new.get("name")})
        mismatches = {f"Administrator contact {k}": d for k, d in _mismatches(want, new).items()}
        wrote = True

    made_inactive = []
    for old in changes["deactivate"]:
        if not contacts[old["contact_id"]].get("is_active", True):
            continue  # made inactive by an earlier attempt
        body, contact_id = {"is_active": False}, old["contact_id"]
        try:
            updated, call_id = _call(conn, key, "PATCH", f"/contacts/{contact_id}", body,
                                     lambda: client.update_contact(contact_id, body))
        except CrmError as e:
            raise CrmError(f"added {want['name']} ({new['contact_id']}) but could not make {old['name']} inactive "
                           f"({e}). Retry it now: the new contact will be reused, not added again.") from e
        store.log_changes(conn, call_id, key, account_id, "update", ["administrator_made_inactive"],
                          {"administrator_made_inactive": old["name"]}, {"administrator_made_inactive": "inactive"})
        if updated.get("is_active") is not False:
            mismatches[f"{old['name']}'s contact active"] = {"sent": False, "returned": updated.get("is_active")}
        made_inactive.append(contact_id)
        wrote = True

    return {"contact": new["contact_id"], "made_inactive": made_inactive, "mismatches": mismatches,
            "already_applied": not wrote}  # not wrote: the CRM had this administrator, and no old one was active


def _patch(conn, client, key: str, current: dict, body: dict) -> dict:
    account_id = current["account_id"]
    updated, call_id = _call(conn, key, "PATCH", f"/accounts/{account_id}", body,
                             lambda: client.update_account(account_id, body))
    store.log_changes(conn, call_id, key, account_id, "update", body, current, updated)
    return updated


def apply_proposal(conn, client, row) -> dict:
    kind, key = row["kind"], row["key"]
    if kind not in KINDS:
        raise ValueError(f"unknown proposal kind {kind!r}")
    if not store.claim(conn, key):
        status = (store.get(conn, key) or {"status": "missing"})["status"]
        hint = (f": wait for it to finish; if it stays stuck, re-run the pipeline {store.STUCK_MINUTES} or more "
                "minutes from now" if status == "applying" else "")
        raise AlreadyHandled(f"'{row['title']}' is {status}, not pending or failed{hint}")
    row = store.get(conn, key)  # the latest stored version: a pipeline run may have refreshed it since
    changes, before = json.loads(row["changes"]), json.loads(row["before"])

    if kind == "create":
        created = _create_once(conn, client, key, changes, row["run_id"])
        return {"created": created["account_id"], "mismatches": _mismatches(changes, created)}
    contact_change = changes.pop(ADMINISTRATOR, None) if kind == "update" else None
    if contact_change is None:
        return _apply_account(conn, client, row, kind, changes, before)
    return _apply_update_with_administrator(conn, client, row, changes, before, contact_change)


def _apply_update_with_administrator(conn, client, row, fields: dict, before: dict, contact_change: dict) -> dict:
    """An update that also changes the Administrator contact: the account's fields first (they may make it Active
    again), then the contacts. Both are checked before anything is written, and a retry skips what already landed."""
    account_id = row["account_id"]
    # everything that would stop the contact change, checked before the account is written
    _check_live(_read(client, account_id), fields.get("status"))
    contacts = _read_contacts(client, account_id, contact_change)
    if _find_contact(conn, row["key"], contacts, contact_change["add"]) is None:
        _stale_run(conn, row["run_id"])  # it would add a contact: only if the latest run still proposes it
    account = (_apply_account(conn, client, row, "update", fields, before) if fields
               else {"updated": account_id, "mismatches": {}, "already_applied": True})
    try:
        contacts = _apply_administrator(conn, client, row, contact_change)
    except CrmError as e:
        if not account.get("already_applied"):
            raise CrmError(f"updated the account but not its Administrator contact ({e}). Retry it now: the account "
                           "change is in and won't be written again.") from e
        raise
    result = {"updated": account_id, "contact": contacts["contact"], "made_inactive": contacts["made_inactive"],
              "mismatches": account["mismatches"] | contacts["mismatches"]}
    if account.get("already_applied") and contacts["already_applied"]:
        result["already_applied"] = True
    return result


def _apply_account(conn, client, row, kind: str, changes: dict, before: dict) -> dict:
    """Write a PATCH kind's or a CHOW's account change."""
    key, account_id = row["key"], row["account_id"]
    current = _read(client, account_id)
    if kind in PATCH_KINDS and all(current.get(f) == v for f, v in changes.items()):
        # the write landed earlier: record it unless its response came back (a retry after the contacts failed)
        path = f"/accounts/{account_id}"
        last = conn.execute("SELECT response FROM api_log WHERE proposal_key=? AND path=? AND method LIKE 'PATCH%'"
                            " ORDER BY id DESC LIMIT 1", (key, path)).fetchone()
        if last is None or "error" in json.loads(last["response"]):
            call_id = store.log_call(conn, key, "PATCH (confirmed)", path, changes, current)
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
