"""Daily job: scrape the website, read the CRM, store new proposals. It never writes to the CRM."""
import argparse
import datetime
import json

from . import store
from .classify import build_proposals
from .config import DB_PATH
from .crm import CrmClient
from .scrape import scrape


def run(conn, client, scrape_fn, today: str) -> dict:
    """One daily run: scrape, read the CRM, store proposals. Never writes to the CRM.

    If the scrape or a CRM read fails, the exception propagates before any proposal is touched, so nothing is
    superseded, and cron sees a non-zero exit. The run's row keeps finished_at NULL and its stats record the
    error, so the review app can show why the run stopped.
    """
    run_id = store.start_run(conn)
    try:
        locations = scrape_fn()
        accounts, contacts = client.list_accounts(), client.list_contacts()
        proposals = build_proposals(locations, accounts, contacts, today)
        new, known = store.add_proposals(conn, run_id, proposals)
        superseded = store.supersede_missing(conn, [p.key for p in proposals])
        stats = {"locations": len(locations), "accounts": len(accounts), "contacts": len(contacts),
                 "proposals": len(proposals), "new": new, "already_known": known, "superseded": superseded}
    except Exception as e:
        store.fail_run(conn, run_id, f"{type(e).__name__}: {e}")
        raise
    store.finish_run(conn, run_id, stats)
    return stats


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DB_PATH))
    args = parser.parse_args(argv)
    conn = store.connect(args.db)
    stats = run(conn, CrmClient(), scrape, datetime.date.today().isoformat())
    print(json.dumps(stats))


if __name__ == "__main__":
    main()
