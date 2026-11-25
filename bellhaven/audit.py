"""Read-only check of the CRM's end state. Works against the mock or the live sandbox.

Run:  python -m bellhaven.audit --before data\snapshot\crm.json
"""
import argparse
import datetime
import json
from pathlib import Path

from .classify import build_proposals
from .config import API_BASE
from .crm import CrmClient
from .scrape import scrape
from .sop import must_preserve_old_account


def audit(before: dict, accounts: list[dict], contacts: list[dict], locations, today: str) -> list[str]:
    """Return a list of problems. Empty means the CRM matches the website and follows the rules."""
    problems = [f"still proposed: {p.kind} | {p.title}" for p in build_proposals(locations, accounts, contacts, today)]
    now = {a["account_id"]: a for a in accounts}
    for old in before["accounts"]:
        current = now.get(old["account_id"])
        if current is None:
            problems.append(f"account disappeared: {old['account_id']} {old['name']}")
        elif must_preserve_old_account(old) and current["parent_id"] != old["parent_id"]:
            problems.append(f"billing SOP broken: '{old['name']}' has revenue and AR but its parent changed")
    for a in accounts:
        for ref in ("chow_current_account", "duplicate_of_account"):
            if a.get(ref) and a[ref] not in now:
                problems.append(f"'{a['name']}': {ref} points to a missing account {a[ref]}")
        if a.get("duplicate_of_account") and a["status"] != "Inactive":
            problems.append(f"'{a['name']}': marked as a duplicate but status is {a['status']}")
    return problems


def changes_since(before: dict, accounts: list[dict]) -> dict:
    old = {a["account_id"]: a for a in before["accounts"]}

    def strip(a):
        return {k: v for k, v in a.items() if k != "updated_at"}

    return {"created": sum(a["account_id"] not in old for a in accounts),
            "changed": sum(a["account_id"] in old and strip(a) != strip(old[a["account_id"]]) for a in accounts)}


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", required=True, help="snapshot made by python -m mock_crm.snapshot")
    args = parser.parse_args(argv)
    before = json.loads(Path(args.before).read_text(encoding="utf-8"))
    client = CrmClient()
    accounts, contacts = client.list_accounts(), client.list_contacts()
    problems = audit(before, accounts, contacts, scrape(), datetime.date.today().isoformat())
    print(f"CRM: {API_BASE}")
    print(json.dumps(changes_since(before, accounts)))
    for problem in problems:
        print("PROBLEM:", problem)
    print("OK" if not problems else f"{len(problems)} problem(s)")
    raise SystemExit(1 if problems else 0)


if __name__ == "__main__":
    main()
