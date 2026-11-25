# Bellhaven CRM pipeline: runbook

The pipeline scrapes every Bellhaven community from the website, matches each one to the CRM, proposes fixes, and writes only reviewer-approved fixes back through the CRM API. This runbook holds what isn't in the code: the background from exploring the site and API, the decisions, how the pieces fit, and the steps still to do. **The code is the reference for how anything works.**

**Status (2026-11-24):** built and tested on branch `bellhaven-pipeline` (207 tests): the pipeline, the mock CRM, the audit and the Meridian-style review app. Nothing has been written to the live sandbox yet. Still to do: the live run, the review and demo, the writeup, and the final check (Steps 1–4 below).

## Rules for the live phase

1. Nothing writes to the live sandbox except your own approvals in the review app. Never send a test POST: every POST creates a real, graded account, and there is no delete.
2. Don't run the pipeline while you're approving.
3. If an ownership change (CHOW) reports an error, press Retry on it before running the pipeline again.
4. The CRM browser's address contains your API token, and anyone with the token can change your graded sandbox through the API. Keep that tab's address bar out of the shared recording.
5. Never commit `.env`, `data/` or `brief/`: they hold the token or the CRM's data.

## Background (from exploring the site and API on 2026-11-24)

The brief is in `brief/Bellhaven_CRM_Exercise.docx` (transcribed from the three screenshots beside it).

**Website** (`https://analyst-assessment-production.up.railway.app`)
- `/communities` lists **34** communities on 3 pages (`?page=2`, `?page=3`, with a "Next →" link in `.pager`). The first page says "34 communities listed".
- The homepage says "35 communities". The 35th, **Bellhaven Meadows of Findlay**, is linked only from a homepage banner, so the scraper also collects community links from `/` and `/about`.
- Detail pages (`/communities/<slug>`) have an `h1` name and a `dl.detail` with Address (street `<br>` "City, ST 12345"), Care Offerings (`.badge` spans), Administrator and Phone.
- `/about`: Bellhaven acquired **Harborview Care Group in 2025** and some **Cedar Trail** communities in 2026. That explains most wrong parents.

**CRM API** (`/api/v1`, header `Authorization: Bearer <token>`)
- `GET /accounts` takes the filters `q`, `city`, `state`, `zip`, `street`, `parent_id`, `page` and `page_size` (max 50), and returns `{"data": [...], "page", "page_size", "total"}`. There are 121 accounts.
- `GET /accounts/{id}`, `POST /accounts`, `PATCH /accounts/{id}`. There is no delete.
- `GET /contacts?account_id=` returns 67 contacts, each with `account_id`, `name`, `title` and `is_active`. Most facilities have their **Administrator** as a contact, which is strong matching evidence.
- Account fields: `account_id, name, parent_id, parent_name, billing_street, billing_city, billing_state, billing_zip, care_type, status, phone, lifetime_revenue, outstanding_ar, chow_current_account, duplicate_of_account, note, created_by_candidate, updated_at`.
- The OpenAPI spec has no request-body schema, and `POST /accounts` declares no validation error.
- **HTTPS on this laptop:** `requests` fails with `CERTIFICATE_VERIFY_FAILED` (corporate TLS inspection), so `config.py` calls `truststore.inject_into_ssl()` to use the Windows certificate store. Don't turn off certificate verification instead.
- **CRM browser (read only):** `https://analyst-assessment-production.up.railway.app/crm/<your token>/accounts`, and each account at `.../accounts/<account_id>`. It shows the same fields as the API (parent, status, care type, address, revenue and AR, CHOW Current Account, Duplicate Of, Note, Last Updated), and a parent's page lists its child accounts.

