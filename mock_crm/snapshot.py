"""Save a read-only copy of the LIVE CRM sandbox. It fills the mock and is the 'before' state for the audit.

Run once, before approving anything live:  python -m mock_crm.snapshot
"""
import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from bellhaven.config import LIVE_API_BASE, ROOT, is_graded_crm
from bellhaven.crm import CrmClient

DEFAULT_OUT = ROOT / "data" / "snapshot" / "crm.json"


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    args = parser.parse_args(argv)
    out = Path(args.out)
    if out.exists():
        raise SystemExit(f"{out} already exists. It is the 'before' state; delete it only if you mean to replace it.")
    if not is_graded_crm(LIVE_API_BASE):
        raise SystemExit(f"{LIVE_API_BASE} is not the graded sandbox; the 'before' state must come from the live CRM.")
    client = CrmClient(base=LIVE_API_BASE)  # GET requests only
    snapshot = {"taken_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "accounts": client.list_accounts(), "contacts": client.list_contacts()}
    ours = [a["account_id"] for a in snapshot["accounts"] if a.get("created_by_candidate")]
    if ours:
        raise SystemExit(f"{len(ours)} accounts were already created by this tool, so this isn't a 'before' state.")
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
    os.replace(tmp, out)  # never leave a half-written 'before' state behind
    print(f"Saved {len(snapshot['accounts'])} accounts and {len(snapshot['contacts'])} contacts to {out}")


if __name__ == "__main__":
    main()
