"""Daily job: scrape the website, read the CRM, store new proposals. It never writes to the CRM."""
import argparse
import datetime
import json

from . import store
from .classify import build_proposals
from .config import API_BASE, DB_PATH
from .crm import CrmClient
from .scrape import scrape


def run(conn, client, scrape_fn, today: str) -> dict:
    run_id = store.start_run(conn)
    locations = scrape_fn()
    accounts, contacts = client.list_accounts(), client.list_contacts()
    proposals = build_proposals(locations, accounts, contacts, today)
    new, known = store.add_proposals(conn, run_id, proposals)
    superseded = store.supersede_missing(conn, [p.key for p in proposals])
    stats = {"locations": len(locations), "accounts": len(accounts), "proposals": len(proposals),
             "new": new, "already_known": known, "superseded": superseded}
    store.finish_run(conn, run_id, stats)
    return stats


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DB_PATH))
    args = parser.parse_args(argv)
    conn = store.connect(args.db, crm_base=API_BASE)
    stats = run(conn, CrmClient(), scrape, datetime.date.today().isoformat())
    print(json.dumps(stats))


if __name__ == "__main__":
    main()
