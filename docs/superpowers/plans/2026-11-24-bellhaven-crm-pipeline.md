# Bellhaven CRM Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a daily pipeline that scrapes every Bellhaven community from the website, matches each one to the CRM, proposes fixes, and writes only reviewer-approved fixes back through the CRM API. Then use it to leave the sandbox CRM corrected.

**Architecture:** A Python package in stages: **scrape → read CRM → match → propose → review → apply**. Matching and proposal-building are pure functions that are easy to test. A SQLite file stores every proposal, decision and API call, which is what makes re-runs safe. A Streamlit app is the only place a write can start, and `apply.py` is the only module that calls the CRM's write endpoints. A local mock of the CRM API, filled from a read-only snapshot of the sandbox, lets the whole flow be rehearsed without touching the graded copy. A read-only audit then checks the end state.

**Tech Stack:** Python 3.12, `requests` + `truststore`, `beautifulsoup4`, `rapidfuzz`, `streamlit`, `sqlite3` (standard library), `pytest`, plus `fastapi` + `uvicorn` + `httpx` for the mock CRM and its tests. Schedule: a cron entry.

---

## Changes made during the build (2026-11-24)

Each task was built from this plan, then reviewed twice: once against the spec, once for code quality. Review fixes and one final whole-branch review changed the code below. **Where the code on branch `bellhaven-pipeline` differs from a code block in this plan, the code is authoritative.** The suite has 147 tests.

- **Task 1, normalize:**
  - Phone extensions are ignored.
  - Periods are removed rather than turned into spaces ("N.W." → "nw").
- **Task 2, scrape and config:**
  - The scrape fails closed if the "N communities listed" text is missing or the directory is empty. Parse errors name the page.
  - `.env` parsing handles a byte-order mark, quotes and `export`.
  - `USING_MOCK` is true only for local hosts (`is_mock_crm`). `is_graded_crm` compares hosts and ignores a trailing dot.
- **Task 3, crm:**
  - Network and non-JSON errors become `CrmError`.
  - Paging has a shape check, a loop guard and a final count check.
  - Writes are never retried.
- **Task 4, sop:** NaN or unreadable billing values count as "money involved". Whitespace counts as blank.
- **Task 6, classify:**
  - A direct re-parent stores the billing values in `before`, so the SOP is re-checked at write time.
  - A CHOW that comes with duplicates is low-confidence: review them together.
  - Weak matches beside a strong one are no longer hidden.
  - not_on_website `before` includes `parent_id`.
  - An unlinked CHOW's own new account is never proposed as a duplicate.
- **Task 7, store:**
  - Every write is a single transaction. WAL mode, and a 30 s lock timeout.
  - The status conditions are inside each UPDATE, so the app's decisions are never overwritten.
  - Superseded proposals reopen when produced again. Applied ones reopen only if decided before the run started.
  - A proposal still "applying" reopens only if it was claimed 10 minutes before the run started.
  - Failed proposals with a logged write are never superseded.
  - New functions: `claim()` and a guarded `reject()`. Timestamps are in microseconds and strictly increasing.
- **Task 8, apply:**
  - An atomic claim (`AlreadyHandled` on a double click), then a re-read of the stored row.
  - Rule re-checks:
    - a CHOW requires revenue and AR
    - nothing is written to an already-resolved account
    - a duplicate's survivor must be live
  - Create-once uses our own API log first, then a strict name + street + zip search.
  - "Already applied" detection for a lost response.
  - Failures are logged, and a CHOW that fails after its create says "Retry it now, before the next pipeline run".
  - A create, or a CHOW's new account, only POSTs if the latest finished pipeline run still proposes it. An account that already landed is still recovered.
- **Task 9, pipeline:** the stats include `contacts`. There is a test that a failed read changes nothing.
- **Task 10, mock and snapshot:**
  - The mock rejects wrong types, blank names, empty PATCHes, self-references, unknown filters and malformed JSON.
  - The snapshot refuses unless it reads the graded host, refuses a CRM that already holds our writes, and writes atomically.
- **Task 11, audit:**
  - A CHOW old account must be unchanged except for the link.
  - Duplicate and CHOW links must point to live accounts.
  - Collateral damage is flagged: fields this tool never edits, or an account set Inactive without a duplicate link. So is an account created twice.
  - `main()` returns the exit code.
- **Task 12, app:**
  - The app doesn't set "applying" itself (apply claims the proposal).
  - Reject is guarded.
  - Failed proposals show their last error, and "applying" rows are explained.
- **Task 13, docs:** accurate re-run wording, the two-terminal mock setup, and a schtasks command with a log file.
- **Known limitations, for the writeup:**
  - Partially approving a CHOW-with-duplicates group.
  - A CHOW whose link response is lost ends "stale" (the CRM is correct, but the proposal is never marked applied).
  - Linking to an existing Bellhaven copy instead of creating a new account was deferred.

**Two rules for the live review (Tasks 15–16):**
1. Don't run the pipeline while you're approving.
2. If an ownership change (CHOW) reports an error, press Retry on it before running the pipeline again.

**Review app replaced (2026-11-24):** the Streamlit `review_app.py` from Task 12 was replaced by a Meridian-style FastAPI app: run `python -m bellhaven.web` and open http://localhost:8000. See `docs/superpowers/plans/2026-11-24-review-app-redesign.md`. Wherever this plan mentions `review_app.py`, Streamlit or the app's mock/live banner, read `bellhaven.web` and its **Mock CRM** / **LIVE SANDBOX** badge; Task 12's code is history. The suite now has 208 tests.

## Background (read this first)

The brief is in `Bellhaven_CRM_Exercise.docx` in the repo root. Here is what exploring the site and the API turned up on 2026-11-24:

**Website** (`https://analyst-assessment-production.up.railway.app`)
- `/communities` lists **34** communities on 3 pages (`?page=2`, `?page=3`, with a "Next →" link in `.pager`). The first page says "34 communities listed".
- The homepage says "35 communities". The 35th, **Bellhaven Meadows of Findlay**, is linked only from a homepage banner. The scraper must also collect community links from `/` and `/about`.
- Detail pages (`/communities/<slug>`) contain an `h1` name and a `dl.detail` with Address (street `<br>` "City, ST 12345"), Care Offerings (`.badge` spans), Administrator and Phone.
- `/about`: Bellhaven acquired **Harborview Care Group in 2025** and some **Cedar Trail** communities in 2026. That explains most wrong parents.

**CRM API** (`/api/v1`, header `Authorization: Bearer <token>`)
- `GET /accounts` takes the filters `q`, `city`, `state`, `zip`, `street`, `parent_id`, `page` and `page_size` (max 50). It returns `{"data": [...], "page", "page_size", "total"}`. There are 121 accounts.
- `GET /accounts/{id}`, `POST /accounts`, `PATCH /accounts/{id}`. There is no delete.
- `GET /contacts?account_id=` returns 67 contacts, each with `account_id`, `name`, `title` and `is_active`. Most facilities have their **Administrator** as a contact, which is strong matching evidence.
- Account fields: `account_id, name, parent_id, parent_name, billing_street, billing_city, billing_state, billing_zip, care_type, status, phone, lifetime_revenue, outstanding_ar, chow_current_account, duplicate_of_account, note, created_by_candidate, updated_at`.
- **The OpenAPI spec has no request-body schema, and `POST /accounts` declares no validation error.** Never send a test POST: every POST creates a real account in the CRM that gets graded.
- **HTTPS on this laptop:** `requests` fails with `CERTIFICATE_VERIFY_FAILED`, which is typical of corporate TLS inspection. `config.py` calls `truststore.inject_into_ssl()` so `requests` uses the Windows certificate store. Don't turn off certificate verification instead.

**Traps found in the data**
- Matching on name alone gives wrong answers. The CRM has an "Amberly Manor" in Colorado Springs, CO, which is not the one in Hudson, OH. "Union Square Senior Living" is in the same town as "Bellhaven at Union Square" but has a different address, parent and administrator. "Bellhaven of New Carlisle, OH" is not "Bellhaven of Carlisle, PA".
- Phone numbers differ between the website and the CRM for about half of the true matches. Use phone as evidence only.
- Addresses are written in different ways: `W`/`West`, `NW`/`Northwest`, `Pk`/`Pike`, `Rd W`/`Road West`. One account's street is a PO Box. One zip has two digits swapped (45626 vs 45662).
- Care types use different names. "Short-Term Rehabilitation & Nursing" on the website is "Skilled Nursing" in the CRM, and "Memory Support" is "Memory Care".
- Billing rule (SOP): Akron, Defiance and Meadville have revenue and unpaid AR but are already under Bellhaven, so they don't move. Lima and Findlay have revenue but zero AR, so they move directly. Tiffin and Marietta (Cedar Trail) have both, so they need a change of ownership (CHOW).
- About 60 accounts belong to other operators (Juniper Point, Stonebridge, or no parent). Leave them alone.

## Decisions this plan makes (change them before starting if you disagree)

| # | Question | Default in this plan | Alternative |
|---|---|---|---|
| 1 | A Bellhaven account is gone from the website and has no other copy | `status = "Needs Review"` plus a note (set by `BH_NOT_ON_WEBSITE_STATUS`) | `Inactive` |
| 2 | A Bellhaven account is gone from the website, but another operator's account is at the same address (Sandusky: billing history, and "Millstone Care of Sandusky" at the same address) | **Decided:** treat it like #1, with `Needs Review` and a note naming the other account. Nothing is linked or merged automatically, because the CRM alone can't prove who owns it now. | Link it with `chow_current_account`, or create a new account under Millstone |
| 3 | The phone number differs from the website | Evidence only, never updated | Propose phone updates |
| 4 | The website lists 2 care types; the CRM field holds one | Leave it if the CRM value is one of them, otherwise use the first listed | — |
| 5 | Contacts for new accounts | Not created | Create an Administrator contact |
| 6 | Schedule | cron entry in `deploy/crontab.txt` | GitHub Actions workflow |
| 7 | Live writes before the review | **Decided:** none. The format test write in Task 3 is skipped; the first live write is the careful Portsmouth approval in Task 16. | A harmless empty-note update in Task 3 |

Also decided: build **subagent-driven** (a fresh subagent per task, reviewed between tasks), and #1 stays `Needs Review`.

## Architecture

```
 DAILY PIPELINE (cron: python -m bellhaven.pipeline). Reads only, never writes to the CRM
 ┌───────────────────────────────────────────────────────────────────────────────────────┐
 │ Bellhaven website ─► scrape.py ──┐                                                    │
 │                                  ├─► match.py ─► classify.py ─► store.py (SQLite)    │
 │ CRM API (GET) ─────► crm.py ─────┘   normalize.py   sop.py        new proposals only  │
 └───────────────────────────────────────────────────────────────────────┬───────────────┘
                                                                         │ pending proposals
 HUMAN REVIEW                                                            ▼
   reviewer ─► review_app.py ── reject ─► decision saved (never proposed again)
                     │ approve
                     ▼
               apply.py ─► CRM API (POST / PATCH) ─► api_log + change_log (store.py)

 TEST BENCH: mock_crm/ (local CRM API from a read-only snapshot) · audit.py (read-only end-state check)
```

**How each piece solves the problem.** The business needs to know which parent company owns each facility, and the brief turns that into the requirements quoted below.

| Piece | Brief requirement | What it solves |
|---|---|---|
| `scrape.py` | "A scraper that pulls every Bellhaven location" | A current list of who Bellhaven owns. It also catches the homepage-only Findlay page, and stops if the directory count doesn't add up rather than flagging good accounts as missing. |
| `crm.py` | API access | The single gateway to the CRM. The pipeline only reads; only `apply.py` writes. |
| `normalize.py` + `match.py` | "Matching logic that links each location to the right CRM account" | Links facilities despite rebrands, address formatting and stale phones, and avoids name-only traps (same name in another state, same town at a different address). |
| `classify.py` | "…and classifies it": confident match, needs a fix, no CRM account, no longer on the website; CRM conventions (`status`, `note`, `duplicate_of_account`) | Turns each finding into one specific, explained change: update, chow, duplicate, create or not_on_website. |
| `sop.py` | "One SOP you must follow" | Protects billing history: when revenue and AR are both > 0, use a change of ownership instead of moving the account. |
| `store.py` | "Re-runs must be safe… must not re-propose items that were already decided" | Remembers every proposal and decision, and logs every API change field by field (the audit trail for sales and billing). |
| `review_app.py` | "A small review app… sees each proposed change with its supporting evidence… approve or reject… Nothing writes without approval" | A human makes every call, with the website, the CRM record, the match score and the SOP check side by side. |
| `apply.py` | "Approved changes write back to the CRM through the API" | Writes only approved changes, refuses if the record changed since the proposal was made, never creates the same account twice, and logs each change. |
| `pipeline.py` + `deploy/crontab.txt` | "Built to run daily" | Keeps the ownership picture current as facilities are bought and sold. |
| `mock_crm/` + `audit.py` | "Your finished submission is a corrected CRM, not just a tool" | Rehearse everything without touching the graded copy, then prove the final state is right. |

## Database (SQLite, `data/state.db`)

```
runs 1──* proposals 1──* api_log 1──* change_log        meta: which CRM this database belongs to
                     └──────────────────────* change_log
change_history (view) = change_log joined to proposals: who/what/why for every field changed
```

| Table | One row per | Main columns |
|---|---|---|
| `runs` | Pipeline run | `started_at`, `finished_at`, `stats` |
| `proposals` | Proposed change | `key` (PK, a hash of the decision), `run_id` → runs, `kind`, `account_id`, `changes` / `before` / `evidence` (JSON), `confidence`, `status` (CHECK: pending, applying, applied, rejected, failed, superseded), `decided_at`, `comment`, `result` |
| `api_log` | Write call to the CRM API | `proposal_key` → proposals, `method`, `path`, `body`, `response`, `at` |
| `change_log` | Field changed through the API | `api_call_id` → api_log, `proposal_key` → proposals, `account_id`, `action` (create or update), `field`, `old_value`, `new_value`, `changed_at` |
| `meta` | Setting | `crm_base`: ties the database to one CRM, so mock and live decisions never mix |

Foreign keys are enforced (`PRAGMA foreign_keys = ON` on every connection). `old_value` comes from the fresh read just before writing, and `new_value` is what the CRM returned. That means the log records what actually landed, not what was sent.

## File map

| File | Responsibility |
|---|---|
| `requirements.txt`, `pyproject.toml`, `.gitignore`, `.env.example` | Setup. `pyproject.toml` holds only pytest settings. |
| `bellhaven/config.py` | URLs, database path, token from env or `.env`, Bellhaven parent name, decision #1 |
| `bellhaven/models.py` | `Location` dataclass (one website community) |
| `bellhaven/normalize.py` | Street, zip, phone and name normalization; care-type map. Comparison only. |
| `bellhaven/scrape.py` | Directory pages, extra homepage/about links, detail pages → `list[Location]` |
| `bellhaven/crm.py` | `CrmClient`: paginated reads and the two write calls |
| `bellhaven/sop.py` | The billing rule |
| `bellhaven/match.py` | Scores a location against an account; one-to-one assignment |
| `bellhaven/classify.py` | Turns matches into `Proposal`s (update, chow, duplicate, create, not_on_website). Pure. |
| `bellhaven/store.py` | SQLite (relational, foreign keys enforced): runs → proposals → api_log → change_log, plus meta and the change_history view. Idempotent inserts. |
| `bellhaven/apply.py` | Writes one approved proposal, with a freshness check and logging. The only writer. |
| `bellhaven/pipeline.py` | Daily command (`python -m bellhaven.pipeline`). Never writes to the CRM. |
| `bellhaven/audit.py` | Read-only end-state check against the mock or live CRM (`python -m bellhaven.audit`) |
| `review_app.py` | Streamlit review queue (repo root, so `import bellhaven` works). Shows whether it's pointed at the mock or the live sandbox. |
| `mock_crm/server.py` | Local stand-in for the CRM API (FastAPI), in memory, stricter than the real one |
| `mock_crm/snapshot.py` | Read-only copy of the live sandbox: fills the mock and is the "before" state for the audit |
| `mock_crm/approve_all.py` | Rehearsal helper: approves every pending proposal. Refuses to run against the live sandbox. |
| `deploy/crontab.txt` | Schedule |
| `tests/factories.py` | Test builders plus `FakeCrm` |
| `tests/test_*.py` | One test file per module |
| `README.md`, `WRITEUP.md` | How to run; the explanation of choices for graders |

