"""Rehearsal only: approve every pending proposal against the MOCK CRM. Refuses to run against the live sandbox."""
from bellhaven import store
from bellhaven.apply import apply_proposal
from bellhaven.config import API_BASE, DB_PATH, USING_MOCK
from bellhaven.crm import CrmClient


def main() -> None:
    if not USING_MOCK:
        raise SystemExit("Refusing: BH_API_BASE is the live sandbox. This script is for the mock only.")
    conn, crm = store.connect(DB_PATH, crm_base=API_BASE), CrmClient()
    rows = store.list_proposals(conn, "pending")
    for row in rows:
        store.set_status(conn, row["key"], "applied", "rehearsal", apply_proposal(conn, crm, row))
    print(f"Applied {len(rows)} proposals to the mock at {API_BASE}")


if __name__ == "__main__":
    main()