**Traps in the data**
- Matching on name alone gives wrong answers. The CRM has an "Amberly Manor" in Colorado Springs, CO, which is not the one in Hudson, OH. "Union Square Senior Living" is in the same town as "Bellhaven at Union Square" but has a different address, parent and administrator. "Bellhaven of New Carlisle, OH" is not "Bellhaven of Carlisle, PA".
- Phone numbers differ between the website and the CRM for about half of the true matches, so phone is evidence only.
- Addresses are written in different ways: `W`/`West`, `NW`/`Northwest`, `Pk`/`Pike`, `Rd W`/`Road West`. One account's street is a PO Box. One zip has two digits swapped (45626 vs 45662).
- Care types use different names: "Short-Term Rehabilitation & Nursing" on the website is "Skilled Nursing" in the CRM, and "Memory Support" is "Memory Care".
- Billing rule (SOP): Akron, Defiance and Meadville have revenue and unpaid AR but are already under Bellhaven, so they don't move. Lima and Findlay have revenue but zero AR, so they move directly. Tiffin and Marietta (Cedar Trail) have both, so they need a change of ownership (CHOW).
- About 60 accounts belong to other operators (Juniper Point, Stonebridge, or no parent). They are left alone.

## Decisions

| # | Question | Decision | Alternative |
|---|---|---|---|
| 1 | A Bellhaven account is gone from the website and has no other copy | `status = "Needs Review"` plus a note (`BH_NOT_ON_WEBSITE_STATUS`) | `Inactive` |
| 2 | A Bellhaven account is gone from the website, but another operator's account is at the same address (Sandusky: billing history, and "Millstone Care of Sandusky" at the same address) | Like #1, with `Needs Review` and a note naming the other account. Nothing is linked or merged automatically: the CRM alone can't prove who owns it now. | Link it with `chow_current_account`, or create a new account under Millstone |
| 3 | The phone number differs from the website | Evidence only, never updated | Propose phone updates |
| 4 | The website lists 2 care types; the CRM field holds one | Leave it if the CRM value is one of them, otherwise use the first listed | — |
| 5 | Contacts for new accounts | Not created | Create an Administrator contact |
| 6 | Schedule | cron entry in `deploy/crontab.txt` (a `schtasks` command in the README for Windows) | GitHub Actions workflow |
| 7 | Live writes before the review | None. The first live write is the careful Portsmouth approval in Step 2. | A harmless test write |
| 8 | Confirmation in the review app | Every approval on the live sandbox asks first; the mock never does | Confirm only creates and CHOWs |

## How it's built

```
 DAILY PIPELINE (cron: python -m bellhaven.pipeline). Reads only, never writes to the CRM
 ┌───────────────────────────────────────────────────────────────────────────────────────┐
 │ Bellhaven website ─► scrape.py ──┐                                                    │
 │                                  ├─► match.py ─► classify.py ─► store.py (SQLite)    │
 │ CRM API (GET) ─────► crm.py ─────┘   normalize.py   sop.py        new proposals only  │
 └───────────────────────────────────────────────────────────────────────┬───────────────┘
                                                                         │ pending proposals
 HUMAN REVIEW (python -m bellhaven.web, http://localhost:8000)           ▼
   reviewer ─► web/ review app ── reject ─► decision saved (never proposed again)
                     │ approve (on live: confirmed first)
                     ▼
               web/review.py ─► apply.py ─► CRM API (POST / PATCH) ─► api_log + change_log

 TEST BENCH: mock_crm/ (local CRM API from a read-only snapshot) · audit.py (read-only end-state check)
```