---

### Task 0: Project setup

**Files:**
- Create: `requirements.txt`, `pyproject.toml`, `.gitignore`, `.env.example`, `bellhaven/__init__.py`, `tests/__init__.py`, `tests/test_smoke.py`

- [ ] **Step 1: Initialise git and the virtual environment** (PowerShell, from `C:\Users\463995\rt_pg\crm`)

```powershell
git init
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

- [ ] **Step 2: Write `requirements.txt`**

```text
requests>=2.31
truststore>=0.9
beautifulsoup4>=4.12
rapidfuzz>=3.6
streamlit>=1.38
pytest>=8.0
fastapi>=0.110
uvicorn>=0.29
httpx>=0.27
```

- [ ] **Step 3: Write `pyproject.toml`**

```toml
[tool.pytest.ini_options]
pythonpath = ["."]
testpaths = ["tests"]
```

- [ ] **Step 4: Write `.gitignore`.** The brief's screenshots and the Word copy contain the personal API token, so they stay out of git.

```text
.venv/
__pycache__/
*.pyc
.env
data/
logs/
image (*).png
*.docx
```

- [ ] **Step 5: Write `.env.example`, then copy it to `.env` and paste the token in**

```text
# Copy to .env and fill in. Never commit .env.
BH_API_TOKEN=
```

```powershell
Copy-Item .env.example .env
```

- [ ] **Step 6: Create empty `bellhaven/__init__.py` and `tests/__init__.py`, and write `tests/test_smoke.py`**

```python
import bellhaven


def test_package_imports():
    assert bellhaven.__name__ == "bellhaven"
```

- [ ] **Step 7: Install and run**

Run: `python -m pip install -r requirements.txt; python -m pytest -q`
Expected: `1 passed`

- [ ] **Step 8: Commit**

```powershell
git add requirements.txt pyproject.toml .gitignore .env.example bellhaven tests docs
git commit -m "chore: project setup"
```

---

### Task 1: Normalization

**Files:**
- Create: `bellhaven/normalize.py`
- Test: `tests/test_normalize.py`

- [ ] **Step 1: Write the failing tests**

```python
import pytest

from bellhaven.normalize import crm_care_types, is_po_box, norm_name, norm_phone, norm_street, same_text, zip5


@pytest.mark.parametrize("a, b", [
    ("4850 Northwest Sylvania Avenue", "4850 NW Sylvania Ave"),
    ("199 Barks Road West", "199 Barks Rd W"),
    ("3313 Wilmington Pk", "3313 Wilmington Pike"),
    ("980 West Michigan Avenue", "980 W Michigan Ave"),
    ("100 Main St Suite 4", "100 Main Street"),
    ("100 Main St, Ste. 200", "100 Main St"),
])
def test_street_variants_normalize_equal(a, b):
    assert norm_street(a) == norm_street(b)


def test_street_name_starting_with_ste_is_kept():
    # regression: a prefix match on "ste" once turned "750 Stewart Rd" into "750 Rd"
    assert norm_street("750 Stewart Rd") == "750 stewart rd"


def test_different_streets_stay_different():
    assert norm_street("118 Union Square Dr") != norm_street("240 Market St")


def test_po_box():
    assert is_po_box("PO Box 517")
    assert is_po_box("P.O. Box 5")
    assert not is_po_box("100 Box Elder Rd")


def test_zip5():
    assert zip5("45662-1234") == "45662"
    assert zip5("") == ""


def test_phone():
    assert norm_phone("(614) 250-9447") == "6142509447"
    assert norm_phone("1-614-250-9447") == "6142509447"
    assert norm_phone("") == ""


def test_name_normalization_for_similarity():
    assert norm_name("Bellhaven Rehab and Nursing of Grove City") == norm_name("Bellhaven Rehabilitation & Nursing of Grove City")
    assert norm_name("Bellhaven Health Care Center of Ashland") == norm_name("Bellhaven Healthcare Centre of Ashland")


def test_same_text_ignores_only_case_and_spacing():
    assert same_text(" Bellhaven  of Owosso", "bellhaven of owosso")
    assert not same_text("Bellhaven of Sycamore Ridge", "Bellhaven at Sycamore Ridge")


def test_care_type_map():
    assert crm_care_types(("Short-Term Rehabilitation & Nursing", "Memory Support")) == ["Skilled Nursing", "Memory Care"]
    assert crm_care_types(("Hospice",)) == ["Hospice"]
```

- [ ] **Step 2: Run them to check they fail**

Run: `python -m pytest tests/test_normalize.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'bellhaven.normalize'`

- [ ] **Step 3: Write `bellhaven/normalize.py`**

```python
"""Normalization for comparing website data with CRM data. Comparison only: never write these values."""
import re

_STREET_WORDS = {
    "street": "st", "avenue": "ave", "road": "rd", "drive": "dr", "boulevard": "blvd",
    "lane": "ln", "court": "ct", "place": "pl", "parkway": "pkwy", "highway": "hwy",
    "pk": "pike", "pke": "pike",
    "north": "n", "south": "s", "east": "e", "west": "w",
    "northwest": "nw", "northeast": "ne", "southwest": "sw", "southeast": "se",
}
# Whole words only: "Ste" is a suite, but "Stewart" is a street name.
_UNIT = re.compile(r"(?:\b(?:suite|ste|unit|apt)\b\.?|#)\s*[\w-]+", re.IGNORECASE)
_PO_BOX = re.compile(r"^\s*p\.?\s*o\.?\s*box\b", re.IGNORECASE)

CARE_TYPE_MAP = {
    "Short-Term Rehabilitation & Nursing": "Skilled Nursing",
    "Assisted Living": "Assisted Living",
    "Memory Support": "Memory Care",
    "Independent Living": "Independent Living",
}


def norm_street(street: str) -> str:
    s = _UNIT.sub(" ", street or "")
    s = re.sub(r"[.,]", " ", s.lower())
    return " ".join(_STREET_WORDS.get(word, word) for word in s.split())


def is_po_box(street: str) -> bool:
    return bool(_PO_BOX.match(street or ""))


def zip5(zip_code: str) -> str:
    return re.sub(r"\D", "", zip_code or "")[:5]


def norm_phone(phone: str) -> str:
    digits = re.sub(r"\D", "", phone or "")
    return digits[-10:] if len(digits) >= 10 else ""


def norm_name(name: str) -> str:
    """For similarity scoring only. Name *changes* use same_text, because any wording change is real."""
    n = (name or "").lower().replace("&", " and ")
    n = re.sub(r"\bhealth care\b", "healthcare", n)
    n = re.sub(r"\bcentre\b", "center", n)
    n = re.sub(r"\brehab\b", "rehabilitation", n)
    n = re.sub(r"[^a-z0-9 ]", " ", n)
    return " ".join(n.split())


def same_text(a: str, b: str) -> bool:
    return " ".join((a or "").split()).lower() == " ".join((b or "").split()).lower()


def crm_care_types(offerings) -> list[str]:
    return [CARE_TYPE_MAP.get(o, o) for o in offerings]
```

- [ ] **Step 4: Run the tests to check they pass**

Run: `python -m pytest tests/test_normalize.py -q`
Expected: all pass

- [ ] **Step 5: Commit**

```powershell
git add bellhaven/normalize.py tests/test_normalize.py
git commit -m "feat: address, phone and name normalization"
```

---

### Task 2: Location model and scraper

**Files:**
- Create: `bellhaven/config.py`, `bellhaven/models.py`, `bellhaven/scrape.py`
- Test: `tests/test_scrape.py`

- [ ] **Step 1: Write `bellhaven/config.py`** (no test needed; the other modules use it)

```python
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
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


_load_env_file(ROOT / ".env")

SITE_BASE = os.environ.get("BH_SITE_BASE", "https://analyst-assessment-production.up.railway.app")
LIVE_API_BASE = f"{SITE_BASE}/api/v1"
# Set BH_API_BASE to the mock CRM (Task 10) to test without touching the graded sandbox.
API_BASE = os.environ.get("BH_API_BASE", LIVE_API_BASE)
USING_MOCK = API_BASE != LIVE_API_BASE
DB_PATH = Path(os.environ.get("BH_DB_PATH", ROOT / "data" / "state.db"))
BELLHAVEN_PARENT_NAME = "Bellhaven Senior Living (Parent Account)"
# Decision #1: status for a Bellhaven account that is gone from the website and has no other copy.
NOT_ON_WEBSITE_STATUS = os.environ.get("BH_NOT_ON_WEBSITE_STATUS", "Needs Review")


def api_token() -> str:
    token = os.environ.get("BH_API_TOKEN", "")
    if not token:
        raise RuntimeError("BH_API_TOKEN is not set. Copy .env.example to .env and add your token.")
    return token
```

- [ ] **Step 2: Write `bellhaven/models.py`**

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class Location:
    """One community as listed on the Bellhaven website."""
    slug: str
    url: str
    name: str
    street: str
    city: str
    state: str
    zip: str
    phone: str
    administrator: str
    care_offerings: tuple[str, ...]
    in_directory: bool
```

- [ ] **Step 3: Write the failing tests `tests/test_scrape.py`**

```python
import pytest

from bellhaven.scrape import ScrapeError, parse_detail, parse_listing, scrape

BASE = "https://site.test"


def listing(slugs, next_page=None, total=3):
    cards = "".join(f'<div class="card"><h3><a href="{s}">{s}</a></h3></div>' for s in slugs)
    nxt = f'<a href="{next_page}">Next &rarr;</a>' if next_page else ""
    return (f'<div class="wrap"><p>Page 1 of 2 · {total} communities listed</p>'
            f'<div class="cardgrid">{cards}</div>'
            f'<div class="pager"><a href="/communities?page=1">&larr; Prev</a>{nxt}</div></div>')


def detail(name, street="100 Main Street", city_line="Testville, OH 44000", offerings=("Assisted Living",)):
    badges = "".join(f'<span class="badge">{o}</span>' for o in offerings)
    return (f'<div class="wrap"><h1>{name}</h1><dl class="detail">'
            f'<dt>Address</dt><dd>{street}<br>{city_line}</dd>'
            f'<dt>Care Offerings</dt><dd>{badges}</dd>'
            f'<dt>Administrator</dt><dd>Pat Admin</dd>'
            f'<dt>Phone</dt><dd>(614) 555-0100</dd></dl></div>')


class FakeResponse:
    def __init__(self, text, status=200):
        self.text, self.status_code = text, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self, pages):
        self.pages = pages

    def get(self, url, timeout):
        path = url.removeprefix(BASE)
        return FakeResponse(self.pages[path]) if path in self.pages else FakeResponse("", 404)


def test_parse_listing():
    slugs, nxt, count = parse_listing(listing(["/communities/a"], "/communities?page=2", total=34))
    assert slugs == ["/communities/a"]
    assert nxt == "/communities?page=2"
    assert count == 34


def test_parse_detail():
    loc = parse_detail(detail("Bellhaven Shores of Erie", "4930 W Lake Rd", "Erie, PA 16505",
                              ("Assisted Living", "Memory Support")), "/communities/erie", BASE, True)
    assert (loc.name, loc.street, loc.city, loc.state, loc.zip) == ("Bellhaven Shores of Erie", "4930 W Lake Rd", "Erie", "PA", "16505")
    assert loc.care_offerings == ("Assisted Living", "Memory Support")
    assert loc.administrator == "Pat Admin" and loc.phone == "(614) 555-0100"
    assert loc.url == "https://site.test/communities/erie"


def pages(total=3):
    return {
        "/communities": listing(["/communities/a", "/communities/b"], "/communities?page=2", total),
        "/communities?page=2": listing(["/communities/c"], None, total),
        "/": '<a href="/communities/d">New!</a> <a href="/communities/a">A</a> <a href="/communities">All</a>',
        "/about": "<p>About us</p>",
        "/communities/a": detail("A"), "/communities/b": detail("B"),
        "/communities/c": detail("C"), "/communities/d": detail("D"),
    }


def test_scrape_follows_pager_and_finds_homepage_only_communities():
    locs = scrape(FakeSession(pages()), base=BASE, delay=0)
    assert [l.name for l in locs] == ["A", "B", "C", "D"]
    assert [l.in_directory for l in locs] == [True, True, True, False]


def test_scrape_fails_when_directory_count_is_wrong():
    with pytest.raises(ScrapeError, match="says 5"):
        scrape(FakeSession(pages(total=5)), base=BASE, delay=0)
```

- [ ] **Step 4: Run them to check they fail**

Run: `python -m pytest tests/test_scrape.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'bellhaven.scrape'`

- [ ] **Step 5: Write `bellhaven/scrape.py`**

```python
"""Scrape every Bellhaven community: all directory pages plus community links found elsewhere on the site."""
import re
import time
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from .config import SITE_BASE
from .models import Location

EXTRA_LINK_PAGES = ("/", "/about")
_CITY_LINE = re.compile(r"^(?P<city>.+?),\s*(?P<state>[A-Z]{2})\s+(?P<zip>\d{5}(?:-\d{4})?)$")


class ScrapeError(RuntimeError):
    pass


def parse_listing(html: str) -> tuple[list[str], str | None, int | None]:
    """Return (community slugs, next page path, the 'N communities listed' count)."""
    soup = BeautifulSoup(html, "html.parser")
    slugs = [a["href"] for a in soup.select(".card h3 a[href^='/communities/']")]
    next_path = next((a["href"] for a in soup.select(".pager a") if "Next" in a.get_text()), None)
    m = re.search(r"(\d+)\s+communities listed", soup.get_text(" "))
    return slugs, next_path, int(m.group(1)) if m else None


def parse_community_links(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    return [a["href"] for a in soup.select("a[href^='/communities/']") if "?" not in a["href"]]


def parse_detail(html: str, slug: str, base: str, in_directory: bool) -> Location:
    soup = BeautifulSoup(html, "html.parser")
    fields = {dt.get_text(strip=True).lower(): dt.find_next_sibling("dd") for dt in soup.select("dl.detail dt")}
    lines = list(fields["address"].stripped_strings)
    m = _CITY_LINE.match(lines[-1])
    if not m:
        raise ScrapeError(f"{slug}: cannot parse city/state/zip from {lines[-1]!r}")
    offerings = fields.get("care offerings")
    return Location(
        slug=slug,
        url=urljoin(base, slug),
        name=soup.select_one(".wrap h1").get_text(strip=True),
        street=", ".join(lines[:-1]),
        city=m["city"],
        state=m["state"],
        zip=m["zip"],
        phone=fields["phone"].get_text(strip=True) if "phone" in fields else "",
        administrator=fields["administrator"].get_text(strip=True) if "administrator" in fields else "",
        care_offerings=tuple(b.get_text(strip=True) for b in offerings.select(".badge")) if offerings else (),
        in_directory=in_directory,
    )


def scrape(session=None, base: str = SITE_BASE, delay: float = 0.3) -> list[Location]:
    s = session or requests.Session()

    def get(path: str) -> str:
        r = s.get(urljoin(base, path), timeout=30)
        r.raise_for_status()
        time.sleep(delay)
        return r.text

    directory, path, claimed, seen = [], "/communities", None, set()
    while path and path not in seen:
        seen.add(path)
        slugs, path, count = parse_listing(get(path))
        claimed = claimed if claimed is not None else count
        directory += [x for x in slugs if x not in directory]
    if claimed is not None and len(directory) != claimed:
        raise ScrapeError(f"directory says {claimed} communities but {len(directory)} were found")

    extra = []
    for page in EXTRA_LINK_PAGES:
        extra += [x for x in parse_community_links(get(page)) if x not in directory and x not in extra]

    return [parse_detail(get(slug), slug, base, slug in directory) for slug in directory + extra]
```

