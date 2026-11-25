"""Settings from environment variables, falling back to a .env file in the repo root."""
import os
from pathlib import Path

import truststore

# Use the operating system's certificate store, so HTTPS works behind corporate TLS inspection.
truststore.inject_into_ssl()

ROOT = Path(__file__).resolve().parent.parent


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            os.environ.setdefault(key.strip(), value)


_load_env_file(ROOT / ".env")

SITE_BASE = os.environ.get("BH_SITE_BASE", "https://analyst-assessment-production.up.railway.app")
API_BASE = f"{SITE_BASE}/api/v1"  # the graded sandbox: every approved write lands here
DB_PATH = Path(os.environ.get("BH_DB_PATH", ROOT / "data" / "state.db"))
BELLHAVEN_PARENT_NAME = "Bellhaven Senior Living (Parent Account)"
# Decision #1: status for a Bellhaven account that is gone from the website and has no other copy.
NOT_ON_WEBSITE_STATUS = os.environ.get("BH_NOT_ON_WEBSITE_STATUS", "Needs Review")


def api_token() -> str:
    token = os.environ.get("BH_API_TOKEN", "")
    if not token:
        raise RuntimeError("BH_API_TOKEN is not set. Copy .env.example to .env and add your token.")
    return token