| Piece | Brief requirement | What it solves |
|---|---|---|
| `scrape.py` | "A scraper that pulls every Bellhaven location" | A current list of who Bellhaven owns. It also catches the homepage-only Findlay page, and stops if the directory count doesn't add up rather than flagging good accounts as missing. |
| `crm.py` | API access | The single gateway to the CRM. The pipeline only reads; only `apply.py` writes. |
| `normalize.py` + `match.py` | "Matching logic that links each location to the right CRM account" | Links facilities despite rebrands, address formatting and stale phones, and avoids the name-only traps. |
| `classify.py` | "…and classifies it" | Turns each finding into one explained change: update, chow, duplicate, create or not_on_website. |
| `sop.py` | "One SOP you must follow" | When revenue and AR are both above 0, a change of ownership instead of moving the account. |
| `store.py` | "Re-runs must be safe" | Remembers every proposal and decision, and logs every API change field by field. |
| `web/` (`present.py`, `review.py`, `app.py`, templates) | "A small review app… approve or reject… Nothing writes without approval" | A Meridian-style queue and one page per proposal: why, the SOP check, now → after, the website and the CRM side by side, the match signals. |
| `apply.py` | "Approved changes write back to the CRM through the API" | Writes only approved changes, refuses if the record changed, never creates the same account twice, and logs each change. |
| `pipeline.py` + `deploy/crontab.txt` | "Built to run daily" | Keeps the ownership picture current. |
| `mock_crm/` + `audit.py` | "Your finished submission is a corrected CRM" | Rehearse everything without touching the graded copy, then check the end state. |

