"""Read-only check of the CRM's end state. Works against the mock or the live sandbox.

Run:  python -m bellhaven.audit --before data\\snapshot\\crm.json
Proposals you rejected on purpose also show up as "still proposed"; that is expected.
"""
import argparse
import datetime
import json
from collections import Counter
from pathlib import Path

from .classify import bellhaven_parent_id, build_proposals
from .config import API_BASE, NOT_ON_WEBSITE_STATUS
from .crm import CrmClient
from .scrape import scrape
from .sop import must_preserve_old_account

# Fields this tool may change on an existing account. A change to anything else (phone, billing...) is a problem.
MAY_CHANGE = {"name", "billing_street", "billing_city", "billing_state", "billing_zip", "care_type", "status", "note",
              "parent_id", "parent_name", "duplicate_of_account", "chow_current_account", "updated_at"}


def _changed(old: dict, new: dict) -> set:
    return {k for k in set(old) | set(new) if k != "updated_at" and old.get(k) != new.get(k)}


def _check_account(old: dict, current: dict) -> list[str]:
    problems, changed = [], _changed(old, current)
    if must_preserve_old_account(old):
        if current.get("parent_id") != old.get("parent_id"):
            problems.append(f"billing SOP broken: '{old['name']}' has revenue and AR but its parent changed")
        elif current.get("chow_current_account") and changed - {"chow_current_account"}:
            edited = ", ".join(sorted(changed - {"chow_current_account"}))
            problems.append(f"billing SOP broken: '{old['name']}' was kept for billing after a change of ownership "
                            f"but was also edited: {edited}")
    unexpected = changed - MAY_CHANGE
    if unexpected:
        problems.append(f"'{old['name']}': fields this tool never changes were changed: {', '.join(sorted(unexpected))}")
    if (old.get("status") == "Active" and current.get("status") not in ("Active", NOT_ON_WEBSITE_STATUS)
            and not current.get("duplicate_of_account")):
        problems.append(f"'{old['name']}': set to {current.get('status')} without being marked a duplicate")
    return problems


def _check_links(a: dict, now: dict, bh: str) -> list[str]:
    problems = []
    dup, chow = a.get("duplicate_of_account"), a.get("chow_current_account")
    if dup:
        target = now.get(dup)
        if a.get("status") != "Inactive":
            problems.append(f"'{a['name']}': marked as a duplicate but status is {a['status']}")
        if target is None:
            problems.append(f"'{a['name']}': duplicate_of_account points to a missing account {dup}")
        elif dup == a["account_id"] or target.get("duplicate_of_account") or target.get("status") != "Active":
            problems.append(f"'{a['name']}': duplicate_of_account points to {dup}, which isn't a live account")
    if chow:
        target = now.get(chow)
        if target is None:
            problems.append(f"'{a['name']}': chow_current_account points to a missing account {chow}")
        elif (chow == a["account_id"] or target.get("duplicate_of_account") or target.get("status") != "Active"
              or target.get("parent_id") != bh):
            problems.append(f"'{a['name']}': chow_current_account points to {chow}, which isn't a live "
                            "Bellhaven account")
    return problems


def audit(before: dict, accounts: list[dict], contacts: list[dict], locations, today: str) -> list[str]:
    """Return a list of problems. Empty means the CRM matches the website and follows the rules."""
    now = {a["account_id"]: a for a in accounts}
    old_ids = {a["account_id"] for a in before["accounts"]}
    if old_ids and not old_ids & set(now):
        return ["the before-snapshot shares no accounts with this CRM: wrong snapshot, or wrong CRM?"]
    try:
        bh = bellhaven_parent_id(accounts)
        proposals = build_proposals(locations, accounts, contacts, today)
    except ValueError as e:
        return [f"cannot check the proposals: {e}"]

    problems = [f"still proposed: {p.kind} | {p.title}" for p in proposals]
    for old in before["accounts"]:
        current = now.get(old["account_id"])
        if current is None:
            problems.append(f"account disappeared: {old['account_id']} {old['name']}")
        else:
            problems += _check_account(old, current)
    for a in accounts:
        problems += _check_links(a, now, bh)
    created = Counter((a.get("name"), a.get("billing_street")) for a in accounts if a.get("created_by_candidate"))
    problems += [f"created more than once: '{name}' at {street} ({n} accounts)"
                 for (name, street), n in created.items() if n > 1]
    return problems


def changes_since(before: dict, accounts: list[dict]) -> dict:
    old = {a["account_id"]: a for a in before["accounts"]}
    return {"created": sum(a["account_id"] not in old for a in accounts),
            "changed": sum(a["account_id"] in old and bool(_changed(old[a["account_id"]], a)) for a in accounts)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", required=True, help="snapshot made by python -m mock_crm.snapshot")
    args = parser.parse_args(argv)
    before = json.loads(Path(args.before).read_text(encoding="utf-8"))
    client = CrmClient()  # only list_accounts and list_contacts are called: GET requests
    accounts, contacts = client.list_accounts(), client.list_contacts()
    problems = audit(before, accounts, contacts, scrape(), datetime.date.today().isoformat())
    print(f"CRM: {API_BASE}")
    print(json.dumps(changes_since(before, accounts)))
    for problem in problems:
        print("PROBLEM:", problem)
    print("OK" if not problems else
          f"{len(problems)} problem(s). Proposals you rejected on purpose show as 'still proposed'.")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
