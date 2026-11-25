"""Rehearsal only: approve every pending proposal against the MOCK CRM. Refuses to run against the live sandbox."""
from bellhaven import store
from bellhaven.config import API_BASE, DB_PATH, USING_MOCK
from bellhaven.crm import CrmClient
from bellhaven.web import review


def main() -> None:
    if not USING_MOCK:
        raise SystemExit("Refusing: BH_API_BASE is the live sandbox. This script is for the mock only.")
    conn, crm = store.connect(DB_PATH, crm_base=API_BASE), CrmClient()
    rows = store.list_proposals(conn, "pending")
    problems = []
    for row in rows:
        outcome = review.approve(conn, crm, row["key"], "rehearsal")  # the same path as the Approve button
        if outcome.kind not in (review.APPLIED, review.ALREADY_APPLIED):
            problems.append(f"{row['title']}: {outcome.kind} {outcome.message}")
    print(f"Applied {len(rows) - len(problems)} of {len(rows)} proposals to the mock at {API_BASE}")
    if problems:
        raise SystemExit("Not applied:\n" + "\n".join(problems))


if __name__ == "__main__":
    main()