**Database** (SQLite, `data/state.db`; one database per CRM): `runs` 1──* `proposals` 1──* `api_log` 1──* `change_log`, plus `meta` (which CRM the database belongs to) and the `change_history` view (every field changed, with the proposal and the reviewer's comment). Foreign keys are enforced. `old_value` comes from a fresh read just before writing, and `new_value` is what the CRM returned, so the log records what actually landed.

**Behaviour worth knowing (and explaining in the writeup)**
- **Re-runs:** a proposal's key is a hash of the decision (notes excluded). Rejected proposals are never proposed again. Pending, failed and superseded ones are refreshed with current data. Undecided ones a run no longer produces are superseded, unless a write was attempted. An applied one comes back only if the CRM changed back, judged against decisions made before the run started. One stuck "applying" reopens only after 10 minutes.
- **Writing:** an atomic claim stops a double click from writing twice. The account is re-read and the write refused if it changed or the rule no longer holds: a CHOW still needs revenue and AR, a resolved account is left alone, and a duplicate's survivor must be live. A lost response is detected as "already applied". Creates happen at most once (our API log first, then a strict name, street and zip search), and only if the latest finished run still proposes them. A CHOW that fails after its create says to retry before the next run.
- **Review app safety:** writes only on POST, with a CSRF token and an Origin check. On live, nothing is written until the reviewer confirms, enforced by the server. It listens on 127.0.0.1 and serves only local host names, and pages can't be framed. Meridian links go through `/meridian/...` on the app, so the token never appears in a page. On live, the `R` shortcut asks before rejecting.
- **Matching:** address-first weighted score (threshold 60, weak 40). A different state scores 0. Assignment is one-to-one.
- **Known limitations:** partially approving a CHOW-with-duplicates group. A CHOW whose link response is lost ends "stale" (the CRM is right, but the proposal is never marked applied). Linking a CHOW to an existing Bellhaven copy instead of creating a new account was deferred. A very long pipeline error is shown in full on the runs page.

## Rehearse on the mock (optional, before going live)

1. Start a fresh mock, and point a second terminal at it, as in the README's "Testing without touching the graded CRM". Restarting the mock resets its data.
2. In that second terminal, run `python -m bellhaven.pipeline` (expected: `"proposals": 29`), then `python -m bellhaven.web`. The app at http://localhost:8000 shows the green **Mock CRM** badge.
3. Practise the demo (see Step 2 below). The mock has no CRM browser: to see a change there, run `python -c "from bellhaven.crm import CrmClient; print(CrmClient().get_account('<id>'))"` in a terminal pointed at the mock. Then stop the app with Ctrl+C and approve everything left with `python -m mock_crm.approve_all`. Expected: `Applied N of N proposals to the mock at http://localhost:8001/api/v1`. If any can't be applied, it lists them and exits non-zero.
4. Run `python -m bellhaven.pipeline` again. Expected: `"new": 0`, and `"proposals"` equals the number you rejected.
5. Run `python -m bellhaven.audit --before data\snapshot\crm.json`. Expected: `{"created": 6, "changed": 25}`, give or take what you rejected, then `OK`. The only acceptable `PROBLEM` lines are `still proposed`, for proposals you rejected on purpose.
6. Clean up: `Remove-Item data\test.db; Remove-Item Env:BH_API_BASE, Env:BH_DB_PATH`, and stop the mock.

## Step 1: Live run and check the proposals (read-only)

- [ ] **Switch back to the live sandbox.** Open a new terminal, or clear the test settings:

```powershell
Remove-Item Env:BH_API_BASE, Env:BH_DB_PATH, Env:BH_MOCK_SEED -ErrorAction SilentlyContinue
```

If a command later says a database "holds decisions for" another CRM, the guard has caught a mix-up. Check your settings rather than deleting the database.

- [ ] **Run the pipeline against the sandbox:** `python -m bellhaven.pipeline`. Expected: about `{"locations": 35, "accounts": 121, "proposals": 29, "new": 29, ...}`.
- [ ] **List the proposals:**

```powershell
python -c "from bellhaven import store; from bellhaven.config import DB_PATH; [print(r['kind'], '|', r['title'], '|', r['confidence']) for r in store.list_proposals(store.connect(DB_PATH), 'pending')]"
```

- [ ] **Compare with what exploration found** (confirmed on 2026-11-24 against the sandbox, read-only). Investigate any difference before reviewing. If the code is wrong, fix it with a new test; don't fix the CRM by hand.

| kind | Expected |
|---|---|
| update (13) | Names: Grove City, Sycamore Ridge, Arbors Dayton, Ashland, Riverbend Manor → Chagrin Falls, Sunny Acres → Willow Creek, Chesterton Senior Commons → Chesterton. Name and parent: Cedar Trail of Zanesville, Kettering Nursing & Rehabilitation (the Kettering survivor: all three copies tie, so the lowest id is kept). Parent only: Lima, Findlay. Ashtabula street (PO Box). Portsmouth zip 45626 → 45662. |
| chow (2) | Bellhaven of Tiffin, Bellhaven of Marietta (both Cedar Trail, revenue and AR > 0) |
| duplicate (7) | Second Bellhaven of Owosso, Harborview Nursing & Rehab of Port Clinton, Harborview Shores of Erie, Cedar Trail of Monroe, Monroe Gardens Care Center, Kettering Care Centre, Kettering Senior Campus |
| create (4) | Bellhaven of Batavia, Bellhaven of Carlisle (PA), Bellhaven at Union Square, Amberly Manor (Hudson OH) |
| not_on_website (3) | Bellhaven Care Center of Alliance, Bellhaven of Coldwater, and Bellhaven of Sandusky (its note names Millstone Care of Sandusky at the same address; nothing is linked, per decision #2) |

- [ ] **Run it a second time:** `python -m bellhaven.pipeline`. Expected: `"new": 0, "already_known": 29`.

## Step 2: Review the queue and record the demo

- [ ] **Make the first live write carefully.** The mock can't confirm the real API's exact write format, so test it on one low-risk change first.
  - Run `python -m bellhaven.web`, open http://localhost:8000, and check the badge says **LIVE SANDBOX**.
  - Approve only **Update Bellhaven of Portsmouth** (zip 45626 → 45662). The app asks "Write this to the live sandbox?" and shows the exact change: check it, then press **Write to live CRM**.
  - Check that the green banner says "The CRM stored exactly what was sent", and that "View in Meridian ↗" shows the new zip. If anything was stored differently, or you get an error, stop and fix `crm.py`/`apply.py` (with a test) before approving anything else.
  - Then make the first create and the first CHOW the same way, one at a time, before going faster. For a CHOW, the banner lists any field the CRM stored differently on the new account or the old account's link; there should be none.
- [ ] **Start a screen recorder** (Loom, or any free recorder), with the app still open.
- [ ] **Record about 2 minutes:**
  - Open the Tiffin CHOW proposal and explain the billing SOP box.
  - Open a rename (for example Riverbend Manor → Bellhaven of Chagrin Falls), approve it and confirm the dialog. Show the green "Written" banner.
  - Reject one proposal you genuinely disagree with, with a comment. If you agree with all of them, the most debatable are the `not_on_website` ones (decisions #1 and #2) and the Kettering duplicate survivor (a tie, broken by lowest account id).
  - Click "View in Meridian ↗" and show the approved change in the CRM browser. Keep its address bar out of the recording (full screen, or crop it): it contains the token.
- [ ] **Work through the rest of the queue.** Read every proposal's evidence before approving. A queue with unapproved proposals scores the same as doing nothing.
- [ ] **Check the Failed tab.** For a stale failure, re-run the pipeline and review again. For an API error, read the error saved on the proposal, fix the code, and approve again. If you reject one by mistake, put it back in the queue with the command below, using the key at the end of its page's address (`/proposals/<key>`):

```powershell
python -c "import sys; from bellhaven import store; from bellhaven.config import DB_PATH; c = store.connect(DB_PATH); n = c.execute('UPDATE proposals SET status=?, decided_at=NULL WHERE key=? AND status=?', ('pending', sys.argv[1], 'rejected')).rowcount; c.commit(); print(n, 'reopened')" <key>
```

- [ ] **Run the pipeline again:** `python -m bellhaven.pipeline`. Expected: `"new": 0`, and the app shows 0 pending.
- [ ] **Share the recording** so anyone with the link can view it, and save the link for the submission form.

## Step 3: Writeup

- [ ] **Write `WRITEUP.md` with these sections**, using your actual results:
  1. **Approach:** the pipeline stages, and why matching is address-first (rebrands and stale phones).
  2. **Matching:** the weights table from `match.py`, the 60/40 thresholds, the state rule, one-to-one assignment, and the three name traps (Amberly Manor CO, Union Square, New Carlisle vs Carlisle).
  3. **Outcomes:** counts per proposal kind and how many you approved or rejected (from `store.counts`).
  4. **Duplicates:** the survivor ranking (billing history → under Bellhaven → administrator → phone → Active → score), and why losers get `duplicate_of_account` plus `Inactive` instead of a merge.
  5. **Billing SOP:** Tiffin and Marietta (CHOW); Lima and Findlay (direct, because AR is 0); Akron, Defiance and Meadville (no move, so the rule doesn't apply); Sandusky (flagged Needs Review with a note naming Millstone Care of Sandusky, not linked: the same address is a clue, not proof of a sale, and its billing history makes a wrong link costly; explain decision #2).
  6. **Accounts not on the website:** why `Needs Review` and not `Inactive` (decision #1).
  7. **Safe re-runs:** proposal keys, decided proposals never re-proposed, refresh and supersede, the freshness check before writing, create-once.
  8. **Audit trail:** the SQLite schema, and how to answer "what changed on this account, when, and why" from the `change_history` view or the app's Change history page. Include counts from your live run.
  9. **Testing without touching the graded CRM:** the strict mock filled from a read-only snapshot, the end-to-end test, the rehearsal, the database guard, the Mock CRM / LIVE SANDBOX badge and the live confirmation, the careful first live write, and the final audit.
  10. **What I'd change for production:** a shared database in place of SQLite, a geocoding/address service, contact sync, alerting when the website layout changes (the directory count check is the start of this), and multi-reviewer sign-in.
- [ ] **Commit it:** `git add WRITEUP.md`, then `git commit -m "docs: writeup"`.

## Step 4: Final check and submit

- [ ] `python -m pytest -q`: all pass.
- [ ] `python -m bellhaven.pipeline`: expect `"new": 0`.
- [ ] `python -m bellhaven.audit --before data\snapshot\crm.json` against the live sandbox (read-only): expect `OK`, or only `still proposed` lines for proposals you rejected on purpose. Investigate any other `PROBLEM` line.
- [ ] `git status`: `.env`, `data/` and `brief/` are not tracked. `git grep -E "bh_[A-Za-z0-9_-]{16,}"` finds nothing (no token in tracked files).
- [ ] Push to the repository you'll submit (private, or as the form asks). Paste the repo link and the demo link into the submission form.
