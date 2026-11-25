# Review App Redesign (Meridian style) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Streamlit review app with a FastAPI app whose pages look like the Meridian CRM: a review queue, one page per proposal, the change history and the pipeline runs, with a confirmation before every write to the live sandbox.

**Architecture:** A new package, `bellhaven/web/`, in three layers. `present.py` turns stored proposals into what the pages show, as pure functions. `review.py` approves or rejects one proposal and turns every result of `apply.apply_proposal` into an outcome. `app.py` holds the routes and the CSRF and origin checks, and renders Jinja2 templates styled with Meridian's CSS. The database, the apply guards and the pipeline stay as they are, apart from recording failed runs.

**Tech Stack:** Python 3.12, FastAPI, Jinja2 (autoescape on), python-multipart (form posts), uvicorn, a little vanilla JavaScript (progressive enhancement only), and pytest with FastAPI's `TestClient`.

**Spec:** `docs/superpowers/specs/2026-11-24-review-app-redesign-design.md` (approved).

---

## Changes made during the build (2026-11-24)

Each task was built from this plan, then reviewed twice: once against the spec, once for code quality. A final review covered the whole branch. Review fixes changed the code, so **where the code on branch `bellhaven-pipeline` differs from a code block in this plan, the code is authoritative.** The suite has 208 tests: this plan's 192, plus 16 added by the fixes.

- **Task 3, present:**
  - Account names come from the newest evidence (by `run_id`), so a renamed survivor shows its current name.
  - A banner shows only when the stored status matches the outcome, so an old link never announces a write.
  - States compare like the matching does. Negative amounts print as "-$500". The stuck-apply hint uses `store.STUCK_MINUTES`.
- **Task 4, review:**
  - `approve(conn, client, key, comment=None, *, live, confirmed=False)`: `live` is required and keyword-only, so it can't be swapped with `confirmed`.
  - A database error never escapes: a new `error` outcome with a "Not saved" banner, also used when a failure can't be saved. Errors are logged.
  - Tests for the claim race and the database-failure paths. `approve_all` passes `live=not USING_MOCK`.
- **Task 5, app:**
  - The live dialog's `confirmed` value is reset after it submits and can't be restored by Back (`autocomplete="off"`, and a reload when the page comes from the back/forward cache).
  - Every POST form submits once. Only this machine's host names are served (against DNS rebinding), and pages can't be framed.
  - HTTP errors are pages, not JSON. `PORT` is defined once. A test covers decided proposal pages.
- **Final review:**
  - Meridian links go through `/meridian/accounts/<id>` on the app, which redirects. The token never appears in a page, where hovering a link would show it on screen.
  - "testserver" is no longer an allowed host; the tests use `http://127.0.0.1:8000`.
  - The "Not saved" banner no longer claims every write is in the change history.
  - The website-vs-CRM grid's CRM side is labelled "CRM when proposed".
  - On live, the `R` shortcut asks before rejecting.
  - The main plan's rehearsal (Task 14) runs the existing `approve_all` instead of rewriting it.
- **Left for later:** a long pipeline error is shown in full on the runs page, and if recording a failed run itself fails, the run shows as "Running or interrupted" (the cron log still has the original error).

## Before you start

