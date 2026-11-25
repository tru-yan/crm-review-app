"""Save a read-only copy of the LIVE CRM sandbox. It fills the mock and is the 'before' state for the audit.

Run once, before approving anything live:  python -m mock_crm.snapshot
"""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from bellhaven.config import LIVE_API_BASE, ROOT
from bellhaven.crm import CrmClient

DEFAULT_OUT = ROOT / "data" / "snapshot" / "crm.json"


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    args = parser.parse_args(argv)
    out = Path(args.out)
    if out.exists():
        raise SystemExit(f"{out} already exists. It is the 'before' state; delete it only if you mean to replace it.")
    client = CrmClient(base=LIVE_API_BASE)  # GET requests only
    snapshot = {"taken_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "accounts": client.list_accounts(), "contacts": client.list_contacts()}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
    print(f"Saved {len(snapshot['accounts'])} accounts and {len(snapshot['contacts'])} contacts to {out}")


if __name__ == "__main__":
    main()