- [ ] **Step 6: Run the tests to check they pass**

Run: `python -m pytest tests/test_scrape.py -q`
Expected: 4 passed

- [ ] **Step 7: Check against the live site (read-only)**

Run: `python -c "from bellhaven.scrape import scrape; l = scrape(); print(len(l), [x.name for x in l if not x.in_directory])"`
Expected: `35 ['Bellhaven Meadows of Findlay']`

- [ ] **Step 8: Commit**

```powershell
git add bellhaven/config.py bellhaven/models.py bellhaven/scrape.py tests/test_scrape.py
git commit -m "feat: scrape all Bellhaven communities including homepage-only links"
```

---

### Task 3: CRM client

**Files:**
- Create: `bellhaven/crm.py`
- Test: `tests/test_crm.py`

- [ ] **Step 1: Write the failing tests**

```python
import json

import pytest

from bellhaven.crm import CrmClient, CrmError


class FakeResponse:
    def __init__(self, body, status=200):
        self.body, self.status_code, self.text = body, status, json.dumps(body)

    def json(self):
        return self.body


class FakeSession:
    def __init__(self, responses):
        self.headers, self.calls, self.responses = {}, [], list(responses)

    def request(self, method, url, timeout, params=None, json=None):
        self.calls.append((method, url, params, json))
        return self.responses.pop(0)


def page(ids, total):
    return FakeResponse({"data": [{"account_id": str(i)} for i in ids], "page": 1, "page_size": 50, "total": total})


def test_list_accounts_follows_pages_and_sends_token():
    s = FakeSession([page(range(50), 60), page(range(50, 60), 60)])
    accounts = CrmClient(token="t", base="https://api.test", session=s).list_accounts()
    assert len(accounts) == 60
    assert s.calls[1][2]["page"] == 2
    assert s.headers["Authorization"] == "Bearer t"


def test_errors_raise_with_status_and_body():
    s = FakeSession([FakeResponse({"detail": "bad status"}, status=422)])
    with pytest.raises(CrmError, match="422"):
        CrmClient(token="t", base="https://api.test", session=s).update_account("A1", {"status": "Nope"})
```

- [ ] **Step 2: Run them to check they fail**

Run: `python -m pytest tests/test_crm.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'bellhaven.crm'`

- [ ] **Step 3: Write `bellhaven/crm.py`**

```python
"""Thin client for the CRM sandbox API. Only bellhaven/apply.py may call create_account / update_account."""
import requests

from .config import API_BASE, api_token


class CrmError(RuntimeError):
    pass


class CrmClient:
    def __init__(self, token: str | None = None, base: str = API_BASE, session=None):
        self.base = base
        self.s = session or requests.Session()
        self.s.headers["Authorization"] = f"Bearer {token or api_token()}"

    def _req(self, method: str, path: str, **kwargs):
        r = self.s.request(method, f"{self.base}{path}", timeout=30, **kwargs)
        if r.status_code >= 400:
            raise CrmError(f"{method} {path} -> {r.status_code}: {r.text[:500]}")
        return r.json()

    def _list(self, path: str, **filters) -> list[dict]:
        out, page = [], 1
        while True:
            body = self._req("GET", path, params={**filters, "page": page, "page_size": 50})
            out += body["data"]
            if not body["data"] or len(out) >= body["total"]:
                return out
            page += 1

    def list_accounts(self, **filters) -> list[dict]:
        return self._list("/accounts", **filters)

    def list_contacts(self, **filters) -> list[dict]:
        return self._list("/contacts", **filters)

    def get_account(self, account_id: str) -> dict:
        return self._req("GET", f"/accounts/{account_id}")

    def create_account(self, body: dict) -> dict:
        return self._req("POST", "/accounts", json=body)

    def update_account(self, account_id: str, body: dict) -> dict:
        return self._req("PATCH", f"/accounts/{account_id}", json=body)
```

- [ ] **Step 4: Run the tests to check they pass**

Run: `python -m pytest tests/test_crm.py -q`
Expected: 2 passed

- [ ] **Step 5: Check reads against the live API**

Run: `python -c "from bellhaven.crm import CrmClient; c = CrmClient(); print(len(c.list_accounts()), len(c.list_contacts()))"`
Expected: `121 67` (or more, if you have already created accounts)