- Work on branch `bellhaven-pipeline` in `C:\Users\463995\rt_pg\crm`, with the virtual environment active (`.\.venv\Scripts\Activate.ps1`). `python -m pytest -q` should report **147 passed** before Task 1.
- **Never point anything in this plan at the live sandbox.** The tests use `FakeCrm` (in `tests/factories.py`), and the browser check in Task 7 uses the local mock CRM.
- Never commit `.env`, `data/`, the screenshots or the `.docx` (they hold the API token).
- End every commit message with the line `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. The commit commands below pass it as a second `-m`.
- **How code steps are written:**
  - "**Create** `path`" gives the whole file. If the file exists, replace all of it.
  - "**Append to** `path`" adds the block at the end of the file, after two blank lines.
  - "**Replace in** `path`" gives a block that appears exactly once in the file, then the block to put in its place.
  - "**Delete** `path`" removes the file.

## How the app fits together

- The **pipeline** (unchanged, read-only) fills `proposals` in SQLite.
- `GET` pages only read. `present.py` works out what to show from the stored rows: the one-line summary, the Now → After rows, the website-vs-CRM grids, the SOP box and the banner.
- The two `POST` routes (`/approve`, `/reject`) check the CSRF token and the `Origin`/`Referer`, then call `review.approve` or `review.reject`.
- `review.approve` calls `apply.apply_proposal`, the only code that writes to the CRM. It keeps all of its guards: the claim, the freshness and rule re-checks, create-once, and the latest-run check. It records the result on the proposal and returns an `Outcome`.
- After a decision the browser is redirected (303) to the next pending proposal. The redirect carries `last=<key>&outcome=<kind>`, and the next page shows a banner read from the stored proposal.
- On live, `review.approve` returns `needs_confirmation` unless the form says `confirmed=yes`, and the route then renders the confirmation page instead of writing.

## Decisions made while planning (beyond the spec)

1. **Duplicate evidence names its survivor.** `classify._duplicate` adds `evidence["survivor"] = {"account_id", "name"}`. In the real data, 5 of the 7 duplicates' survivors have no proposal of their own, so the queue couldn't otherwise say "Duplicate of {name}". Evidence isn't part of the proposal key, so existing proposals keep their keys, and the next pipeline run refreshes their evidence.
2. **The redirect carries the outcome as well as `last`.** "Already handled" and "written but not recorded" can't be read back from the database. The banner's text still comes from the stored proposal; `outcome` only says which banner to show, and unknown values show none.
3. **`store.list_runs`** is added for the runs page, next to `fail_run` and `latest_run`.
4. **Retry is offered on every "Failed" banner,** not only for a CHOW. It posts approve again, so on live it asks for confirmation again.
5. **Two extra templates:** `notfound.html` (the 404 page) and `_confirm_body.html` (shared by the confirmation dialog and the confirmation page).
6. **Account ids in the queue.** Each row shows the account id under the city. A duplicate whose name is the same as its survivor's shows the survivor's id too, for example "Duplicate of Bellhaven of Owosso (001EGU7BMJ942ZTRE6)".
7. **`mock_crm/approve_all.py`** now goes through `review.approve`, the same path as the Approve button. It reports every proposal it couldn't apply and exits non-zero, instead of stopping at the first error.
8. **One lock serializes requests.** FastAPI runs each request in a worker thread, and the app shares one SQLite connection, so each handler holds a `threading.Lock`. It's a single-user local app, so nothing has to wait in practice.
9. **Times are shown in local time,** to the minute (`2026-11-24 20:13`).

## File map

| File | Status | Responsibility |
|---|---|---|
| `bellhaven/store.py` | modify | add `fail_run`, `latest_run`, `list_runs` |
| `bellhaven/pipeline.py` | modify | record a failed run's error, then re-raise |
| `bellhaven/classify.py` | modify | duplicate evidence names its survivor |
| `bellhaven/web/__init__.py` | create | package marker |
| `bellhaven/web/present.py` | create | pure view-model functions over stored proposals |
| `bellhaven/web/review.py` | create | approve and reject one proposal, as an `Outcome` |
| `bellhaven/web/app.py` | create | `create_app()` (routes, CSRF, origin check, templates, static files) and `build()` |
| `bellhaven/web/__main__.py` | create | `python -m bellhaven.web` runs uvicorn on 127.0.0.1:8000 |
| `bellhaven/web/templates/*.html` | create | `base`, `queue`, `proposal`, `_confirm_body`, `confirm`, `history`, `runs`, `notfound` |
| `bellhaven/web/static/meridian.css` | create | Meridian's CSS rules plus the app's additions |
| `bellhaven/web/static/review.js` | create | keyboard shortcuts, clickable rows, and the live confirmation dialog |
| `mock_crm/approve_all.py` | modify | use `review.approve`, and report what wasn't applied |
| `tests/review_fixtures.py` | create | a queue with one proposal of each kind, built by the real pipeline |
| `tests/test_present.py`, `tests/test_review_service.py`, `tests/test_web.py`, `tests/test_approve_all.py` | create | tests for the new code |
| `tests/test_store.py`, `tests/test_pipeline.py`, `tests/test_classify.py` | modify | one new test each |
| `requirements.txt` | modify | add `jinja2` and `python-multipart`; remove `streamlit` |
| `review_app.py` | delete | the Streamlit app |
| `README.md`, `docs/superpowers/plans/2026-11-24-bellhaven-crm-pipeline.md` | modify | the new app's address and the confirmation step |

---

### Task 1: Record failed pipeline runs

The runs page and the queue header need to say why a run stopped. Today a failed run just leaves `finished_at` NULL.

**Files:**
- Modify: `bellhaven/store.py` (append three functions)
- Modify: `bellhaven/pipeline.py` (`run`)
- Test: `tests/test_store.py`, `tests/test_pipeline.py`

- [ ] **Step 1: Write the failing tests**

**Append to `tests/test_store.py`** (after two blank lines):

```python
def test_runs_are_listed_newest_first_and_a_failure_is_recorded():
    conn = store.connect(":memory:")
    assert store.latest_run(conn) is None
    first = store.start_run(conn)
    store.finish_run(conn, first, {"proposals": 0})
    second = store.start_run(conn)
    store.fail_run(conn, second, "CrmError: timeout")
    assert [r["id"] for r in store.list_runs(conn)] == [second, first]
    latest = store.latest_run(conn)
    assert latest["id"] == second and latest["finished_at"] is None
    assert json.loads(latest["stats"]) == {"error": "CrmError: timeout"}
    assert store.latest_finished_run(conn) == first
```

**Replace in `tests/test_pipeline.py`**, this exact text (it appears once):

```python
import pytest
```

with:

```python
import json

import pytest
```

**Append to `tests/test_pipeline.py`** (after two blank lines):

```python
def test_a_failed_run_records_its_error():
    conn = store.connect(":memory:")

    def broken_scrape():
        raise ScrapeError("site layout changed")

    with pytest.raises(ScrapeError):
        pipeline.run(conn, FakeCrm(parent_accounts()), broken_scrape, "2026-11-25")
    run = store.latest_run(conn)
    assert run["finished_at"] is None
    assert json.loads(run["stats"]) == {"error": "ScrapeError: site layout changed"}
```

- [ ] **Step 2: Run them to make sure they fail**

Run: `python -m pytest tests/test_store.py tests/test_pipeline.py -q`
Expected: 2 failed, with `AttributeError: module 'bellhaven.store' has no attribute 'latest_run'`.

- [ ] **Step 3: Add the run functions to the store**

**Append to `bellhaven/store.py`** (after two blank lines):

```python
def fail_run(conn, run_id: int, error: str) -> None:
    """Record why a run stopped. finished_at stays NULL, so a failed run never counts as finished."""
    with conn:
        conn.execute("UPDATE runs SET stats=? WHERE id=?", (json.dumps({"error": error}), run_id))


def latest_run(conn):
    """The most recent pipeline run, finished or not, or None if there has never been one."""
    return conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()


def list_runs(conn):
    """Every pipeline run, newest first."""
    return conn.execute("SELECT * FROM runs ORDER BY id DESC").fetchall()
```

- [ ] **Step 4: Record the error in the pipeline**

**Replace in `bellhaven/pipeline.py`**, this exact text (it appears once):

```python
    If the scrape or a CRM read fails, the exception propagates before any proposal is touched, so nothing is
    superseded, and cron sees a non-zero exit. The run's row is then left with finished_at NULL, which means the
    run failed or is still running.
    """
    run_id = store.start_run(conn)
    locations = scrape_fn()
    accounts, contacts = client.list_accounts(), client.list_contacts()
    proposals = build_proposals(locations, accounts, contacts, today)
    new, known = store.add_proposals(conn, run_id, proposals)
    superseded = store.supersede_missing(conn, [p.key for p in proposals])
    stats = {"locations": len(locations), "accounts": len(accounts), "contacts": len(contacts),
             "proposals": len(proposals), "new": new, "already_known": known, "superseded": superseded}
    store.finish_run(conn, run_id, stats)
```

with:

```python
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
```

- [ ] **Step 5: Run the tests**

Run: `python -m pytest -q`
Expected: **149 passed**.

- [ ] **Step 6: Commit**

```powershell
git add bellhaven/store.py bellhaven/pipeline.py tests/test_store.py tests/test_pipeline.py
git commit -m "feat: record why a pipeline run failed" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Name a duplicate's survivor in its evidence

The queue shows "Duplicate of {survivor name}". Most survivors have no proposal of their own, so the name has to travel with the duplicate.

**Files:**
- Modify: `bellhaven/classify.py` (`_duplicate`)
- Test: `tests/test_classify.py`

- [ ] **Step 1: Write the failing test**

**Append to `tests/test_classify.py`** (after two blank lines):

```python
def test_duplicate_evidence_names_the_survivor():
    accounts = [make_account(account_id="A1"),
                make_account(account_id="A2", name="Harborview Shores of Testville", parent_id=HARBORVIEW)]
    [dup] = run([make_location()], accounts)
    assert dup.evidence["survivor"] == {"account_id": "A1", "name": "Bellhaven of Testville"}
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `python -m pytest tests/test_classify.py -q`
Expected: 1 failed, with `KeyError: 'survivor'`.

- [ ] **Step 3: Add the survivor to the evidence**

**Replace in `bellhaven/classify.py`**, this exact text (it appears once):

```python
    changes = {"duplicate_of_account": s["account_id"], "status": "Inactive",
               "note": _note(a["note"], f"Duplicate of {s['account_id']} ({s['name']}).", today, loc.url)}
    return Proposal("duplicate", f"Duplicate: '{a['name']}' -> '{s['name']}'", a["account_id"], loc.slug, changes,
                    {k: a.get(k) for k in changes}, _evidence(loc, m, a, reasons), confidence)
```

with:

```python
    changes = {"duplicate_of_account": s["account_id"], "status": "Inactive",
               "note": _note(a["note"], f"Duplicate of {s['account_id']} ({s['name']}).", today, loc.url)}
    evidence = _evidence(loc, m, a, reasons)
    evidence["survivor"] = {"account_id": s["account_id"], "name": s["name"]}  # so the review app can name it
    return Proposal("duplicate", f"Duplicate: '{a['name']}' -> '{s['name']}'", a["account_id"], loc.slug, changes,
                    {k: a.get(k) for k in changes}, evidence, confidence)
```

- [ ] **Step 4: Run the tests**

Run: `python -m pytest -q`
Expected: **150 passed**. The proposal key ignores evidence, so no existing test changes.

- [ ] **Step 5: Commit**

```powershell
git add bellhaven/classify.py tests/test_classify.py
git commit -m "feat: duplicate evidence names the surviving account" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Page logic (`present.py`)

Pure functions that turn stored proposals into what the pages show. They make no database or network calls and produce no HTML, so they're easy to test.

Three things to know:
- **Parsed rows.** `present.parse(row)` turns a `sqlite3.Row` into a dict with the JSON columns (`changes`, `before`, `evidence`, `result`) decoded. Every other function takes those dicts.
- **Names for ids.** `account_names(rows)` builds `{account_id: name}` from everything the stored evidence knows: each CRM record, its parent, weak candidates, a duplicate's survivor, and accounts a create or CHOW already made. `show(field, value, names)` uses it to print `parent_id` as the parent's name, and `duplicate_of_account` / `chow_current_account` as "Name (id)".
- **Empty values** come back as `None`, and the templates print them as *empty*.

**Files:**
- Create: `bellhaven/web/__init__.py`, `bellhaven/web/present.py`
- Create: `tests/review_fixtures.py` (shared by Tasks 3–5), `tests/test_present.py`

- [ ] **Step 1: Create the package marker and the shared test queue**

The fixture runs the real pipeline over a fake CRM. That gives one proposal of each kind with realistic evidence: a rename (`update`), Tiffin's change of ownership (`chow`), Batavia (`create`), a second Owosso copy (`duplicate`), and Coldwater (`not_on_website`).

**Create `bellhaven/web/__init__.py`**:

```python
"""The review app. Start it with: python -m bellhaven.web"""
```

**Create `tests/review_fixtures.py`**:

```python
"""A review queue with one proposal of each kind, made by the real pipeline over the fake CRM."""
from bellhaven import pipeline, store
from bellhaven.web import present
from tests.factories import CEDAR, FakeCrm, make_account, make_location, parent_accounts


def queue_crm() -> FakeCrm:
    return FakeCrm(parent_accounts() + [
        # update: renamed on the website
        make_account(account_id="A1", name="Riverbend Manor"),
        # chow: under another parent, with revenue and AR
        make_account(account_id="A2", name="Bellhaven of Tiffin", parent_id=CEDAR, lifetime_revenue=84000,
                     outstanding_ar=12400, billing_street="45 St Lawrence Dr", billing_city="Tiffin",
                     billing_zip="44883", phone="(614) 829-9278"),
        # duplicate: two copies of Owosso; A3 has the website's phone, so it is kept
        make_account(account_id="A3", name="Bellhaven of Owosso", billing_street="9 Elm St", billing_city="Owosso",
                     billing_state="MI", billing_zip="48867", phone="(989) 555-0101"),
        make_account(account_id="A4", name="Second Bellhaven of Owosso", billing_street="9 Elm St",
                     billing_city="Owosso", billing_state="MI", billing_zip="48867", phone="(989) 555-0199"),
        # not_on_website: under Bellhaven, not listed
        make_account(account_id="A5", name="Bellhaven of Coldwater", billing_street="1 Lake Rd",
                     billing_city="Coldwater", billing_state="MI", billing_zip="49036", phone="(517) 555-0102"),
    ])


def locations():
    site = lambda slug, **values: make_location(slug=f"/communities/{slug}", url=f"https://site.test/communities/{slug}",
                                                **values)
    return [
        site("chagrin-falls", name="Bellhaven of Chagrin Falls"),
        site("tiffin", name="Bellhaven of Tiffin", street="45 St Lawrence Drive", city="Tiffin", zip="44883",
             phone="(614) 829-9278", administrator="Kim Lee"),
        site("owosso", name="Bellhaven of Owosso", street="9 Elm Street", city="Owosso", state="MI", zip="48867",
             phone="(989) 555-0101", administrator="Sam Fox"),
        site("batavia", name="Bellhaven of Batavia", street="7 River Rd", city="Batavia", zip="45103",
             phone="(513) 555-0103", administrator="Dee Ray"),
    ]


def make_queue(crm=None):
    """(crm, conn) with five pending proposals: chow, create, duplicate, not_on_website, update."""
    crm = crm or queue_crm()
    conn = store.connect(":memory:")
    pipeline.run(conn, crm, locations, "2026-11-25")
    return crm, conn


def by_kind(conn) -> dict:
    """kind -> the parsed proposal (one of each)."""
    return {p["kind"]: p for p in (present.parse(r) for r in store.list_proposals(conn))}
```

- [ ] **Step 2: Write the failing tests**

**Create `tests/test_present.py`**:

```python
from bellhaven.web import present
from tests.factories import BH, CEDAR
from tests.review_fixtures import by_kind, make_queue


def queue():
    _, conn = make_queue()
    ps = by_kind(conn)
    return ps, present.account_names(list(ps.values()))


def update(changes, before):
    """A parsed update proposal, for the summary rules."""
    return {"key": "k", "kind": "update", "title": "Update X", "account_id": "A1", "status": "pending",
            "changes": changes, "before": before, "evidence": {"reasons": []}, "confidence": "high", "result": None}


def test_summaries_for_every_kind():
    ps, names = queue()
    assert present.summary(ps["update"], names) == "Name → Bellhaven of Chagrin Falls"
    assert present.summary(ps["chow"], names) == "New Bellhaven account; old one kept for billing"
    assert present.summary(ps["create"], names) == "Create under Bellhaven"
    assert present.summary(ps["duplicate"], names) == "Duplicate of Bellhaven of Owosso"
    assert present.summary(ps["not_on_website"], names) == "Status → Needs Review"


def test_a_duplicate_with_the_same_name_as_its_survivor_shows_the_survivor_id():
    ps, names = queue()
    twin = ps["duplicate"] | {"evidence": ps["duplicate"]["evidence"] | {"crm": {"name": "Bellhaven of Owosso"}}}
    assert present.summary(twin, names) == "Duplicate of Bellhaven of Owosso (A3)"


def test_update_summary_shows_short_old_values_the_parent_by_name_and_a_count():
    names = {BH: "Bellhaven Senior Living (Parent Account)"}
    assert present.summary(update({"billing_zip": "45662", "note": "n"}, {"billing_zip": "45626", "note": ""}),
                           names) == "Zip 45626 → 45662"
    many = update({"name": "Bellhaven of Lima", "parent_id": BH, "billing_street": "1 Elm St", "billing_city": "Lima",
                   "note": "n"},
                  {"name": "Lima Care", "parent_id": CEDAR, "billing_street": "PO Box 4", "billing_city": "Lyma",
                   "note": ""})
    assert present.summary(many, names) == "Name → Bellhaven of Lima, Parent → Bellhaven Senior Living +2 more"


def test_change_rows_show_only_what_changes():
    ps, names = queue()
    rows = {r["field"]: r for r in present.change_rows(ps["update"], names)}
    assert set(rows) == {"name", "note"}
    assert (rows["name"]["now"], rows["name"]["after"]) == ("Riverbend Manor", "Bellhaven of Chagrin Falls")
    assert rows["note"]["now"] is None  # empty: the page shows "empty"
    dup = {r["field"]: r for r in present.change_rows(ps["duplicate"], names)}
    assert dup["duplicate_of_account"]["after"] == "Bellhaven of Owosso (A3)"
    assert (dup["status"]["now"], dup["status"]["after"]) == ("Active", "Inactive")
    gone = {r["field"]: r["after"] for r in present.change_rows(ps["not_on_website"], names)}
    assert gone["status"] == "Needs Review"


def test_create_and_chow_rows_list_the_new_account():
    ps, names = queue()
    create = present.change_rows(ps["create"], names)
    assert all(r["new"] for r in create)
    assert {r["field"]: r["after"] for r in create}["parent_id"] == "Bellhaven Senior Living (Parent Account)"
    chow = present.change_rows(ps["chow"], names)
    assert {r["group"] for r in chow} == {"New account", "Old account A2"}
    link = chow[-1]
    assert (link["field"], link["now"], link["after"]) == ("chow_current_account", None, "the new account's id")


def test_an_applied_chow_shows_and_links_the_created_account():
    ps, _ = queue()
    chow = ps["chow"] | {"status": "applied",
                         "result": {"created": "NEW1", "old_account": "A2", "mismatches": {"new_account": {}, "link": {}}}}
    names = present.account_names([chow])
    assert present.change_rows(chow, names)[-1]["after"] == "Bellhaven of Tiffin (NEW1)"
    assert present.linked_account(chow) == "NEW1"
    assert present.linked_account(ps["chow"]) == "A2"
    assert present.linked_account(ps["create"]) is None  # nothing to link to until it is created


def test_side_by_side_flags_the_fields_that_differ():
    ps, names = queue()
    flagged = lambda cells: [c["label"] for c in cells if c["differs"]]
    side = present.side_by_side(ps["update"], names)
    assert flagged(side["website"]) == ["Name"] and flagged(side["crm"]) == ["Name"]
    chow = present.side_by_side(ps["chow"], names)
    assert flagged(chow["crm"]) == ["Parent"]
    ar = {c["label"]: c for c in chow["crm"]}["Outstanding AR"]
    assert (ar["value"], ar["alert"]) == ("$12,400", True)
    assert present.side_by_side(ps["create"], names)["crm"] is None
    assert present.side_by_side(ps["not_on_website"], names)["website"] is None


def test_sop_box_only_when_the_parent_changes():
    ps, _ = queue()
    assert present.sop(ps["chow"])["text"] == ("Lifetime revenue $84,000, outstanding AR $12,400: "
                                               "keep the old account and use a change of ownership")
    assert present.sop(ps["update"]) is None
    assert present.sop(ps["create"]) is None


def test_signals_become_chips():
    ps, _ = queue()
    sig = present.signals(ps["update"])
    assert sig["score"] == 85 and "street +50" in sig["chips"]
    assert present.signals(ps["create"])["score"] is None


def test_queue_order_puts_the_kinds_that_add_an_account_first():
    ps, _ = queue()
    ordered = present.queue_order(list(ps.values()))
    assert [p["kind"] for p in ordered] == ["chow", "create", "duplicate", "not_on_website", "update"]


def test_next_pending_follows_queue_order_and_wraps_round():
    ps, _ = queue()
    rows = list(ps.values())
    key = lambda kind: ps[kind]["key"]
    assert present.next_pending(rows, key("chow")) == key("create")
    assert present.next_pending(rows, key("update")) == key("chow")
    decided = [p | {"status": "applied"} if p["kind"] in ("create", "duplicate") else p for p in rows]
    assert present.next_pending(decided, key("create")) == key("not_on_website")
    assert present.next_pending([ps["update"] | {"status": "applied"}], key("update")) is None
    assert present.position(rows, key("duplicate")) == (3, 5)
    assert present.position(decided, key("create")) is None


def test_queue_search_matches_name_or_city():
    ps, _ = queue()
    assert present.matches(ps["create"], "batavia") and present.matches(ps["update"], "TESTVILLE")
    assert not present.matches(ps["update"], "owosso")


def test_banners_are_read_from_the_stored_result():
    ps, names = queue()
    applied = ps["update"] | {"status": "applied", "result": {"updated": "A1", "mismatches": {}}}
    b = present.banner(applied, "applied", names)
    assert (b["tone"], b["title"], b["account_id"]) == ("ok", "Written", "A1")
    assert b["text"] == "Name → Bellhaven of Chagrin Falls. The CRM stored exactly what was sent."
    odd = applied | {"result": {"updated": "A1", "mismatches": {"name": {"sent": "X", "returned": "x"}}}}
    assert present.banner(odd, "applied", names)["details"] == ["Name sent 'X', the CRM stored 'x'"]
    stale = ps["update"] | {"status": "failed", "result": {"stale": "name is now 'Y' (the proposal expected 'Z')"}}
    assert present.banner(stale, "stale", names)["title"] == "Not written"
    failed = ps["chow"] | {"status": "failed", "result": {"error": "CrmError: boom"}}
    assert present.banner(failed, "failed", names)["retry"] is True
    busy = present.banner(ps["update"] | {"status": "applying"}, "already_handled", names)
    assert busy["text"].startswith("It is applying now")
    assert present.banner(ps["update"], "made-up", names) is None
```

- [ ] **Step 3: Run them to make sure they fail**

Run: `python -m pytest tests/test_present.py -q`
Expected: an error while collecting, `ImportError: cannot import name 'present' from 'bellhaven.web'`.

- [ ] **Step 4: Write `present.py`**

**Create `bellhaven/web/present.py`**:

```python
"""What the review pages show, worked out from stored proposals. Pure: no database, no network, no HTML."""
import json
import math
from datetime import datetime

from ..config import BELLHAVEN_PARENT_NAME
from ..normalize import crm_care_types, norm_phone, norm_street, same_text, zip5
from ..sop import amount

KIND_LABELS = {"update": "Update", "chow": "Ownership", "create": "New account", "duplicate": "Duplicate",
               "not_on_website": "Not on site"}
# The kinds that add an account (which can't be deleted) come first, then the other status changes.
KIND_ORDER = ("chow", "create", "duplicate", "not_on_website", "update")
FIELD_LABELS = {"name": "Name", "parent_id": "Parent", "billing_street": "Street", "billing_city": "City",
                "billing_state": "State", "billing_zip": "Zip", "care_type": "Care type", "status": "Status",
                "phone": "Phone", "note": "Note", "duplicate_of_account": "Duplicate of",
                "chow_current_account": "CHOW current account"}
SHORT_FIELDS = ("billing_zip", "billing_state", "status")  # summaries show their old value too: "Zip 45626 → 45662"
ACCOUNT_ID_FIELDS = ("duplicate_of_account", "chow_current_account")
BANNER_OUTCOMES = ("applied", "already_applied", "stale", "failed", "already_handled", "rejected", "unrecorded")
APPLYING_HINT = ("It is being applied right now, or an earlier apply was interrupted. If it stays like this, check "
                 "its change log: a pipeline run started 10 or more minutes from now puts it back in the queue if "
                 "the change didn't land.")


def kind_label(kind: str) -> str:
    return KIND_LABELS.get(kind, kind)


def field_label(field: str) -> str:
    return FIELD_LABELS.get(field, field.replace("_", " ").capitalize())


def parse(row) -> dict:
    """A stored proposal as a plain dict, with its JSON columns decoded."""
    p = dict(row)
    for column in ("changes", "before", "evidence"):
        p[column] = json.loads(p[column])
    p["result"] = json.loads(p["result"]) if p.get("result") else None
    return p


def new_account(p) -> dict:
    """The account a create or a CHOW adds; empty for the other kinds."""
    if p["kind"] == "create":
        return p["changes"]
    if p["kind"] == "chow":
        return p["changes"]["new_account"]
    return {}


def account_names(proposals) -> dict:
    """Account id -> name, from everything the stored proposals know. Used to show ids as names."""
    names = {}
    for p in proposals:
        ev = p["evidence"]
        for a in [ev.get("crm") or {}] + [c["account"] for c in ev.get("candidates", [])]:
            if a.get("parent_id") and a.get("parent_name"):
                names[a["parent_id"]] = a["parent_name"]
            if a.get("account_id") and a.get("name"):
                names[a["account_id"]] = a["name"]
        if ev.get("survivor"):
            names[ev["survivor"]["account_id"]] = ev["survivor"]["name"]
        created = (p["result"] or {}).get("created")
        if created and new_account(p).get("name"):
            names[created] = new_account(p)["name"]
    return names


def display_name(p) -> str:
    """The account the reviewer is deciding about: the new account's name for a create or CHOW."""
    return new_account(p).get("name") or (p["evidence"].get("crm") or {}).get("name") or p["title"]


def place(p) -> str:
    """'City, ST' for the queue, from the website if the community is listed there, else from the CRM."""
    site, crm = p["evidence"].get("website") or {}, p["evidence"].get("crm") or {}
    city = site.get("city") or crm.get("billing_city") or ""
    state = site.get("state") or crm.get("billing_state") or ""
    return ", ".join(part for part in (city, state) if part)


def matches(p, q: str) -> bool:
    """The queue search: account name (current or new) or city."""
    needle = (q or "").strip().casefold()
    if not needle:
        return True
    crm = p["evidence"].get("crm") or {}
    return any(needle in (text or "").casefold() for text in (display_name(p), crm.get("name"), place(p)))


def money(value) -> str:
    x = amount(value)
    if math.isinf(x):
        return str(value)
    return f"${x:,.0f}" if x == int(x) else f"${x:,.2f}"


def show(field: str, value, names: dict) -> str | None:
    """A field value as the reviewer should read it, or None when it is empty."""
    if value is None or value == "":
        return None
    if field == "parent_id":
        return names.get(value, value)
    if field in ACCOUNT_ID_FIELDS:
        return f"{names[value]} ({value})" if value in names else str(value)
    if field in ("lifetime_revenue", "outstanding_ar"):
        return money(value)
    return str(value)


def summary(p, names: dict) -> str:
    """The queue's one-line description of the change."""
    kind, changes = p["kind"], p["changes"]
    if kind == "chow":
        return "New Bellhaven account; old one kept for billing"
    if kind == "create":
        return "Create under Bellhaven"
    if kind == "duplicate":
        survivor = changes["duplicate_of_account"]
        name = names.get(survivor, survivor)
        return f"Duplicate of {name}" + (f" ({survivor})" if name == display_name(p) else "")  # same name: tell them apart
    if kind == "not_on_website":
        return f"Status → {changes['status']}"
    parts = []
    for field, value in changes.items():
        if field == "note":
            continue
        after = show(field, value, names) or "empty"
        if field == "parent_id":
            after = after.removesuffix(" (Parent Account)")
        if field in SHORT_FIELDS:
            parts.append(f"{field_label(field)} {show(field, p['before'].get(field), names) or 'empty'} → {after}")
        else:
            parts.append(f"{field_label(field)} → {after}")
    return ", ".join(parts[:2]) + (f" +{len(parts) - 2} more" if len(parts) > 2 else "")


def _row(group: str, field: str, now, after, names: dict, new: bool = False) -> dict:
    return {"group": group, "field": field, "label": field_label(field), "now": show(field, now, names),
            "after": show(field, after, names), "new": new}


def change_rows(p, names: dict) -> list[dict]:
    """The Now → After table: only the fields that change. A create or CHOW lists the new account's fields."""
    kind, changes, before = p["kind"], p["changes"], p["before"]
    if kind not in ("create", "chow"):
        return [_row("", f, before.get(f), v, names) for f, v in changes.items()]
    group = "New account" if kind == "chow" else ""
    rows = [_row(group, f, None, v, names, new=True) for f, v in new_account(p).items()]
    if kind == "chow":
        created = (p["result"] or {}).get("created")
        rows.append(_row(f"Old account {p['account_id']}", "chow_current_account",
                         before.get("chow_current_account"), created or "the new account's id", names))
    return rows


def _differences(site: dict, crm: dict) -> set:
    """Which website-vs-CRM pairs disagree, using the same comparisons as the matching."""
    out = set()
    if not same_text(site["name"], crm.get("name")):
        out.add("name")
    if norm_street(site["street"]) != norm_street(crm.get("billing_street")):
        out.add("street")
    if (not same_text(site["city"], crm.get("billing_city")) or site["state"] != crm.get("billing_state")
            or zip5(site["zip"]) != zip5(crm.get("billing_zip"))):
        out.add("place")
    if norm_phone(site["phone"]) != norm_phone(crm.get("phone")):
        out.add("phone")
    wanted = crm_care_types(site["care_offerings"])
    if wanted and crm.get("care_type") not in wanted:
        out.add("care")
    if crm.get("parent_name") != BELLHAVEN_PARENT_NAME:
        out.add("parent")
    if crm.get("status") != "Active":
        out.add("status")
    return out


def _cell(label: str, value, differs: bool = False, alert: bool = False) -> dict:
    return {"label": label, "value": value if value not in (None, "") else None, "differs": differs, "alert": alert}


def side_by_side(p, names: dict) -> dict:
    """Website and CRM field grids, with the fields that disagree flagged. A side is None when absent."""
    site, crm = p["evidence"].get("website"), p["evidence"].get("crm")
    diff = _differences(site, crm) if site and crm else set()
    website = account = None
    if site:
        website = [_cell("Name", site["name"], "name" in diff), _cell("Street", site["street"], "street" in diff),
                   _cell("City / State / Zip", f"{site['city']}, {site['state']} {site['zip']}", "place" in diff),
                   _cell("Phone", site["phone"], "phone" in diff), _cell("Administrator", site["administrator"]),
                   _cell("Care offerings", ", ".join(site["care_offerings"]), "care" in diff)]
    if crm:
        csz = f"{crm.get('billing_city') or ''}, {crm.get('billing_state') or ''} {crm.get('billing_zip') or ''}"
        account = [_cell("Name", crm.get("name"), "name" in diff),
                   _cell("Parent", show("parent_id", crm.get("parent_id"), names), "parent" in diff),
                   _cell("Street", crm.get("billing_street"), "street" in diff),
                   _cell("City / State / Zip", csz.strip(" ,"), "place" in diff),
                   _cell("Phone", crm.get("phone"), "phone" in diff),
                   _cell("Care type", crm.get("care_type"), "care" in diff),
                   _cell("Status", crm.get("status"), "status" in diff),
                   _cell("Lifetime revenue", money(crm.get("lifetime_revenue"))),
                   _cell("Outstanding AR", money(crm.get("outstanding_ar")),
                         alert=amount(crm.get("outstanding_ar")) > 0)]
    return {"website": website, "crm": account, "url": (site or {}).get("url")}


def signals(p) -> dict:
    """The match score and its signals; for a create, the weak candidates that were not used."""
    ev = p["evidence"]
    chips = lambda found: [f"{name} +{points}" for name, points in (found or {}).items()]
    return {"score": ev.get("score"), "chips": chips(ev.get("signals")),
            "candidates": [{"name": c["account"].get("name"), "account_id": c["account"].get("account_id"),
                            "score": c["score"], "chips": chips(c.get("signals"))} for c in ev.get("candidates", [])]}


def sop(p) -> dict | None:
    """The billing SOP check, shown when the proposal moves an account to another parent."""
    s = p["evidence"].get("sop")
    if not s or not (p["kind"] == "chow" or "parent_id" in p["changes"]):
        return None
    verdict = ("keep the old account and use a change of ownership" if s["preserve_old_account"]
               else "re-parenting allowed")
    return {"preserve": s["preserve_old_account"],
            "text": f"Lifetime revenue {money(s['lifetime_revenue'])}, outstanding AR "
                    f"{money(s['outstanding_ar'])}: {verdict}"}


def queue_order(proposals) -> list:
    """Kinds in KIND_ORDER, then account name."""
    rank = lambda kind: KIND_ORDER.index(kind) if kind in KIND_ORDER else len(KIND_ORDER)
    return sorted(proposals, key=lambda p: (rank(p["kind"]), display_name(p).casefold(), p["key"]))


def position(proposals, key: str) -> tuple[int, int] | None:
    """(n, N) for 'n of N pending', or None when the proposal isn't pending."""
    keys = [p["key"] for p in queue_order([p for p in proposals if p["status"] == "pending"])]
    return (keys.index(key) + 1, len(keys)) if key in keys else None


def next_pending(proposals, after_key: str) -> str | None:
    """The pending proposal after after_key in queue order, wrapping round. None when nothing else is pending."""
    ordered = [p["key"] for p in queue_order([p for p in proposals
                                              if p["status"] == "pending" or p["key"] == after_key])]
    if after_key in ordered:
        i = ordered.index(after_key)
        ordered = ordered[i + 1:] + ordered[:i]
    return ordered[0] if ordered else None


def linked_account(p) -> str | None:
    """The CRM account a proposal's page links to: the account it created once it exists, else the one it changes."""
    return (p["result"] or {}).get("created") or p["account_id"] or None


def failure(p) -> str | None:
    """Why the last attempt failed, as stored on the proposal."""
    result = p["result"] or {}
    return result.get("stale") or result.get("error")


def mismatches(result) -> list[str]:
    """Fields the CRM stored differently from what was sent, one line each."""
    found = (result or {}).get("mismatches") or {}
    if set(found) == {"new_account", "link"}:  # a CHOW: the new account, then the old account's link
        items = [("New account: ", f, d) for f, d in found["new_account"].items()]
        items += [("Old account: ", f, d) for f, d in found["link"].items()]
    else:
        items = [("", f, d) for f, d in found.items()]
    return [f"{where}{field_label(f)} sent {d['sent']!r}, the CRM stored {d['returned']!r}" for where, f, d in items]


def banner(p, outcome: str, names: dict) -> dict | None:
    """The message after a decision, read from the stored proposal. outcome names the decision that was made."""
    if outcome not in BANNER_OUTCOMES:
        return None
    result = p["result"] or {}
    b = {"key": p["key"], "name": p["title"], "details": [], "account_id": None, "retry": False}
    if outcome == "applied":
        lines = mismatches(result)
        text = (f"{summary(p, names)}. "
                + ("The CRM stored exactly what was sent." if not lines else "Some fields were stored differently:"))
        return b | {"tone": "ok", "title": "Written", "text": text, "details": lines, "account_id": linked_account(p)}
    if outcome == "already_applied":
        return b | {"tone": "ok", "title": "Already written", "account_id": linked_account(p),
                    "text": "This change was already in the CRM (an earlier response was lost); it's now recorded."}
    if outcome == "stale":
        return b | {"tone": "warn", "title": "Not written",
                    "text": f"The CRM record changed since this proposal was made: {result.get('stale')}. "
                            "Re-run the pipeline."}
    if outcome == "failed":
        return b | {"tone": "error", "title": "Failed", "text": result.get("error") or "", "retry": True}
    if outcome == "rejected":
        return b | {"tone": "info", "title": "Rejected", "text": ""}
    if outcome == "unrecorded":
        return b | {"tone": "error", "title": "Written, but not recorded",
                    "text": "The CRM write went through, but saving the decision failed. The API log and change "
                            "log have the write; the server output has the error."}
    text = f"It is {p['status']} now, so nothing was done."
    return b | {"tone": "warn", "title": "Already handled",
                "text": text + (" " + APPLYING_HINT if p["status"] == "applying" else "")}


def when(timestamp: str | None) -> str:
    """A stored UTC timestamp in local time, to the minute."""
    if not timestamp:
        return ""
    return datetime.fromisoformat(timestamp).astimezone().strftime("%Y-%m-%d %H:%M")


def run_view(run) -> dict:
    """One pipeline run for the runs page and the queue header."""
    stats = json.loads(run["stats"]) if run["stats"] else {}
    if run["finished_at"]:
        result = "OK"
    elif "error" in stats:
        result = f"Failed: {stats['error']}"
    else:
        result = "Running or interrupted"
    return {"id": run["id"], "started": when(run["started_at"]), "ok": bool(run["finished_at"]),
            "result": result, "stats": stats}
```

- [ ] **Step 5: Run the tests**

Run: `python -m pytest tests/test_present.py -q`
Expected: **13 passed**. Then `python -m pytest -q`: **163 passed**.

- [ ] **Step 6: Commit**

```powershell
git add bellhaven/web/__init__.py bellhaven/web/present.py tests/review_fixtures.py tests/test_present.py
git commit -m "feat: view logic for the review pages" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Approve and reject (`review.py`), and the rehearsal script

`review.approve` reproduces what the Streamlit app's `decide()` did, as a return value instead of a flash message:

| `apply_proposal` result | Stored on the proposal | Outcome |
|---|---|---|
| returns | `applied`, with the result | `applied`, or `already_applied` if the result says so |
| `AlreadyHandled` | nothing | `already_handled` |
| `StaleProposal` | `failed`, `{"stale": message}` | `stale` |
| any other exception | `failed`, `{"error": "Type: message"}` | `failed` |
| returns, but recording `applied` raises | nothing (the API and change logs have the write) | `unrecorded` |

It also returns `already_handled` straight away for a proposal that isn't pending or failed, and `needs_confirmation` on live when `confirmed` is false, in both cases without calling the CRM. It never raises.

**Files:**
- Create: `bellhaven/web/review.py`
- Modify: `mock_crm/approve_all.py` (the whole file)
- Test: `tests/test_review_service.py`, `tests/test_approve_all.py`

- [ ] **Step 1: Write the failing tests**

**Create `tests/test_review_service.py`**:

```python
import json
import sqlite3

from bellhaven import store
from bellhaven.crm import CrmError
from bellhaven.web import review
from tests.review_fixtures import by_kind, make_queue


def setup(kind):
    crm, conn = make_queue()
    return crm, conn, by_kind(conn)[kind]["key"]


def test_approve_writes_and_records_applied():
    crm, conn, key = setup("update")
    assert review.approve(conn, crm, key, "looks right").kind == review.APPLIED
    row = store.get(conn, key)
    assert (row["status"], row["comment"]) == ("applied", "looks right")
    assert crm.accounts["A1"]["name"] == "Bellhaven of Chagrin Falls"


def test_a_change_already_in_the_crm_is_recorded_not_written_again():
    crm, conn, key = setup("update")
    crm.accounts["A1"].update(json.loads(store.get(conn, key)["changes"]))
    assert review.approve(conn, crm, key).kind == review.ALREADY_APPLIED
    assert crm.writes == [] and store.get(conn, key)["status"] == "applied"


def test_a_stale_proposal_fails_with_the_reason_and_writes_nothing():
    crm, conn, key = setup("update")
    crm.accounts["A1"]["name"] = "Someone Renamed It"
    outcome = review.approve(conn, crm, key)
    assert outcome.kind == review.STALE and "Someone Renamed It" in outcome.message
    row = store.get(conn, key)
    assert row["status"] == "failed" and "Someone Renamed It" in json.loads(row["result"])["stale"]
    assert crm.writes == []


def test_a_crm_error_fails_with_the_error():
    crm, conn, key = setup("update")

    def refuse(account_id, body):
        raise CrmError("PATCH /accounts/A1 -> 422: bad body")

    crm.update_account = refuse
    assert review.approve(conn, crm, key).kind == review.FAILED
    assert json.loads(store.get(conn, key)["result"]) == {"error": "CrmError: PATCH /accounts/A1 -> 422: bad body"}


def test_an_unexpected_error_never_escapes():
    crm, conn, key = setup("update")
    crm.get_account = lambda account_id: 1 / 0
    outcome = review.approve(conn, crm, key)
    assert outcome.kind == review.FAILED and outcome.message.startswith("ZeroDivisionError")
    assert store.get(conn, key)["status"] == "failed"


def test_a_decided_proposal_is_already_handled():
    crm, conn, key = setup("update")
    review.approve(conn, crm, key)
    again = review.approve(conn, crm, key)
    assert again.kind == review.ALREADY_HANDLED and "applied" in again.message
    assert len(crm.writes) == 1


def test_a_proposal_being_applied_is_already_handled():
    crm, conn, key = setup("update")
    store.claim(conn, key)  # another click is applying it
    assert review.approve(conn, crm, key).kind == review.ALREADY_HANDLED
    assert crm.writes == []


def test_live_needs_confirmation_before_writing():
    crm, conn, key = setup("create")
    assert review.approve(conn, crm, key, live=True).kind == review.NEEDS_CONFIRMATION
    assert crm.writes == [] and store.get(conn, key)["status"] == "pending"
    assert review.approve(conn, crm, key, live=True, confirmed=True).kind == review.APPLIED
    assert [w[0] for w in crm.writes] == ["POST"]


def test_a_write_that_cannot_be_recorded_says_so(monkeypatch):
    crm, conn, key = setup("update")
    real = store.set_status

    def broken(conn, key, status, *args, **kwargs):
        if status == "applied":
            raise sqlite3.OperationalError("database is locked")
        return real(conn, key, status, *args, **kwargs)

    monkeypatch.setattr(store, "set_status", broken)
    outcome = review.approve(conn, crm, key)
    assert outcome.kind == review.UNRECORDED and "database is locked" in outcome.message
    assert crm.accounts["A1"]["name"] == "Bellhaven of Chagrin Falls"


def test_reject_records_the_decision():
    crm, conn, key = setup("duplicate")
    assert review.reject(conn, key, "separate building").kind == review.REJECTED
    row = store.get(conn, key)
    assert (row["status"], row["comment"]) == ("rejected", "separate building")
    assert crm.writes == []


def test_reject_of_a_decided_proposal_is_already_handled():
    crm, conn, key = setup("update")
    review.approve(conn, crm, key)
    outcome = review.reject(conn, key)
    assert outcome.kind == review.ALREADY_HANDLED and "applied" in outcome.message
    assert store.get(conn, key)["status"] == "applied"
```

**Create `tests/test_approve_all.py`**:

```python
import pytest

from bellhaven import pipeline, store
from mock_crm import approve_all
from tests.review_fixtures import locations, queue_crm

MOCK = "http://localhost:8001/api/v1"


def test_refuses_the_live_sandbox(monkeypatch):
    monkeypatch.setattr(approve_all, "USING_MOCK", False)
    with pytest.raises(SystemExit, match="Refusing"):
        approve_all.main()


def test_applies_every_pending_proposal_on_the_mock(monkeypatch, tmp_path):
    crm, db = queue_crm(), tmp_path / "test.db"
    pipeline.run(store.connect(db, crm_base=MOCK), crm, locations, "2026-11-25")
    monkeypatch.setattr(approve_all, "USING_MOCK", True)
    monkeypatch.setattr(approve_all, "API_BASE", MOCK)
    monkeypatch.setattr(approve_all, "DB_PATH", db)
    monkeypatch.setattr(approve_all, "CrmClient", lambda: crm)
    approve_all.main()
    assert store.counts(store.connect(db)) == {"applied": 5}


def test_reports_what_it_could_not_apply_and_carries_on(monkeypatch, tmp_path):
    crm, db = queue_crm(), tmp_path / "test.db"
    pipeline.run(store.connect(db, crm_base=MOCK), crm, locations, "2026-11-25")
    crm.accounts["A1"]["name"] = "Renamed meanwhile"  # the rename proposal is now stale
    monkeypatch.setattr(approve_all, "USING_MOCK", True)
    monkeypatch.setattr(approve_all, "API_BASE", MOCK)
    monkeypatch.setattr(approve_all, "DB_PATH", db)
    monkeypatch.setattr(approve_all, "CrmClient", lambda: crm)
    with pytest.raises(SystemExit, match="Update Riverbend Manor: stale"):
        approve_all.main()
    assert store.counts(store.connect(db)) == {"applied": 4, "failed": 1}
```

- [ ] **Step 2: Run them to make sure they fail**

Run: `python -m pytest tests/test_review_service.py tests/test_approve_all.py -q`
Expected: the run stops while collecting `test_review_service.py`, with `ImportError: cannot import name 'review' from 'bellhaven.web'`.

Run: `python -m pytest tests/test_approve_all.py -q`
Expected: 1 failed, 2 passed. `test_reports_what_it_could_not_apply_and_carries_on` fails with `StaleProposal`, because the old script stops at the first error.

- [ ] **Step 3: Write `review.py`**

**Create `bellhaven/web/review.py`**:

```python
"""Approve or reject one proposal and record the outcome. apply.apply_proposal does the CRM writing."""
from dataclasses import dataclass

from .. import store
from ..apply import AlreadyHandled, StaleProposal, apply_proposal

APPLIED = "applied"                         # written; the CRM's response is stored on the proposal
ALREADY_APPLIED = "already_applied"         # the CRM already had the change (an earlier response was lost)
STALE = "stale"                             # the record changed since the proposal was made: nothing written
FAILED = "failed"                           # the CRM refused or the call failed: the error is stored
ALREADY_HANDLED = "already_handled"         # decided already, or another click is applying it: nothing done
REJECTED = "rejected"
NEEDS_CONFIRMATION = "needs_confirmation"   # live sandbox, not confirmed yet: nothing written
UNRECORDED = "unrecorded"                   # written, but saving the decision failed


@dataclass(frozen=True)
class Outcome:
    kind: str           # one of the constants above
    key: str
    message: str = ""
    result: dict | None = None


def _record_failure(conn, key: str, comment: str | None, result: dict) -> None:
    try:
        store.set_status(conn, key, "failed", comment, result)
    except Exception:  # the outcome still reaches the reviewer; the API log has any write attempt
        pass


def approve(conn, client, key: str, comment: str | None = None, confirmed: bool = False,
            live: bool = False) -> Outcome:
    """Write one proposal to the CRM. Never raises: every failure becomes an outcome, and is stored on the proposal.

    On the live sandbox nothing is written until the reviewer has confirmed (confirmed=True).
    """
    comment = comment or None
    row = store.get(conn, key)
    if row is None:
        return Outcome(ALREADY_HANDLED, key, "There is no such proposal.")
    if row["status"] not in store.UNDECIDED:
        return Outcome(ALREADY_HANDLED, key, f"'{row['title']}' is {row['status']}, not pending or failed.")
    if live and not confirmed:
        return Outcome(NEEDS_CONFIRMATION, key)
    try:
        result = apply_proposal(conn, client, row)  # it claims the proposal first, so a second click can't write twice
    except AlreadyHandled as e:
        return Outcome(ALREADY_HANDLED, key, str(e))
    except StaleProposal as e:
        _record_failure(conn, key, comment, {"stale": str(e)})
        return Outcome(STALE, key, str(e))
    except Exception as e:  # any API or unexpected error: keep it on the proposal for the reviewer
        message = f"{type(e).__name__}: {e}"
        _record_failure(conn, key, comment, {"error": message})
        return Outcome(FAILED, key, message)
    try:
        store.set_status(conn, key, "applied", comment, result)
    except Exception as e:  # the CRM write succeeded; only recording it failed
        return Outcome(UNRECORDED, key, f"{type(e).__name__}: {e}", result)
    return Outcome(ALREADY_APPLIED if result.get("already_applied") else APPLIED, key, "", result)


def reject(conn, key: str, comment: str | None = None) -> Outcome:
    """Reject a pending or failed proposal. It is never proposed again."""
    if store.reject(conn, key, comment or None):
        return Outcome(REJECTED, key)
    row = store.get(conn, key)
    status = row["status"] if row else "missing"
    return Outcome(ALREADY_HANDLED, key, f"Not rejected: it is {status} now.")
```

- [ ] **Step 4: Switch the rehearsal script to it**

**Create `mock_crm/approve_all.py`** (replace the whole file):

```python
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
```

- [ ] **Step 5: Run the tests**

Run: `python -m pytest tests/test_review_service.py tests/test_approve_all.py -q`
Expected: **14 passed**. Then `python -m pytest -q`: **177 passed**.

- [ ] **Step 6: Commit**

```powershell
git add bellhaven/web/review.py mock_crm/approve_all.py tests/test_review_service.py tests/test_approve_all.py
git commit -m "feat: approve and reject as outcomes; rehearsal uses them" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: The web app: routes, templates and styles

`create_app(conn, client, live, meridian_base, allowed_origins=None)` builds the FastAPI app. Tests pass a `FakeCrm` and an in-memory database; `build()` reads `.env` for the real thing.

**Safety, all enforced on the server:**
- Only the two POST routes change anything.
- Every POST needs the per-process CSRF token from the form's hidden field (403 otherwise).
- A POST whose `Origin`, or else `Referer`, names another site gets a 403. With neither header it's allowed, because the token still applies.
- On live, approve writes nothing until the form says `confirmed=yes`. Without it, the route returns the confirmation page.
- uvicorn binds to 127.0.0.1 only. Jinja2 autoescapes everything.
- The Meridian links (which contain the token) appear only on live.

**Files:**
- Modify: `requirements.txt`
- Create: `bellhaven/web/app.py`, `bellhaven/web/__main__.py`
- Create: `bellhaven/web/templates/base.html`, `queue.html`, `proposal.html`, `_confirm_body.html`, `confirm.html`, `history.html`, `runs.html`, `notfound.html`
- Create: `bellhaven/web/static/meridian.css`, `bellhaven/web/static/review.js`
- Test: `tests/test_web.py`

- [ ] **Step 1: Declare the two libraries the app needs.** Both are already installed in the virtual environment (Streamlit pulled them in), so nothing needs installing; this makes them explicit before Streamlit is removed in Task 6.

**Replace in `requirements.txt`**, this exact text (it appears once):

```text
streamlit>=1.38
```

with:

```text
streamlit>=1.38
jinja2>=3.1
python-multipart>=0.0.9
```

- [ ] **Step 2: Write the failing tests**

**Create `tests/test_web.py`**:

```python
from fastapi.testclient import TestClient

from bellhaven import config, store
from bellhaven.crm import CrmError
from bellhaven.web.app import build, create_app
from tests.review_fixtures import by_kind, make_queue

MERIDIAN = "https://crm.test/crm/tok_secret"


def client_for(live=False):
    crm, conn = make_queue()
    app = create_app(conn, crm, live=live, meridian_base=MERIDIAN if live else None)
    return TestClient(app), app, crm, conn


def form(app, **values):
    return {"csrf_token": app.state.csrf_token} | values


def state(conn, crm):
    """Everything a GET must leave unchanged."""
    proposals = [tuple(r) for r in conn.execute("SELECT key, status, decided_at, comment, result FROM proposals")]
    return proposals, conn.execute("SELECT COUNT(*) FROM api_log").fetchone()[0], list(crm.writes)


def test_every_page_renders_and_no_get_writes():
    http, app, crm, conn = client_for()
    before = state(conn, crm)
    paths = ["/queue", "/queue?status=all", "/queue?kind=chow&q=tiffin", "/history", "/runs"]
    paths += [f"/proposals/{p['key']}" for p in by_kind(conn).values()]
    for path in paths:
        assert http.get(path).status_code == 200, path
    assert http.get("/").url.path == "/queue"
    assert state(conn, crm) == before


def test_an_unknown_proposal_is_404_with_a_way_back():
    http, *_ = client_for()
    r = http.get("/proposals/nope")
    assert r.status_code == 404 and 'href="/queue"' in r.text


def test_queue_lists_pending_proposals_in_order_with_summaries():
    http, *_ = client_for()
    page = http.get("/queue").text
    names = ["Bellhaven of Tiffin", "Bellhaven of Batavia", "Second Bellhaven of Owosso", "Bellhaven of Coldwater",
             "Riverbend Manor"]
    assert [page.index(n) for n in names] == sorted(page.index(n) for n in names)
    assert "Name → Bellhaven of Chagrin Falls" in page and "Duplicate of Bellhaven of Owosso" in page


def test_queue_filters_by_kind_and_search():
    http, *_ = client_for()
    page = http.get("/queue?kind=duplicate").text
    assert "Second Bellhaven of Owosso" in page and "Riverbend Manor" not in page
    page = http.get("/queue?q=coldwater").text
    assert "Bellhaven of Coldwater" in page and "Riverbend Manor" not in page


def test_approve_on_the_mock_writes_and_moves_to_the_next_pending():
    http, app, crm, conn = client_for()
    ps = by_kind(conn)
    chow, create = ps["chow"]["key"], ps["create"]["key"]
    r = http.post(f"/proposals/{chow}/approve", data=form(app, comment="SOP applies"), follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == f"/proposals/{create}?last={chow}&outcome=applied"
    assert [w[0] for w in crm.writes] == ["POST", "PATCH"]
    assert store.get(conn, chow)["comment"] == "SOP applies"
    page = http.get(r.headers["location"]).text
    assert "Written" in page and "The CRM stored exactly what was sent." in page


def test_the_last_decision_goes_back_to_the_queue():
    http, app, crm, conn = client_for()
    for p in by_kind(conn).values():
        r = http.post(f"/proposals/{p['key']}/approve", data=form(app), follow_redirects=False)
    assert r.headers["location"].startswith("/queue?last=")
    assert "No pending proposals" in http.get(r.headers["location"]).text


def test_live_approve_asks_for_confirmation_first():
    http, app, crm, conn = client_for(live=True)
    key = by_kind(conn)["create"]["key"]
    r = http.post(f"/proposals/{key}/approve", data=form(app, comment="new community"))
    assert r.status_code == 200
    assert "Write this to the live sandbox?" in r.text and "Creates 1 account that can't be deleted." in r.text
    assert 'name="confirmed" value="yes"' in r.text and 'value="new community"' in r.text
    assert crm.writes == [] and store.get(conn, key)["status"] == "pending"
    r = http.post(f"/proposals/{key}/approve", data=form(app, comment="new community", confirmed="yes"),
                  follow_redirects=False)
    assert r.status_code == 303 and [w[0] for w in crm.writes] == ["POST"]


def test_the_mock_ignores_confirmation():
    http, app, crm, conn = client_for()
    key = by_kind(conn)["create"]["key"]
    assert http.post(f"/proposals/{key}/approve", data=form(app), follow_redirects=False).status_code == 303
    assert [w[0] for w in crm.writes] == ["POST"]


def test_posts_without_the_token_or_from_another_site_are_refused():
    http, app, crm, conn = client_for()
    key = by_kind(conn)["update"]["key"]
    for path in (f"/proposals/{key}/approve", f"/proposals/{key}/reject"):
        assert http.post(path, data={"comment": "x"}).status_code == 403
        assert http.post(path, data={"csrf_token": "wrong"}).status_code == 403
        assert http.post(path, data=form(app), headers={"Origin": "https://evil.test"}).status_code == 403
        assert http.post(path, data=form(app), headers={"Referer": "https://evil.test/page"}).status_code == 403
    assert crm.writes == [] and store.get(conn, key)["status"] == "pending"
    ok = http.post(f"/proposals/{key}/reject", data=form(app), headers={"Origin": "http://127.0.0.1:8000"},
                   follow_redirects=False)
    assert ok.status_code == 303


def test_reject_is_recorded_and_guarded():
    http, app, crm, conn = client_for()
    key = by_kind(conn)["duplicate"]["key"]
    r = http.post(f"/proposals/{key}/reject", data=form(app, comment="separate building"), follow_redirects=False)
    assert r.status_code == 303 and "outcome=rejected" in r.headers["location"]
    assert store.get(conn, key)["status"] == "rejected"
    again = http.post(f"/proposals/{key}/reject", data=form(app), follow_redirects=False)
    assert "outcome=already_handled" in again.headers["location"]
    assert "Already handled" in http.get(again.headers["location"]).text
    assert crm.writes == []


def test_a_failed_write_shows_the_error_and_a_retry_button():
    http, app, crm, conn = client_for()
    key = by_kind(conn)["update"]["key"]

    def refuse(account_id, body):
        raise CrmError("PATCH /accounts/A1 -> 422: bad body")

    crm.update_account = refuse
    page = http.post(f"/proposals/{key}/approve", data=form(app)).text
    assert "Failed" in page and "422: bad body" in page and ">Retry</button>" in page
    assert "Last attempt failed at" in http.get(f"/proposals/{key}").text


def test_history_shows_each_written_field_newest_first():
    http, app, crm, conn = client_for()
    ps = by_kind(conn)
    for kind in ("update", "duplicate"):
        http.post(f"/proposals/{ps[kind]['key']}/approve", data=form(app))
    page = http.get("/history").text
    assert page.index("Second Bellhaven of Owosso") < page.index("Riverbend Manor")
    assert "Bellhaven of Chagrin Falls" in page and "Bellhaven of Owosso (A3)" in page
    assert "Riverbend Manor" in http.get("/history?q=a1").text
    assert "Second Bellhaven" not in http.get("/history?q=riverbend").text


def test_runs_page_and_queue_header_show_a_failed_run():
    http, app, crm, conn = client_for()
    store.fail_run(conn, store.start_run(conn), "CrmError: timeout")
    page = http.get("/runs").text
    assert "Failed: CrmError: timeout" in page and ">OK<" in page
    assert "Failed: CrmError: timeout" in http.get("/queue").text


def test_environment_badge_and_meridian_links():
    mock, *_ = client_for()
    page = mock.get("/queue").text
    assert "Mock CRM" in page and "LIVE SANDBOX" not in page and "/crm/" not in page
    live, app, crm, conn = client_for(live=True)
    page = live.get(f"/proposals/{by_kind(conn)['update']['key']}").text
    assert "LIVE SANDBOX" in page and f'href="{MERIDIAN}/accounts/A1"' in page


def test_the_token_never_appears_on_mock_pages(monkeypatch, tmp_path):
    monkeypatch.setenv("BH_API_TOKEN", "tok_do_not_show")
    monkeypatch.setattr(config, "USING_MOCK", True)
    monkeypatch.setattr(config, "API_BASE", "http://localhost:8001/api/v1")
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    http = TestClient(build())
    for path in ("/queue", "/history", "/runs"):
        assert "tok_do_not_show" not in http.get(path).text
```

- [ ] **Step 3: Run them to make sure they fail**

Run: `python -m pytest tests/test_web.py -q`
Expected: an error while collecting, `ModuleNotFoundError: No module named 'bellhaven.web.app'`.

- [ ] **Step 4: Write the app and its entry point**

**Create `bellhaven/web/app.py`**:

```python
"""The review app: a queue of proposals, one page per proposal, the change history and the pipeline runs.

Only the two POST routes can change anything, and only through review.approve and review.reject.
"""
import secrets
import threading
from pathlib import Path
from urllib.parse import urlencode, urlsplit

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader

from .. import config, store
from ..crm import CrmClient
from . import present, review

HERE = Path(__file__).parent
DEFAULT_ORIGINS = ("http://127.0.0.1:8000", "http://localhost:8000")
TABS = ("pending", "applied", "rejected", "failed", "superseded", "all")


def create_app(conn, client, live: bool, meridian_base: str | None, allowed_origins=None) -> FastAPI:
    """live: the CRM is the graded sandbox, so every approval asks for confirmation first.
    meridian_base: the CRM browser address up to /accounts (it holds the token), or None for no links."""
    app = FastAPI(title="Bellhaven review", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.csrf_token = secrets.token_urlsafe(32)
    origins = set(allowed_origins or DEFAULT_ORIGINS)
    lock = threading.Lock()  # requests run in worker threads; they take turns on the one SQLite connection
    templates = Jinja2Templates(env=Environment(loader=FileSystemLoader(HERE / "templates"), autoescape=True))
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    meridian = meridian_base.rstrip("/") if live and meridian_base else None

    def meridian_url(account_id) -> str | None:
        return f"{meridian}/accounts/{account_id}" if meridian and account_id else None

    templates.env.globals.update(meridian_url=meridian_url, kind_label=present.kind_label,
                                 field_label=present.field_label, when=present.when, show=present.show)

    def page(request: Request, template: str, status_code: int = 200, /, **context):
        context |= {"live": live, "meridian_home": f"{meridian}/accounts" if meridian else None,
                    "pending": store.counts(conn).get("pending", 0), "csrf_token": app.state.csrf_token}
        return templates.TemplateResponse(request, template, context, status_code=status_code)

    def everything():
        rows = [present.parse(r) for r in store.list_proposals(conn)]
        return rows, present.account_names(rows)

    def banner(rows, names, last: str, outcome: str):
        p = next((r for r in rows if r["key"] == last), None)
        return present.banner(p, outcome, names) if p else None

    def not_found(request: Request, key: str):
        return page(request, "notfound.html", 404, key=key, nav="queue")

    def check_post(request: Request, token: str) -> None:
        sent = request.headers.get("origin")
        if not sent and request.headers.get("referer"):
            parts = urlsplit(request.headers["referer"])
            sent = f"{parts.scheme}://{parts.netloc}"
        if sent and sent not in origins:
            raise HTTPException(403, "This request came from another site, so it was refused.")
        if not secrets.compare_digest(token.encode(), app.state.csrf_token.encode()):
            raise HTTPException(403, "This form is out of date. Reload the page and try again.")

    def after_decision(key: str, outcome: str) -> RedirectResponse:
        rows, _ = everything()
        target = present.next_pending(rows, key)
        path = f"/proposals/{target}" if target else "/queue"
        return RedirectResponse(f"{path}?{urlencode({'last': key, 'outcome': outcome})}", status_code=303)

    @app.get("/")
    def home():
        return RedirectResponse("/queue", status_code=303)

    @app.get("/queue", response_class=HTMLResponse)
    def queue(request: Request, status: str = "pending", kind: str = "", q: str = "", last: str = "",
              outcome: str = ""):
        status = status if status in TABS else "pending"
        with lock:
            rows, names = everything()
            counts = store.counts(conn)
            run = store.latest_run(conn)
            shown = [p for p in rows if (status == "all" or p["status"] == status)
                     and (not kind or p["kind"] == kind) and present.matches(p, q)]
            items = [{"p": p, "name": present.display_name(p), "place": present.place(p),
                      "summary": present.summary(p, names)} for p in present.queue_order(shown)]
            tabs = [(t, t.capitalize(), sum(counts.values()) if t == "all" else counts.get(t, 0)) for t in TABS]
            return page(request, "queue.html", nav="queue", items=items, tabs=tabs, status=status, kind=kind, q=q,
                        kinds=present.KIND_LABELS, run=present.run_view(run) if run else None,
                        banner=banner(rows, names, last, outcome))

    @app.get("/proposals/{key}", response_class=HTMLResponse)
    def proposal(request: Request, key: str, last: str = "", outcome: str = ""):
        with lock:
            rows, names = everything()
            p = next((r for r in rows if r["key"] == key), None)
            if p is None:
                return not_found(request, key)
            decided = p["status"] not in store.UNDECIDED
            return page(request, "proposal.html", nav="queue", p=p, name=present.display_name(p),
                        rows=present.change_rows(p, names), side=present.side_by_side(p, names),
                        sig=present.signals(p), sop=present.sop(p), position=present.position(rows, key),
                        linked=present.linked_account(p), failure=present.failure(p),
                        mismatches=present.mismatches(p["result"]) if p["status"] == "applied" else [],
                        history=store.change_history(conn, proposal_key=key) if decided else [],
                        names=names, next_key=present.next_pending(rows, key), applying_hint=present.APPLYING_HINT,
                        banner=banner(rows, names, last, outcome))

    @app.post("/proposals/{key}/approve")
    def approve(request: Request, key: str, comment: str = Form(""), confirmed: str = Form(""),
                csrf_token: str = Form("")):
        check_post(request, csrf_token)
        with lock:
            if store.get(conn, key) is None:
                return not_found(request, key)
            outcome = review.approve(conn, client, key, comment, confirmed == "yes", live)
            if outcome.kind == review.NEEDS_CONFIRMATION:
                rows, names = everything()
                p = next(r for r in rows if r["key"] == key)
                return page(request, "confirm.html", nav="queue", p=p, name=present.display_name(p),
                            rows=present.change_rows(p, names), comment=comment)
            return after_decision(key, outcome.kind)

    @app.post("/proposals/{key}/reject")
    def reject(request: Request, key: str, comment: str = Form(""), csrf_token: str = Form("")):
        check_post(request, csrf_token)
        with lock:
            if store.get(conn, key) is None:
                return not_found(request, key)
            return after_decision(key, review.reject(conn, key, comment).kind)

    @app.get("/history", response_class=HTMLResponse)
    def history(request: Request, q: str = ""):
        with lock:
            _, names = everything()
            needle = q.strip().casefold()
            entries = []
            for h in reversed(store.change_history(conn)):
                name = names.get(h["account_id"], h["account_id"])
                if needle and needle not in name.casefold() and needle not in h["account_id"].casefold():
                    continue
                entries.append(dict(h) | {"account_name": name})
            return page(request, "history.html", nav="history", entries=entries, q=q, names=names)

    @app.get("/runs", response_class=HTMLResponse)
    def runs(request: Request):
        with lock:
            return page(request, "runs.html", nav="runs", runs=[present.run_view(r) for r in store.list_runs(conn)])

    return app


def build() -> FastAPI:
    """The app for uvicorn, from the settings in .env: the database, and the CRM it is tied to."""
    live = not config.USING_MOCK
    conn = store.connect(config.DB_PATH, crm_base=config.API_BASE)
    meridian = f"{config.SITE_BASE}/crm/{config.api_token()}" if live else None
    return create_app(conn, CrmClient(base=config.API_BASE), live, meridian)
```

**Create `bellhaven/web/__main__.py`**:

```python
"""Start the review app at http://127.0.0.1:8000. It listens on this machine only."""
import uvicorn

from .app import build


def main() -> None:
    try:
        app = build()
    except RuntimeError as e:  # a database tied to another CRM, or no token set
        raise SystemExit(str(e)) from None
    uvicorn.run(app, host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Write the templates.** `base.html` holds the top bar (with the environment badge) and the banner. Every other page extends it. `_confirm_body.html` is shared by the dialog on the proposal page and by the no-JavaScript confirmation page.

**Create `bellhaven/web/templates/base.html`**:

```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>{% block title %}Bellhaven review{% endblock %}</title>
<link rel="stylesheet" href="/static/meridian.css">
</head>
<body>
<div class="topbar">
  <b>Bellhaven review</b>
  <a href="/queue" class="{{ 'on' if nav == 'queue' }}">Queue <span class="count">{{ pending }}</span></a>
  <a href="/history" class="{{ 'on' if nav == 'history' }}">Change history</a>
  <a href="/runs" class="{{ 'on' if nav == 'runs' }}">Pipeline runs</a>
  {% if meridian_home %}<a href="{{ meridian_home }}" target="_blank" rel="noopener noreferrer">Open Meridian ↗</a>{% endif %}
  <span class="env {{ 'live' if live else 'mock' }}">{{ 'LIVE SANDBOX' if live else 'Mock CRM' }}</span>
</div>
<div class="wrap">
{% if banner %}
<div class="banner {{ banner.tone }}" role="status">
  <b>{{ banner.title }}</b> · <a href="/proposals/{{ banner.key }}">{{ banner.name }}</a>{% if banner.text %} · {{ banner.text }}{% endif %}
  {% if banner.details %}<ul>{% for line in banner.details %}<li>{{ line }}</li>{% endfor %}</ul>{% endif %}
  {% set link = meridian_url(banner.account_id) %}
  {% if link %} <a href="{{ link }}" target="_blank" rel="noopener noreferrer">View in Meridian ↗</a>{% endif %}
  {% if banner.retry %}
  <form method="post" action="/proposals/{{ banner.key }}/approve" class="inline">
    <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
    <button type="submit">Retry</button>
  </form>
  {% endif %}
</div>
{% endif %}
{% block content %}{% endblock %}
</div>
<script src="/static/review.js" defer></script>
</body>
</html>
```

**Create `bellhaven/web/templates/queue.html`**:

```html
{% extends "base.html" %}
{% block content %}
<h1>Review queue</h1>
<p class="muted">
{%- if run and run.ok %}Last pipeline run {{ run.started }}: {{ run.stats.locations }} communities on the website, {{ run.stats.accounts }} CRM accounts, {{ run.stats.proposals }} proposals.
{%- elif run %}Last pipeline run {{ run.started }}: {{ run.result }}
{%- else %}No pipeline run yet. Run <code>python -m bellhaven.pipeline</code> to fill the queue.{% endif -%}
</p>
<div class="tabs">
{% for value, label, count in tabs %}<a href="/queue?status={{ value }}" class="{{ 'on' if value == status }}">{{ label }} <span class="count">{{ count }}</span></a>{% endfor %}
</div>
<form class="searchrow" method="get" action="/queue">
  <input type="hidden" name="status" value="{{ status }}">
  <select name="kind" aria-label="Kind">
    <option value="">All kinds</option>
    {% for value, label in kinds.items() %}<option value="{{ value }}" {{ 'selected' if value == kind }}>{{ label }}</option>{% endfor %}
  </select>
  <input type="text" name="q" value="{{ q }}" placeholder="Search account or city" aria-label="Search">
  <button type="submit">Filter</button>
  {% if kind or q %}<a class="clear" href="/queue?status={{ status }}">Clear</a>{% endif %}
</form>
{% if items %}
<div class="tablebox">
<table class="queue">
  <thead><tr><th>Kind</th><th>Account</th><th>Change</th><th>Status</th></tr></thead>
  <tbody>
  {% for it in items %}
  <tr data-href="/proposals/{{ it.p.key }}">
    <td><span class="kind {{ it.p.kind }}">{{ kind_label(it.p.kind) }}</span></td>
    <td><a href="/proposals/{{ it.p.key }}">{{ it.name }}</a>{% if it.p.confidence == 'low' %} <span class="careful">Review carefully</span>{% endif %}
      <div class="sub">{{ it.place }}{% if it.p.account_id %} · {{ it.p.account_id }}{% endif %}</div></td>
    <td>{{ it.summary }}</td>
    <td><span class="status {{ it.p.status }}">{{ it.p.status | capitalize }}</span></td>
  </tr>
  {% endfor %}
  </tbody>
</table>
</div>
{% elif status == 'pending' and not kind and not q %}
<div class="empty">No pending proposals. Run the pipeline to check for changes.</div>
{% elif not kind and not q %}
<div class="empty">No {% if status != 'all' %}{{ status }} {% endif %}proposals yet.</div>
{% else %}
<div class="empty">Nothing matches these filters.</div>
{% endif %}
{% endblock %}
```

**Create `bellhaven/web/templates/proposal.html`**:

```html
{% extends "base.html" %}
{% block title %}{{ name }} · Bellhaven review{% endblock %}
{% block content %}
<div class="crumb"><a href="/queue">Queue</a> / {{ kind_label(p.kind) }}
  {%- if position %} <span class="muted">· {{ position[0] }} of {{ position[1] }} pending</span>{% endif %}
  {%- if next_key %} <a id="next" class="nextlink" href="/proposals/{{ next_key }}">Next pending →</a>{% endif %}</div>
<div class="heading">
  <h1>{{ name }}</h1>
  <span class="kind {{ p.kind }}">{{ kind_label(p.kind) }}</span>
  <span class="status {{ p.status }}">{{ p.status | capitalize }}</span>
  {% set link = meridian_url(linked) %}
  {% if link %}<a class="ext" href="{{ link }}" target="_blank" rel="noopener noreferrer">View in Meridian ↗</a>{% endif %}
</div>
<p class="muted">{{ p.title }}{% if p.account_id %} · {{ p.account_id }}{% endif %}</p>

<div class="why">
  <div class="lbl">Why</div>
  {% for reason in p.evidence.reasons %}<p>{{ reason }}</p>{% endfor %}
  {% if p.confidence == 'low' %}<p><span class="careful">Review carefully</span> Low confidence, for the reason above.</p>{% endif %}
</div>

{% if sop %}<div class="sop"><b>Billing SOP</b> · {{ sop.text }}</div>{% endif %}

<h2>Now → After</h2>
<div class="tablebox">
<table class="changes">
  <thead><tr><th>Field</th><th>Now</th><th>After</th></tr></thead>
  <tbody>
  {% for r in rows %}
  {% if r.group and (loop.first or r.group != loop.previtem.group) %}<tr class="group"><td colspan="3">{{ r.group }}</td></tr>{% endif %}
  <tr>
    <td class="lbl">{{ r.label }}</td>
    <td>{% if r.new %}<span class="newtag">new</span>{% elif r.now is none %}<span class="emptyval">empty</span>{% else %}<s>{{ r.now }}</s>{% endif %}</td>
    <td>{% if r.after is none %}<span class="emptyval">empty</span>{% else %}<span class="after">{{ r.after }}</span>{% endif %}</td>
  </tr>
  {% endfor %}
  </tbody>
</table>
</div>

<h2>Website vs CRM</h2>
<div class="compare">
  <div>
    <div class="side">Website{% if side.url %} · <a href="{{ side.url }}" target="_blank" rel="noopener noreferrer">community page ↗</a>{% endif %}</div>
    {% if side.website %}
    <div class="detailgrid">
      {% for c in side.website %}<div class="field{{ ' differs' if c.differs }}"><div class="lbl">{{ c.label }}</div><div class="val">{{ c.value if c.value is not none else '—' }}</div></div>{% endfor %}
    </div>
    {% else %}<div class="absent">Not on the website</div>{% endif %}
  </div>
  <div>
    <div class="side">CRM now</div>
    {% if side.crm %}
    <div class="detailgrid">
      {% for c in side.crm %}<div class="field{{ ' differs' if c.differs }}"><div class="lbl">{{ c.label }}</div><div class="val{{ ' alert' if c.alert }}">{{ c.value if c.value is not none else '—' }}</div></div>{% endfor %}
    </div>
    {% else %}<div class="absent">No CRM account</div>{% endif %}
  </div>
</div>

{% if sig.score is not none %}
<p class="signals"><b>Match {{ sig.score }}</b> {% for chip in sig.chips %}<span class="chip">{{ chip }}</span>{% endfor %}</p>
{% endif %}
{% if sig.candidates %}
<h2>Weak matches, not used</h2>
<div class="tablebox">
<table>
  <thead><tr><th>Account</th><th>Score</th><th>Signals</th></tr></thead>
  <tbody>
  {% for c in sig.candidates %}
  <tr><td>{{ c.name }} <span class="sub">{{ c.account_id }}</span></td><td>{{ c.score }}</td>
    <td>{% for chip in c.chips %}<span class="chip">{{ chip }}</span>{% endfor %}</td></tr>
  {% endfor %}
  </tbody>
</table>
</div>
{% endif %}

{% if p.status == 'failed' and failure %}
<div class="banner error"><b>Last attempt failed at {{ when(p.decided_at) }}</b> · {{ failure }}</div>
{% endif %}
{% if p.status == 'applying' %}<div class="banner warn">{{ applying_hint }}</div>{% endif %}

{% if p.status in ('pending', 'failed') %}
<form method="post" action="/proposals/{{ p.key }}/approve" id="decide" class="actions"{% if live %} data-confirm="confirm-dialog"{% endif %}>
  <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
  <input type="hidden" name="confirmed" value="">
  <label for="comment" class="lbl">Reviewer comment (optional)</label>
  <input type="text" id="comment" name="comment" placeholder="Why you approve or reject this">
  <div class="buttons">
    <button type="submit" id="approve">{{ 'Retry' if p.status == 'failed' else 'Approve' }}{{ ' and write to live CRM' if live }}</button>
    <button type="submit" id="reject" class="secondary" formaction="/proposals/{{ p.key }}/reject">Reject</button>
  </div>
  <p class="keys muted"><kbd>A</kbd> approve · <kbd>R</kbd> reject · <kbd>N</kbd> next pending · <kbd>Q</kbd> queue</p>
</form>
{% if live %}
<dialog id="confirm-dialog">
  {% include "_confirm_body.html" %}
  <div class="buttons">
    <button type="button" class="secondary" data-close>Cancel</button>
    <button type="button" class="danger" data-confirm-yes>Write to live CRM</button>
  </div>
</dialog>
{% endif %}
{% else %}
<div class="decided">{{ p.status | capitalize }} at {{ when(p.decided_at) }}{% if p.comment %} · {{ p.comment }}{% endif %}</div>
{% if mismatches %}<div class="banner warn">The CRM stored some fields differently:<ul>{% for line in mismatches %}<li>{{ line }}</li>{% endfor %}</ul></div>{% endif %}
{% if history %}
<h2>Change log</h2>
<div class="tablebox">
<table>
  <thead><tr><th>Account</th><th>Action</th><th>Field</th><th>Before → After</th><th>When</th></tr></thead>
  <tbody>
  {% for h in history %}
  {% set old = show(h.field, h.old_value, names) %}{% set new = show(h.field, h.new_value, names) %}
  <tr>
    <td>{{ h.account_id }}</td><td>{{ h.action }}</td><td>{{ field_label(h.field) }}</td>
    <td class="wrapcell">
      {%- if h.action == 'update' %}{% if old is none %}<span class="emptyval">empty</span>{% else %}<s>{{ old }}</s>{% endif %} → {% endif -%}
      {%- if new is none %}<span class="emptyval">empty</span>{% else %}<span class="after">{{ new }}</span>{% endif %}</td>
    <td class="nowrap">{{ when(h.changed_at) }}</td>
  </tr>
  {% endfor %}
  </tbody>
</table>
</div>
{% endif %}
{% endif %}
{% endblock %}
```

**Create `bellhaven/web/templates/_confirm_body.html`**:

```html
<h1 class="confirm-title">Write this to the live sandbox?</h1>
<p class="muted">This is the graded CRM. It has no delete.</p>
<p><b>{{ name }}</b>{% if p.account_id %} <span class="muted">· {{ p.account_id }}</span>{% endif %}</p>
<table class="changes compact">
  {% for r in rows %}
  <tr><td class="lbl">{% if r.group %}{{ r.group }}: {% endif %}{{ r.label }}</td>
    <td>{% if r.new %}<span class="newtag">new</span>{% elif r.now is none %}<span class="emptyval">empty</span>{% else %}<s>{{ r.now }}</s>{% endif %}
      → {% if r.after is none %}<span class="emptyval">empty</span>{% else %}<span class="after">{{ r.after }}</span>{% endif %}</td></tr>
  {% endfor %}
</table>
{% if p.kind in ('create', 'chow') %}<p class="creates">Creates 1 account that can't be deleted.</p>{% endif %}
```

**Create `bellhaven/web/templates/confirm.html`**:

```html
{% extends "base.html" %}
{% block title %}Confirm · Bellhaven review{% endblock %}
{% block content %}
<div class="confirmbox">
  {% include "_confirm_body.html" %}
  <form method="post" action="/proposals/{{ p.key }}/approve">
    <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
    <input type="hidden" name="confirmed" value="yes">
    <input type="hidden" name="comment" value="{{ comment }}">
    <div class="buttons">
      <a class="button secondary" href="/proposals/{{ p.key }}">Cancel</a>
      <button type="submit" class="danger">Write to live CRM</button>
    </div>
  </form>
</div>
{% endblock %}
```

**Create `bellhaven/web/templates/history.html`**:

```html
{% extends "base.html" %}
{% block title %}Change history · Bellhaven review{% endblock %}
{% block content %}
<h1>Change history</h1>
<p class="muted">Every field changed through the API, newest first.</p>
<form class="searchrow" method="get" action="/history">
  <input type="text" name="q" value="{{ q }}" placeholder="Account name or id" aria-label="Search">
  <button type="submit">Filter</button>
  {% if q %}<a class="clear" href="/history">Clear</a>{% endif %}
</form>
{% if entries %}
<div class="tablebox">
<table>
  <thead><tr><th>When</th><th>Account</th><th>Action</th><th>Field</th><th>Before → After</th><th>Comment</th></tr></thead>
  <tbody>
  {% for e in entries %}
  {% set old = show(e.field, e.old_value, names) %}{% set new = show(e.field, e.new_value, names) %}
  <tr>
    <td class="nowrap">{{ when(e.changed_at) }}</td>
    <td><a href="/proposals/{{ e.proposal_key }}">{{ e.account_name }}</a>
      {%- set link = meridian_url(e.account_id) %}{% if link %} <a class="ext" href="{{ link }}" target="_blank" rel="noopener noreferrer">↗</a>{% endif %}
      <div class="sub">{{ e.account_id }}</div></td>
    <td>{{ e.action }}</td>
    <td>{{ field_label(e.field) }}</td>
    <td class="wrapcell">
      {%- if e.action == 'update' %}{% if old is none %}<span class="emptyval">empty</span>{% else %}<s>{{ old }}</s>{% endif %} → {% endif -%}
      {%- if new is none %}<span class="emptyval">empty</span>{% else %}<span class="after">{{ new }}</span>{% endif %}</td>
    <td>{{ e.reviewer_comment or '' }}</td>
  </tr>
  {% endfor %}
  </tbody>
</table>
</div>
{% elif q %}
<div class="empty">No changes match "{{ q }}".</div>
{% else %}
<div class="empty">Nothing has been written to the CRM yet.</div>
{% endif %}
{% endblock %}
```

**Create `bellhaven/web/templates/runs.html`**:

```html
{% extends "base.html" %}
{% block title %}Pipeline runs · Bellhaven review{% endblock %}
{% block content %}
<h1>Pipeline runs</h1>
<p class="muted">Each run scrapes the website and reads the CRM. It never writes to the CRM.</p>
{% if runs %}
<div class="tablebox">
<table>
  <thead><tr><th>Started</th><th>Result</th><th>Communities</th><th>Accounts</th><th>Contacts</th><th>Proposals</th><th>New / known / superseded</th></tr></thead>
  <tbody>
  {% for r in runs %}
  <tr>
    <td class="nowrap">{{ r.started }}</td>
    <td>{% if r.ok %}<span class="status applied">OK</span>{% else %}<span class="status failed">{{ r.result }}</span>{% endif %}</td>
    {% if r.ok %}
    <td>{{ r.stats.locations }}</td><td>{{ r.stats.accounts }}</td><td>{{ r.stats.contacts }}</td><td>{{ r.stats.proposals }}</td>
    <td>{{ r.stats.new }} / {{ r.stats.already_known }} / {{ r.stats.superseded }}</td>
    {% else %}
    <td colspan="5" class="muted">—</td>
    {% endif %}
  </tr>
  {% endfor %}
  </tbody>
</table>
</div>
{% else %}
<div class="empty">No pipeline run yet. Run <code>python -m bellhaven.pipeline</code>.</div>
{% endif %}
{% endblock %}
```

**Create `bellhaven/web/templates/notfound.html`**:

```html
{% extends "base.html" %}
{% block content %}
<h1>Proposal not found</h1>
<p class="muted">There is no proposal with the key {{ key }}.</p>
<p><a href="/queue">Back to the queue</a></p>
{% endblock %}
```

- [ ] **Step 6: Write the stylesheet and the script.** The first block of rules is copied from Meridian's own pages (colours, top bar, table boxes, uppercase labels, detail grid). The rest is the app's additions. The script is progressive enhancement only: every page works without it.

**Create `bellhaven/web/static/meridian.css`**:

```css
/* Meridian CRM's look (copied from its pages), plus what the review app adds. */
:root { --blue: #1b4f72; --bg: #eef1f4; --ink: #22303a; --line: #d5dce2; --muted: #64748b; }
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--ink); font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif; font-size: 14px; }
.topbar { background: var(--blue); color: #fff; padding: 10px 20px; display: flex; align-items: center; gap: 16px; flex-wrap: wrap; }
.topbar b { font-size: 15px; }
.topbar a { color: #cfe3f3; text-decoration: none; font-size: 13px; }
.topbar a.on, .topbar a:hover { color: #fff; }
.topbar .count { background: rgba(255,255,255,0.18); border-radius: 999px; padding: 1px 8px; font-size: 11.5px; margin-left: 2px; }
.wrap { max-width: 1060px; margin: 0 auto; padding: 22px 20px 70px; }
.searchrow { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; margin-bottom: 16px; }
input[type=text], select { padding: 8px 11px; border: 1px solid #b9c4cd; border-radius: 5px; font-size: 14px; font-family: inherit; }
button, a.button { padding: 8px 16px; background: var(--blue); color: #fff; border: 0; border-radius: 5px; font-size: 13.5px; cursor: pointer; font-family: inherit; text-decoration: none; display: inline-block; }
button:disabled { opacity: 0.6; cursor: wait; }
.tablebox { background: #fff; border: 1px solid var(--line); border-radius: 6px; overflow-x: auto; }
table { border-collapse: collapse; width: 100%; }
th { text-align: left; font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em; color: var(--muted); padding: 9px 12px; border-bottom: 2px solid var(--line); white-space: nowrap; background: #f7f9fa; }
td { padding: 8px 12px; border-bottom: 1px solid #e8edf1; vertical-align: top; }
tr:last-child td { border-bottom: none; }
td a { color: var(--blue); text-decoration: none; font-weight: 600; }
.status { display: inline-block; font-size: 11.5px; font-weight: 600; border-radius: 999px; padding: 2px 10px; white-space: nowrap; }
.status.Active { background: #e2f2e6; color: #1e6e46; }
.status.Inactive { background: #f1e4e4; color: #9c3a3a; }
.status.NeedsReview { background: #f6ecd9; color: #8a5a10; }
.muted { color: var(--muted); }
.detailgrid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 2px 30px; background: #fff; border: 1px solid var(--line); border-radius: 6px; padding: 18px 22px; }
.field { padding: 8px 0; border-bottom: 1px solid #eef2f5; }
.field .lbl, .lbl { font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em; color: var(--muted); }
.field .val { font-size: 14.5px; margin-top: 2px; word-break: break-word; }
h1 { font-size: 22px; margin: 4px 0 2px; }
h2 { font-size: 15px; margin: 26px 0 8px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.04em; }
.crumb { font-size: 13px; margin-bottom: 8px; }
.crumb a { color: var(--blue); text-decoration: none; }
.newtag { background: #e8e4f5; color: #5b3fa8; font-size: 11px; border-radius: 3px; padding: 2px 7px; font-weight: 600; white-space: nowrap; }

/* Review app additions */
.env { margin-left: auto; font-size: 11.5px; font-weight: 700; padding: 3px 10px; border-radius: 999px; letter-spacing: 0.03em; }
.env.mock { background: #e2f2e6; color: #1e6e46; }
.env.live { background: #fcebeb; color: #791f1f; }
.status.pending { background: #f6ecd9; color: #8a5a10; }
.status.applying { background: #e6f1fb; color: #0c447c; }
.status.applied { background: #e2f2e6; color: #1e6e46; }
.status.failed { background: #f1e4e4; color: #9c3a3a; }
.status.rejected, .status.superseded { background: #eef1f4; color: #4a5866; }
.kind { display: inline-block; font-size: 11.5px; font-weight: 600; border-radius: 4px; padding: 2px 8px; white-space: nowrap; }
.kind.update { background: #e6f1fb; color: #0c447c; }
.kind.chow { background: #eeedfe; color: #3c3489; }
.kind.create { background: #e1f5ee; color: #085041; }
.kind.duplicate { background: #faece7; color: #712b13; }
.kind.not_on_website { background: #f1efe8; color: #444441; }
.careful { background: #f6ecd9; color: #8a5a10; font-size: 11px; border-radius: 3px; padding: 1px 6px; font-weight: 600; white-space: nowrap; }
.sub { color: var(--muted); font-size: 12.5px; margin-top: 2px; font-weight: 400; }
.tabs { display: flex; gap: 4px; flex-wrap: wrap; margin: 14px 0 12px; border-bottom: 1px solid var(--line); }
.tabs a { padding: 8px 12px; color: var(--muted); text-decoration: none; border-bottom: 2px solid transparent; margin-bottom: -1px; }
.tabs a.on { color: var(--blue); border-bottom-color: var(--blue); font-weight: 600; }
.tabs .count { font-size: 11.5px; background: #e3e8ec; border-radius: 999px; padding: 1px 7px; margin-left: 3px; }
a.clear { color: var(--blue); font-size: 13px; }
table.queue tr[data-href] { cursor: pointer; }
table.queue tbody tr:hover td { background: #f7f9fa; }
.empty, .absent { background: #fff; border: 1px dashed var(--line); border-radius: 6px; padding: 26px; text-align: center; color: var(--muted); }
.banner { border-radius: 6px; padding: 10px 14px; margin: 0 0 16px; border: 1px solid; }
.banner.ok { background: #e2f2e6; color: #1e6e46; border-color: #bfe0c9; }
.banner.warn { background: #f6ecd9; color: #8a5a10; border-color: #ead3a8; }
.banner.error { background: #f1e4e4; color: #9c3a3a; border-color: #e2c3c3; }
.banner.info { background: #eef1f4; color: #4a5866; border-color: var(--line); }
.banner a { color: inherit; font-weight: 600; }
.banner ul { margin: 6px 0 0 18px; padding: 0; }
form.inline { display: inline; margin-left: 10px; }
.heading { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
a.ext, .nextlink { color: var(--blue); text-decoration: none; font-size: 13px; font-weight: 600; }
.nextlink { float: right; }
.why { background: #fff; border: 1px solid var(--line); border-left: 4px solid var(--blue); border-radius: 6px; padding: 12px 16px; margin-top: 14px; }
.why p { margin: 6px 0 0; line-height: 1.45; }
.sop { background: #fdf6dc; border: 1px solid #efe2a6; border-left: 4px solid #e8d44d; border-radius: 6px; padding: 10px 14px; margin-top: 12px; }
table.changes td.lbl { width: 170px; padding-top: 10px; }
table.changes td:nth-child(2) { width: 38%; }
table.changes tr.group td { background: #f7f9fa; font-weight: 600; font-size: 12.5px; }
s { color: #9c3a3a; }
.after { color: #1e6e46; font-weight: 600; }
.emptyval { color: var(--muted); font-style: italic; }
td, .wrapcell { word-break: break-word; }
.nowrap { white-space: nowrap; }
.compare { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
@media (max-width: 760px) { .compare { grid-template-columns: 1fr; } }
.compare .detailgrid { grid-template-columns: 1fr 1fr; padding: 10px 16px; }
.side { font-weight: 600; margin-bottom: 6px; }
.side a { color: var(--blue); font-weight: 400; font-size: 13px; }
.field.differs { background: #fdf3df; margin: 0 -8px; padding: 8px; border-radius: 4px; }
.val.alert { color: #9c3a3a; font-weight: 700; }
.signals { margin-top: 12px; }
.chip { display: inline-block; background: #fff; border: 1px solid var(--line); border-radius: 999px; padding: 1px 9px; font-size: 12px; margin: 2px 4px 2px 0; }
.actions { background: #fff; border: 1px solid var(--line); border-radius: 6px; padding: 14px 16px; margin-top: 22px; }
.actions input[type=text] { width: 100%; margin: 6px 0 12px; }
.buttons { display: flex; gap: 10px; align-items: center; margin-top: 8px; }
button.secondary, a.button.secondary { background: #fff; color: var(--ink); border: 1px solid #b9c4cd; }
button.danger { background: #a32d2d; }
.keys { font-size: 12px; margin: 10px 0 0; }
kbd { border: 1px solid #b9c4cd; border-bottom-width: 2px; border-radius: 3px; padding: 0 5px; font-size: 11px; background: #f7f9fa; font-family: inherit; }
.decided { background: #fff; border: 1px solid var(--line); border-radius: 6px; padding: 10px 14px; margin-top: 22px; color: var(--muted); }
dialog { border: 1px solid var(--line); border-radius: 8px; padding: 20px 22px; max-width: 620px; width: 92vw; box-shadow: 0 10px 40px rgba(0,0,0,0.25); }
dialog::backdrop { background: rgba(20,32,44,0.45); }
.confirmbox { background: #fff; border: 1px solid var(--line); border-radius: 8px; padding: 20px 22px; max-width: 620px; }
.confirm-title { color: #791f1f; }
table.compact td { padding: 5px 8px; }
table.compact td.lbl { width: 150px; }
table.compact td:nth-child(2) { width: auto; }
.creates { color: #791f1f; font-weight: 600; }
code { background: #e3e8ec; border-radius: 3px; padding: 1px 5px; }
```

**Create `bellhaven/web/static/review.js`**:

```javascript
// Keyboard shortcuts, clickable queue rows, and the live confirmation dialog.
// Every page works without this file: on the live sandbox the server shows its own confirmation page instead.
(function () {
  "use strict";
  var form = document.getElementById("decide");
  var dialog = form && form.dataset.confirm ? document.getElementById(form.dataset.confirm) : null;
  var sent = false;

  if (form) {
    form.addEventListener("submit", function (event) {
      var approving = !event.submitter || event.submitter.id === "approve";
      if (approving && dialog && form.elements.confirmed.value !== "yes") {
        event.preventDefault();  // live: ask first; the dialog's button submits again with confirmed=yes
        dialog.showModal();
        return;
      }
      if (sent) {
        event.preventDefault();  // one decision per page load
        return;
      }
      sent = true;
      setTimeout(function () {
        form.querySelectorAll("button").forEach(function (b) { b.disabled = true; });
      }, 0);
    });
  }

  if (dialog) {
    dialog.querySelector("[data-close]").addEventListener("click", function () { dialog.close(); });
    dialog.querySelector("[data-confirm-yes]").addEventListener("click", function () {
      form.elements.confirmed.value = "yes";
      dialog.close();
      form.requestSubmit(document.getElementById("approve"));
    });
  }

  document.querySelectorAll("tr[data-href]").forEach(function (row) {
    row.addEventListener("click", function (event) {
      if (!event.target.closest("a, button, input, select")) { window.location.href = row.dataset.href; }
    });
  });

  document.addEventListener("keydown", function (event) {
    if (event.ctrlKey || event.metaKey || event.altKey || event.defaultPrevented) { return; }
    var target = event.target;
    if (target.closest && target.closest("input, textarea, select, [contenteditable]")) { return; }
    if (dialog && dialog.open) { return; }
    var key = event.key.toLowerCase();
    var next = document.getElementById("next");
    if (key === "a" && form) {
      event.preventDefault();
      form.requestSubmit(document.getElementById("approve"));
    } else if (key === "r" && form) {
      event.preventDefault();
      form.requestSubmit(document.getElementById("reject"));
    } else if (key === "n" && next) {
      window.location.href = next.href;
    } else if (key === "q") {
      window.location.href = "/queue";
    }
  });
})();
```

- [ ] **Step 7: Run the tests**

Run: `python -m pytest tests/test_web.py -q`
Expected: **15 passed**. Then `python -m pytest -q`: **192 passed**.

- [ ] **Step 8: Commit**

```powershell
git add requirements.txt bellhaven/web/app.py bellhaven/web/__main__.py bellhaven/web/templates bellhaven/web/static tests/test_web.py
git commit -m "feat: Meridian-style review app (FastAPI)" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Switch over from Streamlit

The new app does everything the old one did, so remove the old one and point the docs at the new one.

**Files:**
- Delete: `review_app.py`
- Modify: `requirements.txt`, `README.md`, `docs/superpowers/plans/2026-11-24-bellhaven-crm-pipeline.md`

- [ ] **Step 1: Remove the Streamlit app and its dependency**

**Delete `review_app.py`**:

```powershell
git rm review_app.py
```

**Replace in `requirements.txt`**, this exact text (it appears once):

```text
rapidfuzz>=3.6
streamlit>=1.38
jinja2>=3.1
```

with:

```text
rapidfuzz>=3.6
jinja2>=3.1
```

- [ ] **Step 2: Update the README**

**Replace in `README.md`**, this exact text (it appears once):

```markdown
python -m bellhaven.pipeline      # daily job: scrape, read CRM, store proposals (never writes to the CRM)
streamlit run review_app.py       # review queue: approve writes to the CRM, reject records the decision
python -m pytest -q               # tests
```

with:

```markdown
python -m bellhaven.pipeline      # daily job: scrape, read CRM, store proposals (never writes to the CRM)
python -m bellhaven.web           # review app at http://localhost:8000: approve writes to the CRM, reject records it
python -m pytest -q               # tests
```

**Replace in `README.md`**, this exact text (it appears once):

```markdown
The app shows a green banner for the mock and a red one for the live sandbox.
```

with:

```markdown
The app's top bar shows a green **Mock CRM** badge for the mock and a red **LIVE SANDBOX** badge for the graded copy.
```

**Replace in `README.md`**, this exact text (it appears once):

```markdown
## Testing without touching the graded CRM
```

with:

```markdown
## Review app

`python -m bellhaven.web` serves the app at http://localhost:8000, on this machine only. It looks like the Meridian CRM:

- **Review queue:** pending proposals, with the ones that add an account (ownership changes and new accounts) at the top. Filter by kind, or search by name or city.
- **Proposal page:** why it was proposed, the billing SOP check, the exact fields that change (now → after), the website and the CRM side by side with differences highlighted, and the match signals. Approve or reject with an optional comment; the app then opens the next pending proposal. Shortcuts: `A` approve, `R` reject, `N` next pending, `Q` queue.
- **Change history** (every field written through the API) and **Pipeline runs**.

On the live sandbox, every approval asks "Write this to the live sandbox?" and shows the exact change first, and "View in Meridian ↗" links open the CRM browser. Those links contain your token, so keep the address bar out of shared screen recordings.

## Testing without touching the graded CRM
```

- [ ] **Step 3: Update the live-phase steps in the main plan**

**Replace in `docs/superpowers/plans/2026-11-24-bellhaven-crm-pipeline.md`**, this exact text (it appears once):

```markdown
2. If an ownership change (CHOW) reports an error, press Retry on it before running the pipeline again.
```

with:

```markdown
2. If an ownership change (CHOW) reports an error, press Retry on it before running the pipeline again.

**Review app replaced (2026-11-24):** the Streamlit `review_app.py` from Task 12 was replaced by a Meridian-style FastAPI app: run `python -m bellhaven.web` and open http://localhost:8000. See `docs/superpowers/plans/2026-11-24-review-app-redesign.md`. Where Tasks 14 and 16 mention the app, they mean the new one.
```

**Replace in `docs/superpowers/plans/2026-11-24-bellhaven-crm-pipeline.md`**, this exact text (it appears once):

```markdown
Run `streamlit run review_app.py`, check the banner is green,
```

with:

```markdown
Run `python -m bellhaven.web`, open http://localhost:8000, check the top bar's badge says **Mock CRM**,
```

**Replace in `docs/superpowers/plans/2026-11-24-bellhaven-crm-pipeline.md`**, this exact text (it appears once):

```markdown
Run `streamlit run review_app.py` and check the banner is **red (LIVE SANDBOX)**. Approve only **Update Bellhaven of Portsmouth** (zip 45626 → 45662). Check that the success message shows `'mismatches': {}`, and that the CRM browser shows the new zip.
```

with:

```markdown
Run `python -m bellhaven.web`, open http://localhost:8000, and check the badge says **LIVE SANDBOX**. Approve only **Update Bellhaven of Portsmouth** (zip 45626 → 45662). The app asks "Write this to the live sandbox?" and shows the exact change: check it, then press **Write to live CRM**. Check that the green banner says "The CRM stored exactly what was sent", and that "View in Meridian ↗" shows the new zip.
```

**Replace in `docs/superpowers/plans/2026-11-24-bellhaven-crm-pipeline.md`**, this exact text (it appears once):

```markdown
A CHOW's result has two mismatch lists, `{'new_account': {}, 'link': {}}`; both should be empty.
```

with:

```markdown
For a CHOW, the banner lists any field the CRM stored differently on the new account or on the old account's link; there should be none.
```

**Replace in `docs/superpowers/plans/2026-11-24-bellhaven-crm-pipeline.md`**, this exact text (it appears once):

```markdown
and approve it. Show the success message.
```

with:

```markdown
and approve it (confirm the dialog). Show the green "Written" banner.
```

**Replace in `docs/superpowers/plans/2026-11-24-bellhaven-crm-pipeline.md`**, this exact text (it appears once):

```markdown
Check the "failed" filter.
```

with:

```markdown
Check the **Failed** tab.
```

- [ ] **Step 4: Check nothing still refers to the old app**

Run: `git grep -n -i -E "streamlit|review_app" -- . ":!docs"`
Expected: no output. (The plans under `docs/` keep their history; the main plan's Task 12 still describes the Streamlit app it originally built, and the note added in Step 3 points to this plan.)

Run: `python -m pytest -q`
Expected: **192 passed**.

- [ ] **Step 5: Commit**

```powershell
git add requirements.txt README.md docs/superpowers/plans/2026-11-24-bellhaven-crm-pipeline.md
git commit -m "refactor: replace the Streamlit review app with bellhaven.web" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Check it in a browser against the mock

The tests cover the server. This task checks what a reviewer sees. Use the local mock only.

- [ ] **Step 1: Start a fresh mock** (restarting it resets its data). In terminal 1:

```powershell
$env:BH_MOCK_SEED = "data\snapshot\crm.json"; uvicorn mock_crm.server:build --factory --port 8001
```

- [ ] **Step 2: Fill a test database and start the app.** In terminal 2 (delete `data\test.db` first if you want an empty queue):

```powershell
$env:BH_API_BASE = "http://localhost:8001/api/v1"; $env:BH_DB_PATH = "data\test.db"; python -m bellhaven.pipeline; python -m bellhaven.web
```

Expected: the pipeline prints `"proposals": 29`, then uvicorn says `Uvicorn running on http://127.0.0.1:8000`.

- [ ] **Step 3: Click through http://localhost:8000**
  - The top bar shows **Mock CRM** in green, and there's no "Open Meridian" link.
  - The queue shows the last run's counts and **Pending 29**. Ownership changes come first, then new accounts, duplicates, not on site and updates. The kind filter and the search both narrow the list. Clicking anywhere on a row opens it.
  - **Bellhaven of Tiffin** (Ownership): the Why box, the amber Billing SOP box ($84,000 / $12,400, keep the old account), the Now → After rows with a "New account" group and an "Old account" link row, the website and CRM grids with the parent highlighted and AR in red, and the match chips.
  - Approve it with a comment. The next pending proposal opens with a green **Written** banner saying "The CRM stored exactly what was sent", and Pending drops to 28.
  - On that page press `R`. It's rejected, and the next page shows a grey **Rejected** banner. Press `N`, then `Q`, and check that both move as expected.
  - **Change history** lists the CHOW's fields, newest first, with your comment. **Pipeline runs** shows the run as OK.
  - The browser console shows no errors.

- [ ] **Step 4: Check the live confirmation, still against the mock.** Stop the app (Ctrl+C) and start it in live mode, but with the mock's settings, so writes still go to the mock. The fake Meridian address stops the links from going anywhere real.

```powershell
python -c "from bellhaven import config, store; from bellhaven.crm import CrmClient; from bellhaven.web.app import create_app; import uvicorn; uvicorn.run(create_app(store.connect(config.DB_PATH, crm_base=config.API_BASE), CrmClient(), live=True, meridian_base='https://meridian.invalid/crm/fake'), host='127.0.0.1', port=8000)"
```

  - The badge says **LIVE SANDBOX** in red, and Approve reads "Approve and write to live CRM".
  - Open **Bellhaven of Portsmouth** and press Approve. A dialog asks "Write this to the live sandbox?", with "Zip 45626 → 45662" and the note. **Cancel** writes nothing (the proposal stays Pending). **Write to live CRM** writes it and shows the Written banner with a "View in Meridian ↗" link to `https://meridian.invalid/crm/fake/accounts/<id>`.
  - Turn JavaScript off (DevTools → Ctrl+Shift+P → "Disable JavaScript"), open another pending proposal and press Approve. The server's confirmation page appears instead of the dialog. Cancel goes back without writing. Turn JavaScript back on.

- [ ] **Step 5: Stop both servers** (Ctrl+C in each terminal). Nothing to commit. `data\test.db` is ignored by git.

---

## Spec coverage

| Spec section | Where |
|---|---|
| Look: colours, pills, badge | Task 5 (`meridian.css`, `base.html`) |
| Review queue: header, tabs, filters, columns, order, summaries, empty state | Task 3 (`summary`, `queue_order`, `matches`), Task 5 (`queue.html`, `/queue`) |
| Proposal detail: breadcrumb, position, heading, Why, SOP, Now → After, side by side, signals, actions, failed/applying/decided | Task 3 (`change_rows`, `side_by_side`, `signals`, `sop`, `position`), Task 5 (`proposal.html`) |
| Live confirmation (dialog, no-JS page, enforced on the server) | Task 4 (`needs_confirmation`), Task 5 (`confirm.html`, `_confirm_body.html`, `review.js`), Task 7 Step 4 |
| After a decision: redirect and banners | Task 3 (`next_pending`, `banner`), Task 5 (`after_decision`, `base.html`) |
| Change history, pipeline runs | Task 1 (`fail_run`, `latest_run`, `list_runs`), Task 5 (`history.html`, `runs.html`) |
| Architecture: `review.py`, `present.py`, `app.py`, `__main__.py` | Tasks 3–5 |
| Safety: POST only, CSRF, origin, 127.0.0.1, autoescape, token only on live | Task 5 (`check_post`, tests) |
| Error handling: never raises, 404, wrong database at startup | Task 4, Task 5 (`not_found`, `__main__.py`) |
| Testing | Tasks 1–5 (192 tests), Task 7 |
| Switching over | Task 6 |