- [ ] **Step 6: No live write here (decision #7).** Don't send any `PATCH` or `POST` to the live sandbox while building. The mock (Task 10) tests the write path, and the real format is first confirmed by one careful approval in Task 16.

- [ ] **Step 7: Commit**

```powershell
git add bellhaven/crm.py tests/test_crm.py
git commit -m "feat: CRM API client"
```

---

### Task 4: Billing SOP

**Files:**
- Create: `bellhaven/sop.py`
- Test: `tests/test_sop.py`

- [ ] **Step 1: Write the failing tests**

```python
import pytest

from bellhaven.sop import must_preserve_old_account


@pytest.mark.parametrize("revenue, ar, expected", [
    (101000, 9000, True),   # history and money owed: keep the old account
    (47000, 0, False),      # history but nothing owed: re-parent directly
    (0, 500, False),        # owed but no history: re-parent directly
    (None, None, False),
    (100, -50, False),      # a credit is not "greater than zero"
    ("100", "5", True),
    ("n/a", 5, True),       # unreadable billing data: be conservative
])
def test_billing_sop(revenue, ar, expected):
    assert must_preserve_old_account({"lifetime_revenue": revenue, "outstanding_ar": ar}) is expected
```

- [ ] **Step 2: Run them to check they fail**

Run: `python -m pytest tests/test_sop.py -q`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write `bellhaven/sop.py`**

```python
"""Billing SOP for moving an account to a different parent."""


def amount(value) -> float:
    if value in (None, ""):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("inf")  # unreadable billing data: treat as money involved and preserve the account


def must_preserve_old_account(account: dict) -> bool:
    """True when the account has revenue history AND outstanding AR greater than zero.

    In that case the old account must stay exactly as it is: create a new account under the
    correct parent and set chow_current_account on the old one. Otherwise re-parent it directly.
    """
    return amount(account.get("lifetime_revenue")) > 0 and amount(account.get("outstanding_ar")) > 0
```

- [ ] **Step 4: Run the tests to check they pass**

Run: `python -m pytest tests/test_sop.py -q`
Expected: 7 passed

- [ ] **Step 5: Commit**

```powershell
git add bellhaven/sop.py tests/test_sop.py
git commit -m "feat: billing SOP rule"
```

---

### Task 5: Matching

**Files:**
- Create: `bellhaven/match.py`, `tests/factories.py`
- Test: `tests/test_match.py`

- [ ] **Step 1: Write `tests/factories.py`** (shared by the remaining test files)

```python
"""Test data builders and an in-memory CRM. Shapes copy the real API responses."""
from bellhaven.models import Location

BH, HARBORVIEW, CEDAR, MILLSTONE = "P_BELLHAVEN", "P_HARBORVIEW", "P_CEDAR", "P_MILLSTONE"
PARENT_NAMES = {
    BH: "Bellhaven Senior Living (Parent Account)",
    HARBORVIEW: "Harborview Care Group (Parent Account)",
    CEDAR: "Cedar Trail Communities (Parent Account)",
    MILLSTONE: "Millstone Health Partners (Parent Account)",
}


def make_account(**overrides) -> dict:
    account = {
        "account_id": "A1", "name": "Bellhaven of Testville", "parent_id": BH, "parent_name": PARENT_NAMES[BH],
        "billing_street": "100 Main St", "billing_city": "Testville", "billing_state": "OH", "billing_zip": "44000",
        "care_type": "Assisted Living", "status": "Active", "phone": "(614) 555-0100",
        "lifetime_revenue": 0, "outstanding_ar": 0, "chow_current_account": "", "duplicate_of_account": "",
        "note": "", "created_by_candidate": False, "updated_at": "2026-11-24 00:00:00Z",
    }
    account.update(overrides)
    if "parent_id" in overrides and "parent_name" not in overrides:
        account["parent_name"] = PARENT_NAMES.get(overrides["parent_id"], "")
    return account


def parent_accounts() -> list[dict]:
    return [make_account(account_id=pid, name=name, parent_id="", parent_name="", billing_street="",
                         billing_city="", billing_state="", billing_zip="", care_type="", phone="")
            for pid, name in PARENT_NAMES.items()]


def make_location(**overrides) -> Location:
    values = {
        "slug": "/communities/bellhaven-of-testville", "url": "https://site.test/communities/bellhaven-of-testville",
        "name": "Bellhaven of Testville", "street": "100 Main Street", "city": "Testville", "state": "OH",
        "zip": "44000", "phone": "(614) 555-0100", "administrator": "Pat Admin",
        "care_offerings": ("Assisted Living",), "in_directory": True,
    }
    values.update(overrides)
    return Location(**values)


class FakeCrm:
    """Stands in for CrmClient. Records every write in self.writes."""

    def __init__(self, accounts, contacts=()):
        self.accounts = {a["account_id"]: dict(a) for a in accounts}
        self.contacts = list(contacts)
        self.writes = []
        self._next_id = 0

    def list_accounts(self, **filters):
        field_for = {"zip": "billing_zip", "parent_id": "parent_id"}
        return [dict(a) for a in self.accounts.values()
                if all(a.get(field_for[k]) == v for k, v in filters.items())]

    def list_contacts(self, **filters):
        return list(self.contacts)

    def get_account(self, account_id):
        return dict(self.accounts[account_id])

    def create_account(self, body):
        self._next_id += 1
        record = make_account(**body, account_id=f"NEW{self._next_id}", created_by_candidate=True)
        self.accounts[record["account_id"]] = record
        self.writes.append(("POST", body))
        return dict(record)

    def update_account(self, account_id, body):
        self.accounts[account_id].update(body)
        self.writes.append(("PATCH", account_id, body))
        return dict(self.accounts[account_id])
```

- [ ] **Step 2: Write the failing tests `tests/test_match.py`**

```python
from bellhaven.match import MATCH_THRESHOLD, WEAK_THRESHOLD, assign_matches, score
from tests.factories import make_account, make_location


def test_same_address_written_differently_is_a_strong_match():
    m = score(make_location(street="199 Barks Rd W"), make_account(billing_street="199 Barks Road West"), [])
    assert "street" in m.signals and m.score >= MATCH_THRESHOLD


def test_rebranded_facility_matches_on_address_and_administrator():
    loc = make_location(name="Bellhaven of Chagrin Falls")
    acct = make_account(name="Riverbend Manor Care Center", phone="(517) 457-9548")
    m = score(loc, acct, [{"name": "Pat Admin"}])
    assert m.score >= MATCH_THRESHOLD and "administrator" in m.signals


def test_same_name_in_another_state_is_not_a_match():
    acct = make_account(name="Amberly Manor", billing_state="CO", billing_city="Colorado Springs")
    assert score(make_location(name="Amberly Manor"), acct, []).score == 0


def test_same_town_different_address_is_not_a_match():
    loc = make_location(name="Bellhaven at Union Square", street="118 Union Square Dr")
    acct = make_account(name="Union Square Senior Living", billing_street="240 Market St", phone="(260) 782-4620")
    assert score(loc, acct, [{"name": "Dale Croft"}]).score < WEAK_THRESHOLD


def test_different_house_number_is_not_the_same_street():
    m = score(make_location(street="102 Main Street"), make_account(billing_street="100 Main St"), [])
    assert "street" not in m.signals and "street_fuzzy" not in m.signals


def test_po_box_account_still_matches_on_phone_and_administrator():
    acct = make_account(billing_street="PO Box 517")
    assert score(make_location(), acct, [{"name": "Pat Admin"}]).score >= MATCH_THRESHOLD


def test_each_account_goes_to_its_best_location_only():
    exact = make_location(slug="/communities/exact")
    nearby = make_location(slug="/communities/nearby", name="Bellhaven Annex", phone="(614) 555-0199")
    result = assign_matches([nearby, exact], [make_account()], {}, skip_ids=set())
    assert [m.account["account_id"] for m in result["/communities/exact"]] == ["A1"]
    assert result["/communities/nearby"] == []
```

- [ ] **Step 3: Run them to check they fail**

Run: `python -m pytest tests/test_match.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'bellhaven.match'`

- [ ] **Step 4: Write `bellhaven/match.py`**

```python
"""Score how well a CRM account matches a website location.

Address and administrator carry the most weight: names change when facilities are rebranded,
and phone numbers in this CRM are often stale. A different state rules a match out.
"""
from dataclasses import dataclass, field

from rapidfuzz import fuzz

from .models import Location
from .normalize import norm_name, norm_phone, norm_street, zip5

MATCH_THRESHOLD = 60   # at or above: the same facility
WEAK_THRESHOLD = 40    # in between: shown to the reviewer as a possible match, never acted on
WEIGHTS = {"street": 50, "street_fuzzy": 35, "administrator": 25, "phone": 15,
           "name": 15, "name_partial": 8, "zip": 10, "city": 10}


@dataclass
class Match:
    account: dict
    score: int
    signals: dict[str, int] = field(default_factory=dict)


def score(loc: Location, account: dict, contacts: list[dict]) -> Match:
    if (account.get("billing_state") or "").strip().upper() != loc.state.upper():
        return Match(account, 0, {})
    signals = {}
    same_city = (account.get("billing_city") or "").strip().lower() == loc.city.strip().lower()
    a_street, l_street = norm_street(account.get("billing_street")), norm_street(loc.street)
    if a_street and a_street == l_street:
        signals["street"] = WEIGHTS["street"]
    elif (a_street and l_street and same_city and a_street.split()[0] == l_street.split()[0]
          and fuzz.ratio(a_street, l_street) >= 90):
        signals["street_fuzzy"] = WEIGHTS["street_fuzzy"]
    if zip5(account.get("billing_zip")) and zip5(account.get("billing_zip")) == zip5(loc.zip):
        signals["zip"] = WEIGHTS["zip"]
    if same_city:
        signals["city"] = WEIGHTS["city"]
    phone = norm_phone(account.get("phone"))
    if phone and phone == norm_phone(loc.phone):
        signals["phone"] = WEIGHTS["phone"]
    admin = loc.administrator.strip().lower()
    if admin and any((c.get("name") or "").strip().lower() == admin for c in contacts):
        signals["administrator"] = WEIGHTS["administrator"]
    name_score = fuzz.token_sort_ratio(norm_name(account.get("name")), norm_name(loc.name))
    if name_score >= 90:
        signals["name"] = WEIGHTS["name"]
    elif name_score >= 75:
        signals["name_partial"] = WEIGHTS["name_partial"]
    return Match(account, sum(signals.values()), signals)


def assign_matches(locations, accounts, contacts_by_account, skip_ids) -> dict[str, list[Match]]:
    """Give each account to at most one location (its best). Returns slug -> matches, best first."""
    pairs = []
    for loc in locations:
        for a in accounts:
            if a["account_id"] in skip_ids:
                continue
            m = score(loc, a, contacts_by_account.get(a["account_id"], []))
            if m.score >= WEAK_THRESHOLD:
                pairs.append((m.score, loc.slug, m))
    pairs.sort(key=lambda p: (-p[0], p[1], p[2].account["account_id"]))
    taken, result = set(), {loc.slug: [] for loc in locations}
    for _, slug, m in pairs:
        if m.account["account_id"] not in taken:
            taken.add(m.account["account_id"])
            result[slug].append(m)
    return result
```

- [ ] **Step 5: Run the tests to check they pass**

Run: `python -m pytest tests/test_match.py -q`
Expected: 7 passed

- [ ] **Step 6: Commit**

```powershell
git add bellhaven/match.py tests/factories.py tests/test_match.py
git commit -m "feat: address-first matching with one-to-one assignment"
```

---

### Task 6: Proposals (classification)

**Files:**
- Create: `bellhaven/classify.py`
- Test: `tests/test_classify.py`

Proposal kinds:

| kind | When | Change written on approval |
|---|---|---|
| `update` | Matched account differs from the website (name, street, city, zip, care type, inactive status), or has the wrong parent and the SOP allows a direct move | `PATCH` the changed fields plus `note` |
| `chow` | Wrong parent, and the account has revenue AND AR > 0 | `POST` a new account under Bellhaven, then `PATCH` only `chow_current_account` on the old one |
| `duplicate` | A second account for the same facility as a website community | `PATCH duplicate_of_account`, `status=Inactive`, `note` |
| `create` | A website community with no matching account | `POST` a new account under Bellhaven |
| `not_on_website` | Bellhaven account not on the website. If another operator's account is at the same address, the note names it but nothing is linked (decisions #1 and #2). | `PATCH status=Needs Review` plus `note` |

- [ ] **Step 1: Write the failing tests**

```python
from bellhaven.classify import build_proposals
from tests.factories import BH, CEDAR, HARBORVIEW, MILLSTONE, make_account, make_location, parent_accounts

TODAY = "2026-11-25"


def run(locations, accounts, contacts=()):
    return build_proposals(locations, parent_accounts() + accounts, list(contacts), TODAY)


def admin(account_id, name="Pat Admin"):
    return {"account_id": account_id, "name": name, "is_active": True}


def by_kind(proposals):
    out = {}
    for p in proposals:
        out.setdefault(p.kind, []).append(p)
    return out


def test_clean_match_produces_nothing():
    assert run([make_location()], [make_account()]) == []


def test_formatting_only_address_difference_produces_nothing():
    assert run([make_location(street="100 Main Street")], [make_account(billing_street="100 Main St.")]) == []


def test_renamed_facility_gets_name_update():
    [p] = run([make_location(name="Bellhaven of Chagrin Falls")],
              [make_account(name="Riverbend Manor Care Center")], [admin("A1")])
    assert p.kind == "update"
    assert p.changes["name"] == "Bellhaven of Chagrin Falls"
    assert p.before["name"] == "Riverbend Manor Care Center"
    assert p.changes["note"].startswith(f"[{TODAY}]")


def test_wrong_parent_without_outstanding_ar_is_reparented():
    [p] = run([make_location()], [make_account(parent_id=HARBORVIEW, lifetime_revenue=47000, outstanding_ar=0)])
    assert p.kind == "update" and p.changes["parent_id"] == BH


def test_wrong_parent_with_revenue_and_ar_uses_chow():
    [p] = run([make_location()], [make_account(parent_id=CEDAR, lifetime_revenue=84000, outstanding_ar=12400)])
    assert p.kind == "chow" and p.account_id == "A1"
    assert p.changes["new_account"]["parent_id"] == BH
    assert set(p.changes) == {"new_account"}  # nothing else on the old account changes


def test_already_under_bellhaven_with_ar_is_not_touched():
    assert run([make_location()], [make_account(lifetime_revenue=101000, outstanding_ar=9000)]) == []


def test_duplicates_keep_the_copy_with_the_administrator():
    accounts = [make_account(account_id="A1", phone="(330) 383-5991"), make_account(account_id="A2")]
    ps = by_kind(run([make_location()], accounts, [admin("A2")]))
    [dup] = ps["duplicate"]
    assert dup.account_id == "A1"
    assert dup.changes["duplicate_of_account"] == "A2" and dup.changes["status"] == "Inactive"
    assert "update" not in ps


def test_old_owner_copy_is_duplicate_of_bellhaven_copy():
    accounts = [make_account(account_id="A1"),
                make_account(account_id="A2", name="Harborview Shores of Testville", parent_id=HARBORVIEW)]
    [dup] = run([make_location()], accounts)
    assert dup.account_id == "A2" and dup.changes["duplicate_of_account"] == "A1"


def test_location_without_account_is_created_under_bellhaven():
    [p] = run([make_location(name="Bellhaven of Batavia", street="2000 Hospital Dr", city="Batavia", zip="45103")], [])
    assert p.kind == "create" and p.changes["parent_id"] == BH and p.changes["name"] == "Bellhaven of Batavia"


def test_same_name_in_other_state_is_not_matched():
    loc = make_location(name="Amberly Manor", street="4390 Darrow Rd", city="Hudson", zip="44236")
    other = make_account(name="Amberly Manor", parent_id=HARBORVIEW, billing_street="918 S Nevada Ave",
                         billing_city="Colorado Springs", billing_state="CO", billing_zip="80903")
    assert [p.kind for p in run([loc], [other])] == ["create"]


def test_bellhaven_account_missing_from_website_needs_review():
    [p] = run([], [make_account(name="Bellhaven of Coldwater")])
    assert p.kind == "not_on_website" and p.changes["status"] == "Needs Review"


def test_possibly_sold_facility_is_flagged_not_linked():
    # decision #2: same address under another operator is a clue, not proof, so nothing is linked automatically
    old = make_account(account_id="A1", name="Bellhaven of Sandusky", lifetime_revenue=130000, outstanding_ar=5200)
    new_owner = make_account(account_id="A2", name="Millstone Care of Sandusky", parent_id=MILLSTONE)
    [p] = run([], [old, new_owner])
    assert p.kind == "not_on_website" and p.changes["status"] == "Needs Review"
    assert "chow_current_account" not in p.changes and "duplicate_of_account" not in p.changes
    assert "Millstone Care of Sandusky" in p.changes["note"]


def test_resolved_accounts_are_left_alone():
    accounts = [make_account(account_id="A1", chow_current_account="NEW1", parent_id=CEDAR,
                             lifetime_revenue=1, outstanding_ar=1),
                make_account(account_id="NEW1", created_by_candidate=True)]
    assert run([make_location()], accounts) == []


def test_keys_do_not_depend_on_the_date():
    loc, acct = make_location(name="Bellhaven of New Name"), make_account()
    k1 = build_proposals([loc], parent_accounts() + [acct], [], "2026-11-25")[0].key
    k2 = build_proposals([loc], parent_accounts() + [acct], [], "2026-11-26")[0].key
    assert k1 == k2
```

- [ ] **Step 2: Run them to check they fail**

Run: `python -m pytest tests/test_classify.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'bellhaven.classify'`

- [ ] **Step 3: Write `bellhaven/classify.py`**

```python
"""Turn website locations and CRM accounts into proposals for a reviewer. Pure: no network, no database."""
import hashlib
import json
from dataclasses import asdict, dataclass

from .config import BELLHAVEN_PARENT_NAME, NOT_ON_WEBSITE_STATUS
from .match import MATCH_THRESHOLD, Match, assign_matches
from .models import Location
from .normalize import crm_care_types, is_po_box, norm_street, same_text, zip5
from .sop import amount, must_preserve_old_account

CRM_FIELDS = ("account_id", "name", "parent_id", "parent_name", "billing_street", "billing_city",
              "billing_state", "billing_zip", "care_type", "status", "phone", "lifetime_revenue",
              "outstanding_ar", "chow_current_account", "duplicate_of_account", "note")


@dataclass
class Proposal:
    kind: str          # update | chow | duplicate | create | not_on_website
    title: str
    account_id: str    # account to change; "" for create
    slug: str          # website community it came from; "" when the account is not on the website
    changes: dict
    before: dict       # current CRM values of the changed fields; checked again just before writing
    evidence: dict
    confidence: str = "high"

    @property
    def key(self) -> str:
        """Same decision -> same key on any day. Notes contain the date, so they are left out."""
        stable = {k: v for k, v in self.changes.items() if k != "note"}
        if isinstance(stable.get("new_account"), dict):
            stable["new_account"] = {k: v for k, v in stable["new_account"].items() if k != "note"}
        raw = json.dumps([self.kind, self.account_id, self.slug, stable], sort_keys=True)
        return hashlib.sha256(raw.encode()).hexdigest()[:20]


def parent_ids(accounts) -> set[str]:
    referenced = {a["parent_id"] for a in accounts if a.get("parent_id")}
    return referenced | {a["account_id"] for a in accounts if a["name"].endswith("(Parent Account)")}


def bellhaven_parent_id(accounts) -> str:
    ids = [a["account_id"] for a in accounts if a["name"] == BELLHAVEN_PARENT_NAME]
    if len(ids) != 1:
        raise ValueError(f"expected exactly one {BELLHAVEN_PARENT_NAME!r}, found {len(ids)}")
    return ids[0]


def already_resolved(account) -> bool:
    """Marked as a duplicate, or kept for billing after a change of ownership: leave it alone."""
    return bool(account.get("duplicate_of_account") or account.get("chow_current_account"))


def _note(existing: str, text: str, today: str, url: str = "") -> str:
    entry = f"[{today}] {text}" + (f" Source: {url}" if url else "")
    return f"{existing} | {entry}" if existing else entry


def _crm_view(account) -> dict:
    return {f: account.get(f) for f in CRM_FIELDS}


def _evidence(loc: Location | None, m: Match | None, account: dict, reasons: list[str]) -> dict:
    ev = {"crm": _crm_view(account), "reasons": list(reasons),
          "sop": {"lifetime_revenue": account.get("lifetime_revenue"),
                  "outstanding_ar": account.get("outstanding_ar"),
                  "preserve_old_account": must_preserve_old_account(account)}}
    if loc:
        ev["website"] = asdict(loc)
        if not loc.in_directory:
            ev["reasons"].append("Linked from the homepage but missing from the community directory.")
    if m:
        ev["score"], ev["signals"] = m.score, m.signals
    return ev


def _new_account(loc: Location, parent_id: str, note: str) -> dict:
    care = crm_care_types(loc.care_offerings)
    return {"name": loc.name, "parent_id": parent_id, "billing_street": loc.street, "billing_city": loc.city,
            "billing_state": loc.state, "billing_zip": loc.zip, "phone": loc.phone,
            "care_type": care[0] if care else "", "status": "Active", "note": note}


def _field_changes(loc: Location, a: dict) -> tuple[dict, list[str]]:
    """The website is the source of truth. Address formatting differences (Rd vs Road) are not changes."""
    changes, reasons = {}, []
    if not same_text(a["name"], loc.name):
        changes["name"] = loc.name
        reasons.append(f"Name differs: CRM has '{a['name']}', website has '{loc.name}'.")
    if norm_street(a["billing_street"]) != norm_street(loc.street):
        changes["billing_street"] = loc.street
        crm_street = "a PO Box" if is_po_box(a["billing_street"]) else f"'{a['billing_street']}'"
        reasons.append(f"Street differs: CRM has {crm_street}, website has '{loc.street}'.")
    if not same_text(a["billing_city"], loc.city):
        changes["billing_city"] = loc.city
        reasons.append(f"City differs: CRM has '{a['billing_city']}', website has '{loc.city}'.")
    if zip5(a["billing_zip"]) != zip5(loc.zip):
        changes["billing_zip"] = loc.zip
        reasons.append(f"Zip differs: CRM has '{a['billing_zip']}', website has '{loc.zip}'.")
    wanted = crm_care_types(loc.care_offerings)
    if wanted and a["care_type"] not in wanted:
        changes["care_type"] = wanted[0]
        reasons.append(f"Care type: CRM has '{a['care_type'] or 'blank'}', website lists {', '.join(loc.care_offerings)}.")
    if a["status"] != "Active":
        changes["status"] = "Active"
        reasons.append(f"Status is '{a['status']}' but the community is listed on the website.")
    return changes, reasons


def choose_survivor(strong: list[Match], bh: str) -> tuple[Match, str]:
    def rank(m):
        a = m.account
        return (amount(a.get("lifetime_revenue")) > 0, a["parent_id"] == bh, "administrator" in m.signals,
                "phone" in m.signals, a["status"] == "Active", m.score)

    best = max(sorted(strong, key=lambda m: m.account["account_id"]), key=rank)
    labels = ["has billing history", "is already under Bellhaven", "has the website's administrator as a contact",
              "has the website's phone number", "is Active", f"has the highest match score ({best.score})"]
    others = [m for m in strong if m is not best]
    why = "tied, so the lowest account id was kept"
    if others:
        for i, label in enumerate(labels):
            if rank(best)[i] > max(rank(o)[i] for o in others):
                why = label
                break
    return best, why


def _survivor_proposals(loc: Location, m: Match, bh: str, today: str) -> list[Proposal]:
    a = m.account
    changes, reasons = _field_changes(loc, a)
    wrong_parent = a["parent_id"] != bh
    if wrong_parent and must_preserve_old_account(a):
        reasons.append(
            f"Parent is '{a['parent_name'] or 'none'}' but the website lists it as Bellhaven. It has lifetime revenue "
            f"{a['lifetime_revenue']} and outstanding AR {a['outstanding_ar']}, so the billing SOP applies: leave this "
            "account as it is, create a new account under Bellhaven and point chow_current_account at it.")
        new = _new_account(loc, bh, _note("", f"New account after change of ownership; old account {a['account_id']} "
                                              "kept for billing.", today, loc.url))
        return [Proposal("chow", f"Change of ownership: {loc.name}", a["account_id"], loc.slug, {"new_account": new},
                         {"parent_id": a["parent_id"], "chow_current_account": a["chow_current_account"]},
                         _evidence(loc, m, a, reasons))]
    if wrong_parent:
        changes["parent_id"] = bh
        reasons.append(f"Parent is '{a['parent_name'] or 'none'}' but the website lists it as Bellhaven. No revenue "
                       "history or no outstanding AR, so it is re-parented directly.")
    if not changes:
        return []
    changes["note"] = _note(a["note"], f"Updated {', '.join(changes)} to match the Bellhaven website.", today, loc.url)
    before = {k: a.get(k) for k in changes}
    return [Proposal("update", f"Update {a['name']}", a["account_id"], loc.slug, changes, before,
                     _evidence(loc, m, a, reasons))]


def _duplicate(loc: Location, m: Match, survivor: Match, why: str, today: str) -> Proposal:
    a, s = m.account, survivor.account
    reasons = [f"Same facility as '{s['name']}' ({s['account_id']}), which is kept because it {why}."]
    confidence = "high"
    if amount(a.get("lifetime_revenue")) > 0:
        confidence = "low"
        reasons.append("This copy also has billing history; check with billing before marking it inactive.")
    if a["care_type"] and s["care_type"] and a["care_type"] != s["care_type"]:
        confidence = "low"
        reasons.append(f"Care types differ ({a['care_type']} vs {s['care_type']}); it could be a separate building.")
    changes = {"duplicate_of_account": s["account_id"], "status": "Inactive",
               "note": _note(a["note"], f"Duplicate of {s['account_id']} ({s['name']}).", today, loc.url)}
    return Proposal("duplicate", f"Duplicate: '{a['name']}' -> '{s['name']}'", a["account_id"], loc.slug, changes,
                    {k: a.get(k) for k in changes}, _evidence(loc, m, a, reasons), confidence)


def _create(loc: Location, bh: str, weak: list[Match], today: str) -> Proposal:
    reasons = ["No CRM account matches this community's address, phone or administrator."]
    confidence = "high"
    if weak:
        confidence = "low"
        reasons.append("Possible weak matches: " + "; ".join(
            f"'{m.account['name']}' ({m.account['account_id']}, score {m.score})" for m in weak))
    if not loc.in_directory:
        reasons.append("Linked from the homepage but missing from the community directory.")
    payload = _new_account(loc, bh, _note("", "Created from the Bellhaven website; no existing CRM account found.",
                                          today, loc.url))
    evidence = {"website": asdict(loc), "reasons": reasons,
                "candidates": [{"account": _crm_view(m.account), "score": m.score, "signals": m.signals} for m in weak]}
    return Proposal("create", f"Create {loc.name}", "", loc.slug, payload, {}, evidence, confidence)


def _same_address(a: dict, b: dict) -> bool:
    street = norm_street(a["billing_street"])
    return bool(street) and street == norm_street(b["billing_street"]) and zip5(a["billing_zip"]) == zip5(b["billing_zip"])


def _not_on_website(accounts, bh: str, seen: set, skip: set, today: str) -> list[Proposal]:
    out = []
    for a in accounts:
        if a["parent_id"] != bh or a["account_id"] in seen or a["account_id"] in skip or a["status"] != "Active":
            continue
        reasons = ["Under the Bellhaven parent but not listed anywhere on the Bellhaven website."]
        text = "Not listed on the Bellhaven website; closed or sold, new owner unknown."
        # Decision #2: flag for review, never link or merge automatically. The CRM alone can't prove who owns it now.
        twin = next((b for b in accounts if b["account_id"] != a["account_id"] and b["parent_id"] not in ("", bh)
                     and b["account_id"] not in skip and _same_address(a, b)), None)
        if twin:
            reasons.append(f"'{twin['name']}' ({twin['account_id']}) is at the same address under "
                           f"'{twin['parent_name']}', so the facility may have changed owner. Not linked "
                           "automatically: confirm the new owner first.")
            text = (f"Not listed on the Bellhaven website. Possibly sold: '{twin['name']}' ({twin['account_id']}) "
                    f"under '{twin['parent_name']}' is at the same address.")
        if must_preserve_old_account(a):
            reasons.append(f"Lifetime revenue {a['lifetime_revenue']} and outstanding AR {a['outstanding_ar']}: if it "
                           "did change owner, the billing SOP applies (keep this account and link a new one with "
                           "chow_current_account).")
        changes = {"status": NOT_ON_WEBSITE_STATUS, "note": _note(a["note"], text, today)}
        out.append(Proposal("not_on_website", f"Not on website: {a['name']}", a["account_id"], "", changes,
                            {k: a.get(k) for k in changes}, _evidence(None, None, a, reasons)))
    return out


def build_proposals(locations, accounts, contacts, today: str) -> list[Proposal]:
    bh = bellhaven_parent_id(accounts)
    skip = parent_ids(accounts) | {a["account_id"] for a in accounts if already_resolved(a)}
    contacts_by_account = {}
    for c in contacts:
        if c.get("is_active", True):
            contacts_by_account.setdefault(c["account_id"], []).append(c)
    matches = assign_matches(locations, accounts, contacts_by_account, skip)
    proposals, seen = [], set()
    for loc in locations:
        found = matches[loc.slug]
        seen |= {m.account["account_id"] for m in found}
        strong = [m for m in found if m.score >= MATCH_THRESHOLD]
        weak = [m for m in found if m.score < MATCH_THRESHOLD]
        if not strong:
            proposals.append(_create(loc, bh, weak, today))
            continue
        survivor, why = choose_survivor(strong, bh)
        proposals += _survivor_proposals(loc, survivor, bh, today)
        proposals += [_duplicate(loc, m, survivor, why, today) for m in strong if m is not survivor]
    return proposals + _not_on_website(accounts, bh, seen, skip, today)
```

- [ ] **Step 4: Run the tests to check they pass**

Run: `python -m pytest tests/test_classify.py -q`
Expected: 14 passed

- [ ] **Step 5: Commit**

```powershell
git add bellhaven/classify.py tests/test_classify.py
git commit -m "feat: build reviewable proposals including CHOW and duplicates"
```

---

### Task 7: Proposal store (SQLite)

**Files:**
- Create: `bellhaven/store.py`
- Test: `tests/test_store.py`

- [ ] **Step 1: Write the failing tests**

```python
import json
import sqlite3

import pytest

from bellhaven import store
from bellhaven.classify import Proposal


def proposal(**overrides):
    values = dict(kind="update", title="Update X", account_id="A1", slug="/communities/x",
                  changes={"name": "X", "note": "[2026-11-25] n"}, before={"name": "Old", "note": ""},
                  evidence={"reasons": []})
    values.update(overrides)
    return Proposal(**values)


def test_same_proposal_is_stored_once():
    conn = store.connect(":memory:")
    assert store.add_proposals(conn, store.start_run(conn), [proposal()]) == (1, 0)
    assert store.add_proposals(conn, store.start_run(conn), [proposal(changes={"name": "X", "note": "[2026-11-26] n"})]) == (0, 1)


def test_rejected_proposal_is_not_proposed_again():
    conn = store.connect(":memory:")
    p = proposal()
    store.add_proposals(conn, store.start_run(conn), [p])
    store.set_status(conn, p.key, "rejected", "wrong match")
    store.add_proposals(conn, store.start_run(conn), [p])
    assert [r["status"] for r in store.list_proposals(conn)] == ["rejected"]


def test_failed_proposal_is_refreshed_and_back_in_the_queue():
    conn = store.connect(":memory:")
    p = proposal()
    store.add_proposals(conn, store.start_run(conn), [p])
    store.set_status(conn, p.key, "failed", None, {"stale": {}})
    store.add_proposals(conn, store.start_run(conn), [proposal(before={"name": "Changed", "note": ""})])
    row = store.get(conn, p.key)
    assert row["status"] == "pending" and json.loads(row["before"])["name"] == "Changed"


def test_applied_proposal_that_comes_back_is_reopened():
    # after a correct apply the next run no longer produces it; producing it again means the CRM changed back
    conn = store.connect(":memory:")
    p = proposal()
    store.add_proposals(conn, store.start_run(conn), [p])
    store.set_status(conn, p.key, "applied", None, {"updated": "A1"})
    assert store.add_proposals(conn, store.start_run(conn), [p]) == (0, 1)
    assert store.get(conn, p.key)["status"] == "pending"


def test_undecided_proposal_that_disappears_is_superseded():
    conn = store.connect(":memory:")
    p = proposal()
    store.add_proposals(conn, store.start_run(conn), [p])
    assert store.supersede_missing(conn, []) == 1
    assert store.get(conn, p.key)["status"] == "superseded"


def test_database_is_tied_to_one_crm(tmp_path):
    # Proposal keys are the same for mock and live, so mixing databases would make the live run skip real fixes.
    db = tmp_path / "state.db"
    store.connect(db, crm_base="http://localhost:8001/api/v1").close()
    with pytest.raises(RuntimeError, match="separate database"):
        store.connect(db, crm_base="https://live.test/api/v1")


def test_foreign_keys_are_enforced():
    conn = store.connect(":memory:")
    with pytest.raises(sqlite3.IntegrityError):
        store.add_proposals(conn, 999, [proposal()])  # run 999 doesn't exist


def test_change_log_keeps_only_fields_that_changed():
    conn = store.connect(":memory:")
    p = proposal()
    store.add_proposals(conn, store.start_run(conn), [p])
    call = store.log_call(conn, p.key, "PATCH", "/accounts/A1", {"name": "X", "status": "Active"}, {})
    store.log_changes(conn, call, p.key, "A1", "update", ["name", "status"],
                      old={"name": "Old", "status": "Active"}, new={"name": "X", "status": "Active"})
    [row] = store.change_history(conn, account_id="A1")
    assert (row["field"], row["old_value"], row["new_value"], row["title"]) == ("name", "Old", "X", "Update X")
```

- [ ] **Step 2: Run them to check they fail**

Run: `python -m pytest tests/test_store.py -q`
Expected: FAIL with `ImportError: cannot import name 'store'`

- [ ] **Step 3: Write `bellhaven/store.py`**

```python
"""SQLite state (relational, foreign keys enforced): runs -> proposals -> api_log -> change_log.

Makes re-runs safe (decided proposals are never proposed again) and keeps a field-level audit trail
of every change made through the CRM API.
"""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    stats       TEXT
);

CREATE TABLE IF NOT EXISTS proposals (
    key        TEXT PRIMARY KEY,
    run_id     INTEGER NOT NULL REFERENCES runs(id),
    kind       TEXT NOT NULL,
    title      TEXT NOT NULL,
    account_id TEXT NOT NULL,
    slug       TEXT NOT NULL,
    changes    TEXT NOT NULL,
    before     TEXT NOT NULL,
    evidence   TEXT NOT NULL,
    confidence TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'pending'
               CHECK (status IN ('pending', 'applying', 'applied', 'rejected', 'failed', 'superseded')),
    created_at TEXT NOT NULL,
    decided_at TEXT,
    comment    TEXT,
    result     TEXT
);

CREATE TABLE IF NOT EXISTS api_log (
    id           INTEGER PRIMARY KEY,
    proposal_key TEXT NOT NULL REFERENCES proposals(key),
    method       TEXT NOT NULL,
    path         TEXT NOT NULL,
    body         TEXT NOT NULL,
    response     TEXT NOT NULL,
    at           TEXT NOT NULL
);

-- One row per field changed through the API: what it was just before the write, and what the CRM stored.
CREATE TABLE IF NOT EXISTS change_log (
    id           INTEGER PRIMARY KEY,
    api_call_id  INTEGER NOT NULL REFERENCES api_log(id),
    proposal_key TEXT NOT NULL REFERENCES proposals(key),
    account_id   TEXT NOT NULL,
    action       TEXT NOT NULL CHECK (action IN ('create', 'update')),
    field        TEXT NOT NULL,
    old_value    TEXT,
    new_value    TEXT,
    changed_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS change_log_by_account ON change_log (account_id);

CREATE VIEW IF NOT EXISTS change_history AS
SELECT c.id, c.changed_at, c.account_id, c.action, c.field, c.old_value, c.new_value,
       p.kind, p.title, p.comment AS reviewer_comment, c.proposal_key, c.api_call_id
FROM change_log c JOIN proposals p ON p.key = c.proposal_key
ORDER BY c.id;
"""
UNDECIDED = ("pending", "failed")
REOPENABLE = UNDECIDED + ("applied",)  # produced again after being applied = the CRM changed back


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path, crm_base: str | None = None) -> sqlite3.Connection:
    """Open the database. With crm_base, the database is tied to that CRM on first use and refuses any other,
    so decisions made against the mock can never make the live run skip real fixes (or the reverse)."""
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")  # SQLite leaves them off unless asked, per connection
    conn.executescript(SCHEMA)
    if crm_base:
        row = conn.execute("SELECT value FROM meta WHERE key='crm_base'").fetchone()
        if row is None:
            conn.execute("INSERT INTO meta (key, value) VALUES ('crm_base', ?)", (crm_base,))
            conn.commit()
        elif row["value"] != crm_base:
            conn.close()
            raise RuntimeError(f"{path} holds decisions for {row['value']}, not {crm_base}. "
                               "Use a separate database per CRM (set BH_DB_PATH).")
    return conn


def start_run(conn) -> int:
    cur = conn.execute("INSERT INTO runs (started_at) VALUES (?)", (_now(),))
    conn.commit()
    return cur.lastrowid


def finish_run(conn, run_id: int, stats: dict) -> None:
    conn.execute("UPDATE runs SET finished_at=?, stats=? WHERE id=?", (_now(), json.dumps(stats), run_id))
    conn.commit()


def add_proposals(conn, run_id: int, proposals) -> tuple[int, int]:
    """Insert new proposals. Rejected ones are never touched again. Undecided ones are refreshed with today's
    CRM values, so the freshness check before writing uses current data. An applied one that is produced again
    goes back to pending: after a correct apply the next run no longer produces it, so seeing it again means the
    CRM changed back. Returns (new, already_known)."""
    new = known = 0
    for p in proposals:
        values = (json.dumps(p.changes), json.dumps(p.before), json.dumps(p.evidence), p.confidence, run_id)
        row = conn.execute("SELECT status FROM proposals WHERE key=?", (p.key,)).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO proposals (key, kind, title, account_id, slug, changes, before, evidence, confidence,"
                " run_id, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (p.key, p.kind, p.title, p.account_id, p.slug, *values, _now()))
            new += 1
            continue
        known += 1
        if row["status"] in REOPENABLE:
            conn.execute("UPDATE proposals SET changes=?, before=?, evidence=?, confidence=?, run_id=?,"
                         " status='pending' WHERE key=?", (*values, p.key))
    conn.commit()
    return new, known


def supersede_missing(conn, current_keys) -> int:
    """Undecided proposals that today's run no longer produces (fixed elsewhere, or the data changed)."""
    keys = set(current_keys)
    stale = [r["key"] for r in conn.execute("SELECT key, status FROM proposals")
             if r["status"] in UNDECIDED and r["key"] not in keys]
    conn.executemany("UPDATE proposals SET status='superseded', decided_at=? WHERE key=?",
                     [(_now(), k) for k in stale])
    conn.commit()
    return len(stale)


def list_proposals(conn, status: str | None = None):
    if status:
        return conn.execute("SELECT * FROM proposals WHERE status=? ORDER BY kind, title", (status,)).fetchall()
    return conn.execute("SELECT * FROM proposals ORDER BY kind, title").fetchall()


def get(conn, key: str):
    return conn.execute("SELECT * FROM proposals WHERE key=?", (key,)).fetchone()


def set_status(conn, key: str, status: str, comment: str | None = None, result: dict | None = None) -> None:
    conn.execute("UPDATE proposals SET status=?, decided_at=?, comment=COALESCE(?, comment),"
                 " result=COALESCE(?, result) WHERE key=?",
                 (status, _now(), comment, json.dumps(result) if result is not None else None, key))
    conn.commit()


def log_call(conn, key: str, method: str, path: str, body: dict, response: dict) -> int:
    cur = conn.execute("INSERT INTO api_log (proposal_key, method, path, body, response, at) VALUES (?,?,?,?,?,?)",
                       (key, method, path, json.dumps(body), json.dumps(response), _now()))
    conn.commit()
    return cur.lastrowid


def _text(value) -> str | None:
    if value is None:
        return None
    return value if isinstance(value, str) else json.dumps(value)


def log_changes(conn, api_call_id: int, key: str, account_id: str, action: str,
                fields, old: dict, new: dict) -> int:
    """Record each field the API call changed. For updates, fields whose value didn't change are skipped."""
    rows = [(api_call_id, key, account_id, action, f, _text(old.get(f)), _text(new.get(f)), _now())
            for f in fields if action == "create" or old.get(f) != new.get(f)]
    conn.executemany("INSERT INTO change_log (api_call_id, proposal_key, account_id, action, field, old_value,"
                     " new_value, changed_at) VALUES (?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    return len(rows)


def change_history(conn, account_id: str | None = None, proposal_key: str | None = None):
    sql, args = "SELECT * FROM change_history WHERE 1=1", []
    if account_id:
        sql, args = sql + " AND account_id=?", args + [account_id]
    if proposal_key:
        sql, args = sql + " AND proposal_key=?", args + [proposal_key]
    return conn.execute(sql, args).fetchall()


def counts(conn) -> dict:
    return {r[0]: r[1] for r in conn.execute("SELECT status, COUNT(*) FROM proposals GROUP BY status")}
```

- [ ] **Step 4: Run the tests to check they pass**

Run: `python -m pytest tests/test_store.py -q`
Expected: 8 passed

- [ ] **Step 5: Commit**

```powershell
git add bellhaven/store.py tests/test_store.py
git commit -m "feat: SQLite proposal store with idempotent inserts"
```

---

### Task 8: Apply approved proposals

**Files:**
- Create: `bellhaven/apply.py`
- Test: `tests/test_apply.py`

- [ ] **Step 1: Write the failing tests**

```python
import pytest

from bellhaven import store
from bellhaven.apply import StaleProposal, apply_proposal
from bellhaven.classify import build_proposals
from tests.factories import BH, CEDAR, FakeCrm, make_account, make_location, parent_accounts


def setup(accounts, locations):
    crm = FakeCrm(parent_accounts() + accounts)
    [p] = build_proposals(locations, list(crm.accounts.values()), [], "2026-11-25")
    conn = store.connect(":memory:")
    store.add_proposals(conn, store.start_run(conn), [p])
    return crm, conn, store.get(conn, p.key)


def test_update_is_written_and_logged():
    crm, conn, row = setup([make_account(name="Old Name")], [make_location()])
    result = apply_proposal(conn, crm, row)
    assert crm.accounts["A1"]["name"] == "Bellhaven of Testville"
    assert result["mismatches"] == {}
    assert conn.execute("SELECT COUNT(*) FROM api_log").fetchone()[0] == 1


def test_stale_proposal_writes_nothing():
    crm, conn, row = setup([make_account(name="Old Name")], [make_location()])
    crm.accounts["A1"]["name"] = "Someone Else Renamed It"
    with pytest.raises(StaleProposal):
        apply_proposal(conn, crm, row)
    assert crm.writes == []


def test_chow_creates_new_account_and_only_links_the_old_one():
    crm, conn, row = setup([make_account(parent_id=CEDAR, lifetime_revenue=84000, outstanding_ar=12400)],
                           [make_location()])
    old_before = dict(crm.accounts["A1"])
    result = apply_proposal(conn, crm, row)
    new, old = crm.accounts[result["created"]], crm.accounts["A1"]
    assert new["parent_id"] == BH
    assert old["chow_current_account"] == new["account_id"]
    unchanged = lambda acct: {k: v for k, v in acct.items() if k != "chow_current_account"}
    assert unchanged(old) == unchanged(old_before)


def test_create_is_not_repeated_after_a_retry():
    crm, conn, row = setup([], [make_location(name="Bellhaven of Batavia", zip="45103")])
    apply_proposal(conn, crm, row)
    apply_proposal(conn, crm, row)
    assert [w[0] for w in crm.writes] == ["POST"]
    assert conn.execute("SELECT COUNT(DISTINCT api_call_id) FROM change_log").fetchone()[0] == 1


def test_update_is_recorded_field_by_field():
    crm, conn, row = setup([make_account(name="Old Name")], [make_location()])
    apply_proposal(conn, crm, row)
    history = store.change_history(conn, account_id="A1")
    assert ("name", "Old Name", "Bellhaven of Testville") in {(h["field"], h["old_value"], h["new_value"]) for h in history}
    assert {h["action"] for h in history} == {"update"}


def test_chow_logs_the_new_account_and_the_link():
    crm, conn, row = setup([make_account(parent_id=CEDAR, lifetime_revenue=84000, outstanding_ar=12400)],
                           [make_location()])
    result = apply_proposal(conn, crm, row)
    [link] = store.change_history(conn, account_id="A1")  # the old account: only the link changed
    assert (link["field"], link["old_value"], link["new_value"]) == ("chow_current_account", "", result["created"])
    created_fields = {h["field"] for h in store.change_history(conn, account_id=result["created"])}
    assert {"name", "parent_id", "billing_street"} <= created_fields
```

- [ ] **Step 2: Run them to check they fail**

Run: `python -m pytest tests/test_apply.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'bellhaven.apply'`

- [ ] **Step 3: Write `bellhaven/apply.py`**

```python
"""Write one approved proposal to the CRM. This is the only code that calls CRM write methods."""
import json

from . import store


class StaleProposal(Exception):
    """The CRM record changed after the proposal was made. Re-run the pipeline instead of writing."""


def _fetch_fresh(client, account_id: str, before: dict) -> dict:
    """Re-read the account just before writing. Its values are also the 'old' side of the change log."""
    current = client.get_account(account_id)
    drift = {f: {"expected": v, "actual": current.get(f)} for f, v in before.items() if current.get(f) != v}
    if drift:
        raise StaleProposal(drift)
    return current


def _mismatches(sent: dict, returned: dict) -> dict:
    return {k: {"sent": v, "returned": returned.get(k)} for k, v in sent.items() if returned.get(k) != v}


def _create_once(conn, client, key: str, payload: dict) -> dict:
    """Reuse an account already created for this community (for example after a timeout) instead of a second POST."""
    for a in client.list_accounts(zip=payload["billing_zip"]):
        if a.get("created_by_candidate") and a["name"] == payload["name"] and a["parent_id"] == payload["parent_id"]:
            return a
    created = client.create_account(payload)
    call_id = store.log_call(conn, key, "POST", "/accounts", payload, created)
    store.log_changes(conn, call_id, key, created["account_id"], "create", payload, {}, created)
    return created


def _patch(conn, client, key: str, current: dict, body: dict) -> dict:
    account_id = current["account_id"]
    updated = client.update_account(account_id, body)
    call_id = store.log_call(conn, key, "PATCH", f"/accounts/{account_id}", body, updated)
    store.log_changes(conn, call_id, key, account_id, "update", body, current, updated)
    return updated


def apply_proposal(conn, client, row) -> dict:
    kind, key, account_id = row["kind"], row["key"], row["account_id"]
    changes, before = json.loads(row["changes"]), json.loads(row["before"])
    if kind == "create":
        created = _create_once(conn, client, key, changes)
        return {"created": created["account_id"], "mismatches": _mismatches(changes, created)}
    current = _fetch_fresh(client, account_id, before)
    if kind == "chow":
        new = _create_once(conn, client, key, changes["new_account"])
        link = {"chow_current_account": new["account_id"]}
        old = _patch(conn, client, key, current, link)
        return {"created": new["account_id"], "old_account": account_id, "mismatches": _mismatches(link, old)}
    updated = _patch(conn, client, key, current, changes)
    return {"updated": account_id, "mismatches": _mismatches(changes, updated)}
```

- [ ] **Step 4: Run the tests to check they pass**

Run: `python -m pytest tests/test_apply.py -q`
Expected: 6 passed

- [ ] **Step 5: Commit**

```powershell
git add bellhaven/apply.py tests/test_apply.py
git commit -m "feat: apply approved proposals with freshness check and create-once"
```

---

### Task 9: Daily pipeline command

**Files:**
- Create: `bellhaven/pipeline.py`
- Test: `tests/test_pipeline.py`

- [ ] **Step 1: Write the failing test**

```python
from bellhaven import pipeline, store
from tests.factories import FakeCrm, make_account, make_location, parent_accounts


def test_pipeline_never_writes_and_reruns_are_safe():
    crm = FakeCrm(parent_accounts() + [make_account(name="Old Name")])
    conn = store.connect(":memory:")
    scrape = lambda: [make_location()]
    first = pipeline.run(conn, crm, scrape, "2026-11-25")
    second = pipeline.run(conn, crm, scrape, "2026-11-26")
    assert first["new"] == 1
    assert second["new"] == 0 and second["already_known"] == 1
    assert crm.writes == []
```

- [ ] **Step 2: Run it to check it fails**

Run: `python -m pytest tests/test_pipeline.py -q`
Expected: FAIL with `ImportError: cannot import name 'pipeline'`

- [ ] **Step 3: Write `bellhaven/pipeline.py`**

```python
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
```

- [ ] **Step 4: Run the whole suite**

Run: `python -m pytest -q`
Expected: all pass

- [ ] **Step 5: Commit**

```powershell
git add bellhaven/pipeline.py tests/test_pipeline.py
git commit -m "feat: daily pipeline command"
```

---

### Task 10: Mock CRM (test environment)

The graded result is the end state of your sandbox copy, so nothing can be practised there. This task builds a local stand-in for the CRM API, filled from a read-only snapshot of your sandbox, so the pipeline, the app and every kind of write can be run as often as you like. The mock is deliberately **stricter** than the real API (it rejects unknown fields, bad statuses and references to missing accounts), so it catches bugs rather than hiding them. What it can't confirm is the real API's exact create/update format; Task 16 handles that with one careful first write.

**Files:**
- Create: `mock_crm/__init__.py` (empty), `mock_crm/server.py`, `mock_crm/snapshot.py`
- Test: `tests/test_mock_crm.py`

- [ ] **Step 1: Write the failing tests.** These run the real `CrmClient` against the mock through FastAPI's `TestClient`, so the client is tested too.

```python
import pytest
from fastapi.testclient import TestClient

from bellhaven.crm import CrmClient, CrmError
from mock_crm.server import create_app
from tests.factories import BH, HARBORVIEW, make_account, parent_accounts


def mock(extra_accounts=0):
    accounts = parent_accounts() + [make_account()] + [
        make_account(account_id=f"X{i}", name=f"Other {i}", parent_id=HARBORVIEW) for i in range(extra_accounts)]
    contacts = [{"contact_id": "C1", "account_id": "A1", "name": "Pat Admin", "title": "Administrator",
                 "email": "", "phone": "", "is_active": True}]
    http = TestClient(create_app({"accounts": accounts, "contacts": contacts}))
    return CrmClient(token="test", base="http://testserver/api/v1", session=http), http


def test_lists_page_through_all_accounts():
    crm, _ = mock(extra_accounts=60)
    assert len(crm.list_accounts()) == 4 + 1 + 60
    assert [c["name"] for c in crm.list_contacts()] == ["Pat Admin"]


def test_requires_a_token():
    # a fresh client: CrmClient adds the token to the session it is given
    bare = TestClient(create_app({"accounts": parent_accounts(), "contacts": []}))
    assert bare.get("/api/v1/accounts").status_code == 401


def test_create_marks_account_as_candidate_created_and_fills_parent_name():
    crm, _ = mock()
    created = crm.create_account({"name": "Bellhaven of Batavia", "parent_id": BH, "billing_zip": "45103"})
    assert created["created_by_candidate"] is True
    assert created["parent_name"] == "Bellhaven Senior Living (Parent Account)"
    assert crm.get_account(created["account_id"])["name"] == "Bellhaven of Batavia"


def test_reparenting_updates_parent_name():
    crm, _ = mock()
    assert crm.update_account("A1", {"parent_id": HARBORVIEW})["parent_name"] == "Harborview Care Group (Parent Account)"


@pytest.mark.parametrize("body", [
    {"status": "Closed"},                # not Active / Inactive / Needs Review
    {"lifetime_revenue": 0},             # billing fields are read-only
    {"parent_id": "NOPE"},               # references must point to real accounts
    {"duplicate_of_account": "NOPE"},
])
def test_rejects_what_the_real_api_might_reject(body):
    crm, _ = mock()
    with pytest.raises(CrmError, match="422"):
        crm.update_account("A1", body)


def test_filters_by_zip():
    crm, _ = mock()
    assert [a["account_id"] for a in crm.list_accounts(zip="44000")] == ["A1"]
```

- [ ] **Step 2: Run them to check they fail**

Run: `python -m pytest tests/test_mock_crm.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'mock_crm'`

- [ ] **Step 3: Create an empty `mock_crm/__init__.py` and write `mock_crm/server.py`**

```python
"""Local stand-in for the CRM sandbox API, so the whole flow can be tested without touching the graded sandbox.

Run:  $env:BH_MOCK_SEED = "data\\snapshot\\crm.json"; uvicorn mock_crm.server:build --factory --port 8001
State is kept in memory: restart the server to reset it. It is deliberately stricter than the real API.
"""
import copy
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request

VALID_STATUSES = {"Active", "Inactive", "Needs Review"}
TEXT_FIELDS = ("name", "parent_id", "parent_name", "billing_street", "billing_city", "billing_state",
               "billing_zip", "care_type", "phone", "chow_current_account", "duplicate_of_account", "note")
WRITABLE = {"name", "parent_id", "billing_street", "billing_city", "billing_state", "billing_zip", "care_type",
            "status", "phone", "chow_current_account", "duplicate_of_account", "note"}
REFERENCES = ("parent_id", "chow_current_account", "duplicate_of_account")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")


def _paginate(items: list, page: int, page_size: int) -> dict:
    page, page_size = max(page, 1), max(1, min(page_size, 50))
    start = (page - 1) * page_size
    return {"data": items[start:start + page_size], "page": page, "page_size": page_size, "total": len(items)}


def _contains(value, wanted: str) -> bool:
    return wanted.lower() in (value or "").lower()


def create_app(seed: dict) -> FastAPI:
    app = FastAPI(title="Mock Bellhaven CRM (not graded)")
    accounts = {a["account_id"]: copy.deepcopy(a) for a in seed["accounts"]}
    contacts = [copy.deepcopy(c) for c in seed.get("contacts", [])]
    created = {"count": 0}

    def require_token(authorization: str | None) -> None:
        if not authorization or not authorization.startswith("Bearer ") or not authorization[7:].strip():
            raise HTTPException(401, "Missing or invalid token")

    def validate(body, creating: bool) -> None:
        if not isinstance(body, dict):
            raise HTTPException(422, "Body must be a JSON object")
        not_writable = sorted(set(body) - WRITABLE)
        if not_writable:
            raise HTTPException(422, f"Fields not writable: {not_writable}")
        if creating and not str(body.get("name") or "").strip():
            raise HTTPException(422, "name is required")
        if "status" in body and body["status"] not in VALID_STATUSES:
            raise HTTPException(422, f"status must be one of {sorted(VALID_STATUSES)}")
        for ref in REFERENCES:
            if body.get(ref) and body[ref] not in accounts:
                raise HTTPException(422, f"{ref} {body[ref]!r} does not exist")

    def set_parent_name(account: dict) -> None:
        parent = accounts.get(account["parent_id"]) if account.get("parent_id") else None
        account["parent_name"] = parent["name"] if parent else ""

    @app.get("/api/v1/me")
    def me(authorization: str | None = Header(None)):
        require_token(authorization)
        return {"candidate": "mock", "message": "Mock CRM. Nothing here is graded."}

    @app.get("/api/v1/accounts")
    def list_accounts(q: str = "", city: str = "", state: str = "", zip: str = "", street: str = "",
                      parent_id: str = "", page: int = 1, page_size: int = 50,
                      authorization: str | None = Header(None)):
        require_token(authorization)
        items = [a for a in accounts.values()
                 if _contains(a["name"], q) and _contains(a["billing_city"], city)
                 and _contains(a["billing_state"], state) and _contains(a["billing_zip"], zip)
                 and _contains(a["billing_street"], street) and (not parent_id or a["parent_id"] == parent_id)]
        return _paginate(items, page, page_size)

    @app.get("/api/v1/accounts/{account_id}")
    def get_account(account_id: str, authorization: str | None = Header(None)):
        require_token(authorization)
        if account_id not in accounts:
            raise HTTPException(404, "Account not found")
        return accounts[account_id]

    @app.post("/api/v1/accounts", status_code=201)
    async def create_account(request: Request, authorization: str | None = Header(None)):
        require_token(authorization)
        body = await request.json()
        validate(body, creating=True)
        created["count"] += 1
        account = {f: "" for f in TEXT_FIELDS} | {"status": "Active", "lifetime_revenue": 0, "outstanding_ar": 0} | body
        account |= {"account_id": f"001MOCK{created['count']:011d}", "created_by_candidate": True,
                    "updated_at": _now()}
        set_parent_name(account)
        accounts[account["account_id"]] = account
        return account

    @app.patch("/api/v1/accounts/{account_id}")
    async def update_account(account_id: str, request: Request, authorization: str | None = Header(None)):
        require_token(authorization)
        if account_id not in accounts:
            raise HTTPException(404, "Account not found")
        body = await request.json()
        validate(body, creating=False)
        account = accounts[account_id]
        account.update(body)
        account["updated_at"] = _now()
        set_parent_name(account)
        return account

    @app.get("/api/v1/contacts")
    def list_contacts(account_id: str = "", q: str = "", page: int = 1, page_size: int = 50,
                      authorization: str | None = Header(None)):
        require_token(authorization)
        items = [c for c in contacts if (not account_id or c["account_id"] == account_id) and _contains(c["name"], q)]
        return _paginate(items, page, page_size)

    return app


def build() -> FastAPI:
    """Factory for uvicorn: fills the mock from the snapshot file named in BH_MOCK_SEED."""
    path = os.environ.get("BH_MOCK_SEED")
    if not path:
        raise RuntimeError("Set BH_MOCK_SEED to a snapshot made by: python -m mock_crm.snapshot")
    return create_app(json.loads(Path(path).read_text(encoding="utf-8")))
```

- [ ] **Step 4: Run the tests to check they pass**

Run: `python -m pytest tests/test_mock_crm.py -q`
Expected: 9 passed. Starlette may print deprecation warnings about its `TestClient`; they're harmless.

- [ ] **Step 5: Write `mock_crm/snapshot.py`**

```python
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
```

- [ ] **Step 6: Take the snapshot now, before any live approval** (read-only). Keep the file: it's the "before" state the audit compares against. `data/` is gitignored.

Run: `python -m mock_crm.snapshot`
Expected: `Saved 121 accounts and 67 contacts to ...\data\snapshot\crm.json`

- [ ] **Step 7: Start the mock and check it.** In one terminal:

```powershell
$env:BH_MOCK_SEED = "data\snapshot\crm.json"; uvicorn mock_crm.server:build --factory --port 8001
```

In a second terminal:

```powershell
$env:BH_API_BASE = "http://localhost:8001/api/v1"; python -c "from bellhaven.crm import CrmClient; c = CrmClient(); print(len(c.list_accounts()), c._req('GET', '/me'))"
```

Expected: `121 {'candidate': 'mock', 'message': 'Mock CRM. Nothing here is graded.'}`. Stop the mock with Ctrl+C.

- [ ] **Step 8: Commit**

```powershell
git add mock_crm tests/test_mock_crm.py
git commit -m "test: local mock CRM and read-only snapshot"
```

---

### Task 11: End-state audit and end-to-end test

**Files:**
- Create: `bellhaven/audit.py`
- Test: `tests/test_audit.py`, `tests/test_end_to_end.py`

- [ ] **Step 1: Write the failing tests `tests/test_audit.py`**

```python
from bellhaven.audit import audit, changes_since
from tests.factories import CEDAR, make_account, make_location, parent_accounts

TODAY = "2026-11-25"


def test_clean_crm_has_no_problems():
    accounts = parent_accounts() + [make_account()]
    assert audit({"accounts": accounts}, accounts, [], [make_location()], TODAY) == []


def test_reports_what_the_pipeline_would_still_propose():
    accounts = parent_accounts() + [make_account(name="Old Name")]
    [problem] = audit({"accounts": accounts}, accounts, [], [make_location()], TODAY)
    assert problem.startswith("still proposed: update")


def test_catches_a_broken_billing_sop():
    before = parent_accounts() + [make_account(parent_id=CEDAR, lifetime_revenue=84000, outstanding_ar=12400)]
    after = parent_accounts() + [make_account(lifetime_revenue=84000, outstanding_ar=12400)]  # moved directly
    problems = audit({"accounts": before}, after, [], [make_location()], TODAY)
    assert any("billing SOP broken" in p for p in problems)


def test_catches_a_duplicate_pointing_nowhere():
    accounts = parent_accounts() + [make_account(), make_account(account_id="A2", name="Copy", billing_street="9 Other St",
                                                                 duplicate_of_account="GONE", status="Inactive")]
    problems = audit({"accounts": accounts}, accounts, [], [make_location()], TODAY)
    assert any("missing account GONE" in p for p in problems)


def test_counts_created_and_changed_accounts():
    before = {"accounts": [make_account()]}
    after = [make_account(name="New Name"), make_account(account_id="NEW1")]
    assert changes_since(before, after) == {"created": 1, "changed": 1}
```

- [ ] **Step 2: Run them to check they fail**

Run: `python -m pytest tests/test_audit.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'bellhaven.audit'`

- [ ] **Step 3: Write `bellhaven/audit.py`**

```python
"""Read-only check of the CRM's end state. Works against the mock or the live sandbox.

Run:  python -m bellhaven.audit --before data\\snapshot\\crm.json
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
```

- [ ] **Step 4: Run the tests to check they pass**

Run: `python -m pytest tests/test_audit.py -q`
Expected: 5 passed

- [ ] **Step 5: Write `tests/test_end_to_end.py`.** It runs one example of every proposal kind through pipeline → approve → re-run → audit against the mock.

```python
from fastapi.testclient import TestClient

from bellhaven import pipeline, store
from bellhaven.apply import apply_proposal
from bellhaven.audit import audit
from bellhaven.crm import CrmClient
from mock_crm.server import create_app
from tests.factories import CEDAR, HARBORVIEW, MILLSTONE, make_account, make_location, parent_accounts

TODAY = "2026-11-25"


def world():
    accounts = parent_accounts() + [
        make_account(account_id="A1", name="Old Name"),                                    # update (rename)
        make_account(account_id="A2", name="Cedar Trail of Oakton", parent_id=CEDAR,       # chow
                     billing_street="200 Oak St", billing_city="Oakton", billing_zip="44100",
                     phone="(614) 555-0200", lifetime_revenue=84000, outstanding_ar=12400),
        make_account(account_id="A3", name="Bellhaven of Elmira", billing_street="300 Elm St",  # duplicate survivor
                     billing_city="Elmira", billing_zip="44200", phone="(614) 555-0300"),
        make_account(account_id="A4", name="Harborview of Elmira", parent_id=HARBORVIEW,   # duplicate
                     billing_street="300 Elm Street", billing_city="Elmira", billing_zip="44200",
                     phone="(614) 555-0301"),
        make_account(account_id="A5", name="Bellhaven of Coldwater", billing_street="90 N Michigan Ave",  # not on website
                     billing_city="Coldwater", billing_state="MI", billing_zip="49036", phone="(517) 555-0500"),
        make_account(account_id="A6", name="Bellhaven of Sandusky", billing_street="2715 Columbus Ave",   # not on website
                     billing_city="Sandusky", billing_zip="44870", phone="(419) 555-0600",
                     lifetime_revenue=130000, outstanding_ar=5200),
        make_account(account_id="A7", name="Millstone Care of Sandusky", parent_id=MILLSTONE,
                     billing_street="2715 Columbus Ave", billing_city="Sandusky", billing_zip="44870",
                     phone="(419) 555-0601"),
    ]
    locations = [
        make_location(),
        make_location(slug="/communities/oakton", name="Bellhaven of Oakton", street="200 Oak Street",
                      city="Oakton", zip="44100", phone="(614) 555-0200"),
        make_location(slug="/communities/elmira", name="Bellhaven of Elmira", street="300 Elm St",
                      city="Elmira", zip="44200", phone="(614) 555-0300"),
        make_location(slug="/communities/batavia", name="Bellhaven of Batavia", street="2000 Hospital Dr",  # create
                      city="Batavia", zip="45103", phone="(513) 555-0400"),
    ]
    return {"accounts": accounts, "contacts": []}, locations


def test_full_review_cycle_against_the_mock():
    seed, locations = world()
    crm = CrmClient(token="test", base="http://testserver/api/v1", session=TestClient(create_app(seed)))
    conn = store.connect(":memory:")

    pipeline.run(conn, crm, lambda: locations, TODAY)
    pending = store.list_proposals(conn, "pending")
    assert sorted(r["kind"] for r in pending) == ["chow", "create", "duplicate", "not_on_website", "not_on_website",
                                                  "update"]

    for row in pending:
        store.set_status(conn, row["key"], "applied", "e2e", apply_proposal(conn, crm, row))

    assert pipeline.run(conn, crm, lambda: locations, "2026-11-26")["proposals"] == 0
    assert audit(seed, crm.list_accounts(), crm.list_contacts(), locations, TODAY) == []
    assert {h["action"] for h in store.change_history(conn)} == {"create", "update"}
```

- [ ] **Step 6: Run it, then the whole suite**

Run: `python -m pytest tests/test_end_to_end.py -q; python -m pytest -q`
Expected: `1 passed`, then everything passes

- [ ] **Step 7: Commit**

```powershell
git add bellhaven/audit.py tests/test_audit.py tests/test_end_to_end.py
git commit -m "test: end-state audit and end-to-end run against the mock"
```

---

### Task 12: Review app

**Files:**
- Create: `review_app.py`

- [ ] **Step 1: Write `review_app.py`**

```python
"""Review queue for Bellhaven CRM proposals.  Run:  streamlit run review_app.py"""
import json

import streamlit as st

from bellhaven import store
from bellhaven.apply import AlreadyHandled, StaleProposal, apply_proposal
from bellhaven.config import API_BASE, DB_PATH, USING_MOCK
from bellhaven.crm import CrmClient

st.set_page_config(page_title="Bellhaven CRM review", layout="wide")

KIND_LABELS = {
    "update": "Update fields", "chow": "Change of ownership (new account)", "duplicate": "Duplicate",
    "create": "New account", "not_on_website": "Not on website",
}
STATUSES = ["pending", "applied", "rejected", "failed", "applying", "superseded"]


@st.cache_resource
def resources():
    return store.connect(DB_PATH, crm_base=API_BASE), CrmClient()


def flat(d: dict, prefix: str = "") -> dict:
    out = {}
    for k, v in d.items():
        if isinstance(v, dict):
            out.update(flat(v, f"{prefix}{k}."))
        else:
            out[f"{prefix}{k}"] = v
    return out


def decide(conn, client, row, approve: bool, comment: str) -> None:
    if not approve:
        store.set_status(conn, row["key"], "rejected", comment)
        st.session_state.flash = ("info", f"Rejected: {row['title']}")
        return
    try:
        result = apply_proposal(conn, client, row)  # it claims the proposal first, so a second click can't write twice
    except AlreadyHandled as e:
        st.session_state.flash = ("warning", str(e))
        return
    except StaleProposal as e:
        store.set_status(conn, row["key"], "failed", comment, {"stale": str(e)})
        st.session_state.flash = ("error", f"Not written: the CRM record changed since this proposal was made. "
                                           f"Re-run the pipeline. {e}")
        return
    except Exception as e:  # show any API error to the reviewer and keep it on the proposal
        store.set_status(conn, row["key"], "failed", comment, {"error": str(e)})
        st.session_state.flash = ("error", f"Write failed: {e}")
        return
    store.set_status(conn, row["key"], "applied", comment, result)
    st.session_state.flash = ("success", f"Written to CRM: {row['title']} -> {result}")


def show(conn, client, row) -> None:
    ev, changes, before = json.loads(row["evidence"]), json.loads(row["changes"]), json.loads(row["before"])
    header = f"{KIND_LABELS.get(row['kind'], row['kind'])} · {row['title']}"
    if row["confidence"] == "low":
        header += " · LOW CONFIDENCE"
    with st.expander(header):
        for reason in ev.get("reasons", []):
            st.markdown(f"- {reason}")
        sop = ev.get("sop")
        if sop and (row["kind"] == "chow" or "parent_id" in changes):
            verdict = "keep old account, use CHOW" if sop["preserve_old_account"] else "re-parenting allowed"
            st.info(f"Billing SOP: lifetime revenue {sop['lifetime_revenue']}, "
                    f"outstanding AR {sop['outstanding_ar']} -> {verdict}")
        st.markdown("**Proposed change**")
        current = flat(before)
        st.table([{"field": f, "current": str(current.get(f, "")), "proposed": str(v)} for f, v in flat(changes).items()])
        left, right = st.columns(2)
        left.markdown("**Website**")
        left.json(ev.get("website") or {"-": "not on the website"}, expanded=False)
        right.markdown("**CRM now**")
        right.json(ev.get("crm") or {"-": "no CRM account"}, expanded=False)
        if "signals" in ev:
            st.caption(f"Match score {ev['score']}: " + ", ".join(f"{k} +{v}" for k, v in ev["signals"].items()))
        if row["status"] in ("pending", "failed"):
            comment = st.text_input("Reviewer comment", key=f"comment-{row['key']}")
            approve_col, reject_col = st.columns(2)
            if approve_col.button("Approve and write to CRM", key=f"approve-{row['key']}", type="primary"):
                decide(conn, client, row, True, comment)
                st.rerun()
            if reject_col.button("Reject", key=f"reject-{row['key']}"):
                decide(conn, client, row, False, comment)
                st.rerun()
        else:
            st.caption(f"{row['status']} at {row['decided_at']}" + (f" · {row['comment']}" if row["comment"] else ""))
            if row["result"]:
                st.json(json.loads(row["result"]), expanded=False)
            changed = store.change_history(conn, proposal_key=row["key"])
            if changed:
                st.markdown("**Change log**")
                st.table([{k: h[k] for k in ("account_id", "action", "field", "old_value", "new_value", "changed_at")}
                          for h in changed])


conn, client = resources()
counts = store.counts(conn)
status = st.sidebar.radio("Status", STATUSES, format_func=lambda s: f"{s} ({counts.get(s, 0)})")
kinds = st.sidebar.multiselect("Type", list(KIND_LABELS), default=list(KIND_LABELS), format_func=KIND_LABELS.get)
st.title("Bellhaven CRM review queue")
if USING_MOCK:
    st.success(f"MOCK CRM ({API_BASE}): nothing here is graded. Database: {DB_PATH}")
else:
    st.error(f"LIVE SANDBOX: approving writes to the graded CRM. Database: {DB_PATH}")
if "flash" in st.session_state:
    level, message = st.session_state.pop("flash")
    getattr(st, level)(message)
rows = [r for r in store.list_proposals(conn, status) if r["kind"] in kinds]
st.write(f"{len(rows)} {status} proposals")
for row in rows:
    show(conn, client, row)
```

- [ ] **Step 2: Smoke-test against the mock.** Approving here is safe. In one terminal:

```powershell
$env:BH_MOCK_SEED = "data\snapshot\crm.json"; uvicorn mock_crm.server:build --factory --port 8001
```

In a second terminal:

```powershell
$env:BH_API_BASE = "http://localhost:8001/api/v1"; $env:BH_DB_PATH = "data\test.db"; python -m bellhaven.pipeline; streamlit run review_app.py
```

Expected: a green **MOCK CRM** banner and 29 pending proposals. Open a few and check that the reasons, the change table and the website/CRM panels show up. Approve one and reject one, then check the success message and the result under the "applied" filter. Stop both with Ctrl+C, then run `Remove-Item data\test.db`.

- [ ] **Step 3: Commit**

```powershell
git add review_app.py
git commit -m "feat: Streamlit review queue"
```

---

### Task 13: Schedule and README

**Files:**
- Create: `deploy/crontab.txt`, `README.md`

- [ ] **Step 1: Write `deploy/crontab.txt`**

```text
# Bellhaven CRM pipeline: every day at 06:00 server time.
# It only scrapes, reads the CRM and stores proposals. Nothing is written to the CRM until a reviewer approves it.
# Safe to re-run: proposals that were already approved or rejected are never proposed again.
0 6 * * * cd /opt/bellhaven && mkdir -p logs && .venv/bin/python -m bellhaven.pipeline >> logs/pipeline.log 2>&1
```

- [ ] **Step 2: Write `README.md`**

````markdown
# Bellhaven CRM pipeline

Scrapes every Bellhaven community from the website, matches each one to the CRM, and queues proposed fixes. A reviewer approves or rejects each fix in a small app; only approved fixes are written back through the API.

## Setup (Windows PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env   # then put your token in .env
```

## Use

```powershell
python -m bellhaven.pipeline      # daily job: scrape, read CRM, store proposals (never writes to the CRM)
streamlit run review_app.py       # review queue: approve writes to the CRM, reject records the decision
python -m pytest -q               # tests
```

State lives in `data/state.db` (SQLite): runs, proposals and decisions, every API call, and a field-by-field change log. Delete it only if you want to start reviewing from scratch. To see every change made through the API, with its old and new value:

```powershell
python -c "from bellhaven import store; from bellhaven.config import DB_PATH; [print(dict(r)) for r in store.change_history(store.connect(DB_PATH))]"
```

## Testing without touching the graded CRM

`mock_crm/` is a local copy of the CRM API, filled from a read-only snapshot (`python -m mock_crm.snapshot`). Point everything at it with two settings:

```powershell
$env:BH_MOCK_SEED = "data\snapshot\crm.json"; uvicorn mock_crm.server:build --factory --port 8001
$env:BH_API_BASE = "http://localhost:8001/api/v1"; $env:BH_DB_PATH = "data\test.db"
```

Each database is tied to the CRM it was first used with, so test decisions can't leak into the live run. The app shows a green banner for the mock and a red one for the live sandbox. `python -m bellhaven.audit --before data\snapshot\crm.json` checks the end state of whichever CRM is configured (read-only).

## Schedule

`deploy/crontab.txt` runs the pipeline daily at 06:00. Re-runs are safe: decided proposals are never proposed again, and undecided ones are refreshed. On Windows, the equivalent is:

```powershell
schtasks /Create /SC DAILY /ST 06:00 /TN "Bellhaven pipeline" /TR "cmd /c cd /d C:\Users\463995\rt_pg\crm && .venv\Scripts\python.exe -m bellhaven.pipeline"
```
````

- [ ] **Step 3: Commit**

```powershell
git add deploy/crontab.txt README.md
git commit -m "docs: schedule and README"
```

---

### Task 14: Rehearse the whole review on the mock

**Files:**
- Create: `mock_crm/approve_all.py`

- [ ] **Step 1: Start a fresh mock** (restarting resets it). In one terminal:

```powershell
$env:BH_MOCK_SEED = "data\snapshot\crm.json"; uvicorn mock_crm.server:build --factory --port 8001
```

- [ ] **Step 2: Point a second terminal at the mock and run the pipeline**

```powershell
$env:BH_API_BASE = "http://localhost:8001/api/v1"; $env:BH_DB_PATH = "data\test.db"; python -m bellhaven.pipeline
```

Expected: `{"locations": 35, "accounts": 121, "proposals": 29, "new": 29, ...}`

- [ ] **Step 3: Practise the demo.** Run `python -m bellhaven.web`, open http://localhost:8000, check the top bar's badge says **Mock CRM**, and walk through it as you would on camera: open the Tiffin CHOW, approve a rename, reject one, then check the change through the mock (`python -c "from bellhaven.crm import CrmClient; print(CrmClient().get_account('<id>'))"`).

- [ ] **Step 4: Approve everything that's left** with `mock_crm/approve_all.py`. It's already in the repo: it goes through the same `review.approve` path as the Approve button, and refuses to run against the live sandbox.

Run: `python -m mock_crm.approve_all`
Expected: `Applied N of N proposals to the mock at http://localhost:8001/api/v1` (N is 29 minus what you decided in Step 3). If any can't be applied, it lists them and exits non-zero.

- [ ] **Step 5: Run the pipeline again**

Run: `python -m bellhaven.pipeline`
Expected: `"new": 0`, and `"proposals"` equals the number you rejected in Step 3

- [ ] **Step 6: Audit the mock's end state**

Run: `python -m bellhaven.audit --before data\snapshot\crm.json`
Expected: `{"created": 6, "changed": 25}`, give or take what you rejected, then `OK`. The only acceptable `PROBLEM` lines are `still proposed` for proposals you rejected on purpose.

- [ ] **Step 7: Clean up**

```powershell
Remove-Item data\test.db; Remove-Item Env:BH_API_BASE, Env:BH_DB_PATH
```

Stop the mock with Ctrl+C.

---

### Task 15: Live run and check the proposals (read-only)

- [ ] **Step 0: Switch back to the live sandbox.** Open a new terminal, or clear the test settings:

```powershell
Remove-Item Env:BH_API_BASE, Env:BH_DB_PATH, Env:BH_MOCK_SEED -ErrorAction SilentlyContinue
```

If a later command says a database "holds decisions for" another CRM, the guard has caught a mix-up. Check your settings rather than deleting the database.

- [ ] **Step 1: Run the pipeline against the sandbox**

Run: `python -m bellhaven.pipeline`
Expected: about `{"locations": 35, "accounts": 121, "proposals": 29, "new": 29, ...}`

- [ ] **Step 2: List the proposals**

```powershell
python -c "from bellhaven import store; from bellhaven.config import DB_PATH; [print(r['kind'], '|', r['title'], '|', r['confidence']) for r in store.list_proposals(store.connect(DB_PATH), 'pending')]"
```

- [ ] **Step 3: Compare with what exploration found.** This table was confirmed on 2026-11-24 by running this plan's code against the sandbox (read-only). Investigate any difference before reviewing. If the code is wrong, fix it with a new test; don't fix the CRM by hand.

| kind | Expected |
|---|---|
| update (13) | Names: Grove City, Sycamore Ridge, Arbors Dayton, Ashland, Riverbend Manor → Chagrin Falls, Sunny Acres → Willow Creek, Chesterton Senior Commons → Chesterton. Name and parent: Cedar Trail of Zanesville, Kettering Nursing & Rehabilitation (the Kettering survivor: all three copies tie, so the lowest id is kept). Parent only: Lima, Findlay. Ashtabula street (PO Box). Portsmouth zip 45626 → 45662. |
| chow (2) | Bellhaven of Tiffin, Bellhaven of Marietta (both Cedar Trail, revenue and AR > 0) |
| duplicate (7) | Second Bellhaven of Owosso, Harborview Nursing & Rehab of Port Clinton, Harborview Shores of Erie, Cedar Trail of Monroe, Monroe Gardens Care Center, Kettering Care Centre, Kettering Senior Campus |
| create (4) | Bellhaven of Batavia, Bellhaven of Carlisle (PA), Bellhaven at Union Square, Amberly Manor (Hudson OH) |
| not_on_website (3) | Bellhaven Care Center of Alliance, Bellhaven of Coldwater, and Bellhaven of Sandusky (its note names Millstone Care of Sandusky at the same address; nothing is linked, per decision #2) |

- [ ] **Step 4: Run it a second time**

Run: `python -m bellhaven.pipeline`
Expected: `"new": 0, "already_known": 29`

---

### Task 16: Review the queue and record the demo

- [ ] **Step 0: Make the first live write carefully.** The mock can't confirm the real API's exact write format, so test it on one low-risk change first. Run `python -m bellhaven.web`, open http://localhost:8000, and check the badge says **LIVE SANDBOX**. Approve only **Update Bellhaven of Portsmouth** (zip 45626 → 45662). The app asks "Write this to the live sandbox?" and shows the exact change: check it, then press **Write to live CRM**. Check that the green banner says "The CRM stored exactly what was sent", and that "View in Meridian ↗" shows the new zip. If the mismatches aren't empty or you get an error, stop: fix `crm.py`/`apply.py` (with a test) before approving anything else. Then make the first create and the first CHOW the same way, one at a time, before going faster. For a CHOW, the banner lists any field the CRM stored differently on the new account or on the old account's link; there should be none. Don't run the pipeline while approving, and if a CHOW reports an error, press Retry on it before the next pipeline run.
- [ ] **Step 1: Start a screen recorder** (Loom, or any free recorder), with the app still open.
- [ ] **Step 2: Record about 2 minutes.**
  - Open the Tiffin CHOW proposal and explain the billing SOP box.
  - Open a rename (for example Riverbend Manor → Bellhaven of Chagrin Falls) and approve it (confirm the dialog). Show the green "Written" banner.
  - Reject one proposal you genuinely disagree with, with a comment. If you agree with all of them, the most debatable are the `not_on_website` ones (decisions #1 and #2) and the Kettering duplicate survivor (a tie, broken by lowest account id).
  - Open the CRM browser and show the approved change there. Its address is `https://analyst-assessment-production.up.railway.app/crm/<your token>/accounts`, and each account is at `.../accounts/<account_id>`. It's read only and shows the same fields as the API: parent, status, care type, address, revenue and AR, CHOW Current Account, Duplicate Of, Note, Last Updated. A parent's page lists its child accounts. **The token is in that address:** keep the address bar out of the shared recording (full screen, or crop it), because anyone with the token can change your graded sandbox through the API.
- [ ] **Step 3: Work through the rest of the queue.** Read every proposal's evidence before approving. A queue with unapproved proposals scores the same as doing nothing.
- [ ] **Step 4: Check the Failed tab.** For a stale failure, re-run the pipeline and review again. For an API error, read the error saved on the proposal, fix the code, and approve again. If you reject one by mistake, put it back in the queue with the command below, using the key at the end of its page's address (`/proposals/<key>`):

```powershell
python -c "import sys; from bellhaven import store; from bellhaven.config import DB_PATH; c = store.connect(DB_PATH); n = c.execute('UPDATE proposals SET status=?, decided_at=NULL WHERE key=? AND status=?', ('pending', sys.argv[1], 'rejected')).rowcount; c.commit(); print(n, 'reopened')" <key>
```
- [ ] **Step 5: Run the pipeline again**

Run: `python -m bellhaven.pipeline`
Expected: `"new": 0`, and the app shows 0 pending.

- [ ] **Step 6: Share the recording** so anyone with the link can view it, and save the link for the submission form.

---

### Task 17: Writeup

**Files:**
- Create: `WRITEUP.md`

- [ ] **Step 1: Write `WRITEUP.md` with these sections**, using your actual results:
  1. **Approach:** the pipeline stages, and why matching is address-first (rebrands and stale phones).
  2. **Matching:** the weights table from `match.py`, the 60/40 thresholds, the state rule, one-to-one assignment, and the three name traps (Amberly Manor CO, Union Square, New Carlisle vs Carlisle).
  3. **Outcomes:** counts per proposal kind and how many you approved or rejected (from `store.counts`).
  4. **Duplicates:** the survivor ranking (billing history → under Bellhaven → administrator → phone → Active → score), and why losers get `duplicate_of_account` plus `Inactive` instead of a merge.
  5. **Billing SOP:** Tiffin and Marietta (CHOW); Lima and Findlay (direct, because AR is 0); Akron, Defiance and Meadville (no move, so the rule doesn't apply); Sandusky (flagged Needs Review with a note naming Millstone Care of Sandusky, not linked: same address is a clue, not proof of a sale, and its billing history makes a wrong link costly; explain decision #2).
  6. **Accounts not on the website:** why `Needs Review` and not `Inactive` (decision #1).
  7. **Safe re-runs:** proposal keys, decided proposals never re-proposed, refresh and supersede, freshness check before writing, create-once.
  7b. **Audit trail:** the SQLite schema (runs → proposals → api_log → change_log), and how to answer "what changed on this account, when, and why" from the `change_history` view. Include counts from your live run.
  8. **Testing without touching the graded CRM:** the strict mock filled from a read-only snapshot, the end-to-end test, the full rehearsal, the database guard and the mock/live banner, the careful first live write, and the final audit.
  9. **What I'd change for production:** a shared database in place of SQLite, a geocoding/address service, contact sync, alerting when the website layout changes (the directory count check is the start of this), and multi-reviewer auth.
- [ ] **Step 2: Commit**

```powershell
git add WRITEUP.md
git commit -m "docs: writeup"
```

---

### Task 18: Final check and submit

- [ ] **Step 1:** `python -m pytest -q`. All pass.
- [ ] **Step 2:** `python -m bellhaven.pipeline`. Expect `"new": 0`.
- [ ] **Step 2b:** `python -m bellhaven.audit --before data\snapshot\crm.json` against the live sandbox (read-only). Expect `OK`, or only `still proposed` lines for proposals you rejected on purpose. Investigate any other `PROBLEM` line.
- [ ] **Step 3:** Run `git status` and confirm that `.env`, `data/`, the screenshots and the `.docx` are not tracked. Run `git grep -E "bh_[A-Za-z0-9]{16,}"` and confirm it finds nothing (no token in tracked files).
- [ ] **Step 4:** Push to the repository you'll submit (private, or as the form asks). Paste the repo link and the demo link into the submission form.

---

## Rough time budget

| Tasks | Hours |
|---|---|
| 0–4 setup, normalization, scraper, client, SOP | 2 |
| 5–6 matching and proposals | 3 |
| 7–9 store, apply, pipeline | 2 |
| 10–11 mock CRM, audit, end-to-end test | 2 |
| 12–13 app, schedule, README | 2 |
| 14 rehearsal on the mock | 1 |
| 15–18 live run, review, demo, writeup, submit | 3 |
