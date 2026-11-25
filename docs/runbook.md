# CRM Review App: runbook

The pipeline scrapes every Bellhaven community from the website, matches each one to the CRM, proposes fixes, and writes only reviewer-approved fixes back through the CRM API. This runbook holds what isn't in the code: the background from exploring the site and API, the decisions, how the pieces fit, and the steps still to do. **The code is the reference for how anything works.**

**Status (2026-11-25):** built on branch `bellhaven-pipeline`: the pipeline, the audit and the Meridian-style review app. It was tested against a local mock CRM and a suite of 207 automated tests during the build; both were removed afterwards to keep the repo lean, and git history still has them. Step 1 (the live, read-only pipeline run) is done: it found the 29 proposals expected, and a second run proposed nothing new. Since then, care types follow every offering the website lists (decision #4), which adds a care type change for Findlay and Erie, and Administrator contacts are now checked against the website, which adds 9 Administrator contact changes. The website's phone number now also replaces a different one in the CRM (decision #3), which adds a phone change to 10 updates and 5 phone-only updates. Each account now has one review: its Administrator contact change is part of its Update, which leaves 38 proposals. Nothing has been written to the live sandbox yet. Still to do: the review and demo, the writeup, and the final check (Steps 2–4 below).

## Rules for the live phase

1. Nothing writes to the live sandbox except your own approvals in the review app. Never send a test POST: every POST creates a real, graded account, and there is no delete.
2. Don't run the pipeline while you're approving.
3. If an ownership change (CHOW) reports an error, press Retry on it before running the pipeline again.
4. The CRM browser's address contains your API token, and anyone with the token can change your graded sandbox through the API. Keep that tab's address bar out of the shared recording.
5. Never commit `.env` or `data/`: they hold the token and the CRM's data. Keep the exercise brief out of the repo too, because it contains the token.

## Background (from exploring the site and API on 2026-11-24)

**Website** (`https://analyst-assessment-production.up.railway.app`)
- `/communities` lists **34** communities on 3 pages (`?page=2`, `?page=3`, with a "Next →" link in `.pager`). The first page says "34 communities listed".
- The homepage says "35 communities". The 35th, **Bellhaven Meadows of Findlay**, is linked only from a homepage banner, so the scraper also collects community links from `/` and `/about`.
- Detail pages (`/communities/<slug>`) have an `h1` name and a `dl.detail` with Address (street `<br>` "City, ST 12345"), Care Offerings (`.badge` spans), Administrator and Phone.
- `/about`: Bellhaven acquired **Harborview Care Group in 2025** and some **Cedar Trail** communities in 2026. That explains most wrong parents.

**CRM API** (`/api/v1`, header `Authorization: Bearer <token>`)
- `GET /accounts` takes the filters `q`, `city`, `state`, `zip`, `street`, `parent_id`, `page` and `page_size` (max 50), and returns `{"data": [...], "page", "page_size", "total"}`. There are 121 accounts.
- `GET /accounts/{id}`, `POST /accounts`, `PATCH /accounts/{id}`. There is no delete.
- `GET /contacts?account_id=` returns 67 contacts, each with `contact_id`, `account_id`, `name`, `title`, `phone`, `email` and `is_active`. A contact's phone is their own line, never the community's main number. Most facilities have their **Administrator** as a contact, which is strong matching evidence.
- Account fields: `account_id, name, parent_id, parent_name, billing_street, billing_city, billing_state, billing_zip, care_type, status, phone, lifetime_revenue, outstanding_ar, chow_current_account, duplicate_of_account, note, created_by_candidate, updated_at`.
- The OpenAPI spec has no request-body schema, and `POST /accounts` declares no validation error.
- **HTTPS on this laptop:** `requests` fails with `CERTIFICATE_VERIFY_FAILED` (corporate TLS inspection), so `config.py` calls `truststore.inject_into_ssl()` to use the Windows certificate store. Don't turn off certificate verification instead.
- **CRM browser (read only):** `https://analyst-assessment-production.up.railway.app/crm/<your token>/accounts`, and each account at `.../accounts/<account_id>`. It shows the same fields as the API (parent, status, care type, address, revenue and AR, CHOW Current Account, Duplicate Of, Note, Last Updated), and a parent's page lists its child accounts.

**Traps in the data**
- Matching on name alone gives wrong answers. The CRM has an "Amberly Manor" in Colorado Springs, CO, which is not the one in Hudson, OH. "Union Square Senior Living" is in the same town as "Bellhaven at Union Square" but has a different address, parent and administrator. "Bellhaven of New Carlisle, OH" is not "Bellhaven of Carlisle, PA".
- Phone numbers differ between the website and the CRM for about half of the true matches (17 of 31), so a phone never makes a match on its own: it only supports one. The website's number is the current one, so it replaces the CRM's (decision #3).
- Addresses are written in different ways: `W`/`West`, `NW`/`Northwest`, `Pk`/`Pike`, `Rd W`/`Road West`. One account's street is a PO Box. One zip has two digits swapped (45626 vs 45662).
- Care types use different names: "Short-Term Rehabilitation & Nursing" on the website is "Skilled Nursing" in the CRM, and "Memory Support" is "Memory Care".
- Billing rule (SOP): Akron, Defiance and Meadville have revenue and unpaid AR but are already under Bellhaven, so they don't move. Lima and Findlay have revenue but zero AR, so they move directly. Tiffin and Marietta (Cedar Trail) have both, so they need a change of ownership (CHOW).
- About 60 accounts belong to other operators (Juniper Point, Stonebridge, or no parent). They are left alone.

## Decisions

| # | Question | Decision | Alternative |
|---|---|---|---|
| 1 | A Bellhaven account is gone from the website and has no other copy | `status = "Needs Review"` plus a note (`BH_NOT_ON_WEBSITE_STATUS`) | `Inactive` |
| 2 | A Bellhaven account is gone from the website, but another operator's account is at the same address (Sandusky: billing history, and "Millstone Care of Sandusky" at the same address) | Like #1, with `Needs Review` and a note naming the other account. Nothing is linked or merged automatically: the CRM alone can't prove who owns it now. | Link it with `chow_current_account`, or create a new account under Millstone |
| 3 | The phone number differs from the website | The account's `phone` takes the website's number, as one more field in the account's Update. It stays supporting evidence only for matching. Contacts keep their own phone: the website lists only the community's main number. | Evidence only, never updated |
| 4 | The website lists 2 care types; the CRM field holds one | The CRM `care_type` lists every offering, in the CRM's names and the website's order, separated by semicolons ("Assisted Living; Memory Care"), the way a Salesforce multi-select stores them. Order doesn't matter when comparing. | Keep one value (the first listed) |
| 5 | Contacts | The Administrator contact follows the website: if the website's administrator isn't an active contact, add them (name and title only: the website lists no direct phone or email) and make any other active Administrator contact inactive, not renamed, so the CRM keeps who held the role. It is part of the account's Update, so each account is reviewed once; on approval, the account's fields are written first, then the contacts. Not done for new accounts yet, or for a CHOW's old account, which the SOP keeps exactly as it is. | A separate Administrator proposal; rename the old contact; also add contacts for new accounts |
| 6 | Schedule | cron entry in `deploy/crontab.txt` (a `schtasks` command in the README for Windows) | GitHub Actions workflow |
| 7 | Live writes before the review | None. The first live write is the careful Portsmouth approval in Step 2. | A harmless test write |
| 8 | Confirmation in the review app | Every approval asks first | Confirm only creates and CHOWs |

## How it's built

```
 DAILY PIPELINE (cron: python -m crm_review_app.pipeline). Reads only, never writes to the CRM
 ┌───────────────────────────────────────────────────────────────────────────────────────┐
 │ Bellhaven website ─► scrape.py ──┐                                                    │
 │                                  ├─► match.py ─► classify.py ─► store.py (SQLite)    │
 │ CRM API (GET) ─────► crm.py ─────┘   normalize.py   sop.py       proposals, decisions │
 └───────────────────────────────────────────────────────────────────────┬───────────────┘
                                                                         │ pending proposals
 HUMAN REVIEW (python -m crm_review_app.web, http://localhost:8000)      ▼
   reviewer ─► web/ review app ── reject ─► decision saved (never proposed again)
                     │ approve (confirmed first)
                     ▼
               web/review.py ─► apply.py ─► CRM API (POST / PATCH) ─► api_log + change_log

 END-STATE CHECK: audit.py compares the CRM with data/snapshot/crm.json, a read-only copy taken before any write
```

| Piece | Brief requirement | What it solves |
|---|---|---|
| `scrape.py` | "A scraper that pulls every Bellhaven location" | A current list of who Bellhaven owns. It also catches the homepage-only Findlay page, and stops if the directory count doesn't add up rather than flagging good accounts as missing. |
| `crm.py` | API access | The single gateway to the CRM: accounts and contacts. The pipeline only reads; only `apply.py` writes. |
| `normalize.py` + `match.py` | "Matching logic that links each location to the right CRM account" | Links facilities despite rebrands, address formatting and stale phones, and avoids the name-only traps. |
| `classify.py` | "…and classifies it" | Turns each finding into one explained change: update (account fields and the Administrator contact), chow, duplicate, create or not_on_website. |
| `sop.py` | "One SOP you must follow" | When revenue and AR are both above 0, a change of ownership instead of moving the account. |
| `store.py` | "Re-runs must be safe" | Remembers every proposal and decision, and logs every API change field by field. |
| `web/` (`present.py`, `review.py`, `app.py`, templates) | "A small review app… approve or reject… Nothing writes without approval" | A Meridian-style queue and one page per proposal: the reason, the SOP check, now → after, the website and the CRM side by side, the match signals. |
| `apply.py` | "Approved changes write back to the CRM through the API" | Writes only approved changes, refuses if the record changed, never creates the same account twice, and logs each change. |
| `pipeline.py` + `deploy/crontab.txt` | "Built to run daily" | Keeps the ownership picture current. |
| `audit.py` | "Your finished submission is a corrected CRM" | Checks the end state against the copy of the sandbox taken before any write. |

**Database** (SQLite, `data/state.db`): `runs` 1──* `proposals` 1──* `api_log` 1──* `change_log`, plus the `change_history` view (every field changed, with the proposal and the reviewer's comment). Foreign keys are enforced. `old_value` comes from a fresh read just before writing, and `new_value` is what the CRM returned, so the log records what actually landed.

**Behaviour worth knowing (and explaining in the writeup)**
- **Re-runs:** a proposal's key is a hash of the decision (notes excluded). Rejected proposals are never proposed again. Pending, failed and superseded ones are refreshed with current data. Undecided ones a run no longer produces are superseded, unless a write was attempted. An applied one comes back only if the CRM changed back, judged against decisions made before the run started. One stuck "applying" reopens only after 10 minutes.
- **Writing:** an atomic claim stops a double click from writing twice. The account is re-read and the write refused if it changed or the rule no longer holds: a CHOW still needs revenue and AR, a resolved account is left alone, and a duplicate's survivor must be live. A lost response is detected as "already applied". Creates happen at most once (our API log first, then a strict name, street and zip search), and only if the latest finished run still proposes them. A CHOW that fails after its create says to retry before the next run. An update with an Administrator contact change checks the contacts (and that the account will be Active) before writing anything, writes the account's fields, then adds the new contact at most once (our API log first, then a same-name search) before making the old one inactive. A retry skips what already landed: the account change isn't written or logged twice, and the contact is reused.
- **Review app safety:** writes only on POST, with a CSRF token and an Origin check. Nothing is written until the reviewer confirms, enforced by the server. It listens on 127.0.0.1 and serves only local host names, and pages can't be framed. Meridian links go through `/meridian/...` on the app, so the token never appears in a page.
- **Matching:** named rules, not points. A pair is the same facility only if it meets one: **same address** (street, plus the zip or city), **near address** (a near-same street in the same city with the same house number, plus the administrator or phone), or **same people** (the administrator and phone, plus the zip or city). The street alone, or the administrator or phone in the same town, is only a possible match: shown on a create, never acted on. A name alone never makes a match, and a different state rules one out. Matches are ranked by rule, then by how much else agrees (administrator, phone, name, zip, city), and each account goes to one community at most. On the live data, 37 matches met "same address", Ashtabula (PO Box) met "same people", and nothing was a possible match.
- **Known limitations:** partially approving a CHOW-with-duplicates group. A CHOW whose link response is lost ends "stale" (the CRM is right, but the proposal is never marked applied). Linking a CHOW to an existing Bellhaven copy instead of creating a new account was deferred. A very long pipeline error is shown in full on the runs page.

## Step 1: Live run and check the proposals (read-only)

- [x] **Open a new terminal** in the repo folder, with the virtual environment active. If you set `BH_DB_PATH` while practising, clear it (`Remove-Item Env:BH_DB_PATH`) so the live run uses `data\state.db`.

- [x] **Run the pipeline against the sandbox:** `python -m crm_review_app.pipeline`. Expected: about `{"locations": 35, "accounts": 121, "proposals": 38, ...}` (44 before Administrator changes joined the updates, 39 before the phone change, 29 before the care type and Administrator changes).
- [x] **List the proposals:**

```powershell
python -c "from crm_review_app import store; from crm_review_app.config import DB_PATH; [print(r['kind'], '|', r['title'], '|', r['confidence']) for r in store.list_proposals(store.connect(DB_PATH), 'pending')]"
```

- [x] **Compare with what exploration found** (confirmed on 2026-11-24 against the sandbox, read-only). Investigate any difference before reviewing. If the code is wrong, fix the code; don't fix the CRM by hand.

| kind | Expected |
|---|---|
| update (22) | Names: Grove City, Sycamore Ridge, Arbors Dayton, Ashland, Riverbend Manor → Chagrin Falls, Sunny Acres → Willow Creek, Chesterton Senior Commons → Chesterton. Name and parent: Cedar Trail of Zanesville, Kettering Nursing & Rehabilitation (the Kettering survivor: all three copies tie, so the lowest id is kept). Parent only: Lima. Parent and care type (Assisted Living → Assisted Living; Memory Care): Findlay. Care type only: Bellhaven Shores of Erie. Ashtabula street (PO Box). Portsmouth zip 45626 → 45662. Phone (the website's number replaces the CRM's): also on Sycamore Ridge, Arbors Dayton, Ashland, Chagrin Falls, Willow Creek, Zanesville, Kettering, Lima, Findlay and Erie; phone only: Goshen, Defiance, Marion, Port Clinton, Monroe. Administrator contact (9, in the same update): replace (add the website's administrator, make the old contact inactive) on Wooster (Ray Amato → Lorna Sorensen), Saline (Carla Castellano → Dale Green), Sycamore Ridge (Sofia Castellano → Tom Bloom) and Lima (Nadia Sandoval → Tricia Fitch); add (no Administrator contact) on Meadville (Chris Pike), Kettering (Priya Ashby), Grove City (Marie Vogel), Willow Creek (Walt Mabry) and Monroe (Phil Mabry). Wooster, Saline and Meadville change only the contact. |
| chow (2) | Bellhaven of Tiffin, Bellhaven of Marietta (both Cedar Trail, revenue and AR > 0) |
| duplicate (7) | Bellhaven of Owosso (a second copy with the same name), Harborview Nursing & Rehab of Port Clinton, Harborview Shores of Erie, Cedar Trail of Monroe, Monroe Gardens Care Center, Kettering Care Centre, Kettering Senior Campus |
| create (4) | Bellhaven of Batavia, Bellhaven of Carlisle (PA), Bellhaven at Union Square, Amberly Manor (Hudson OH) |
| not_on_website (3) | Bellhaven Care Center of Alliance, Bellhaven of Coldwater, and Bellhaven of Sandusky (its note names Millstone Care of Sandusky at the same address; nothing is linked, per decision #2) |

- [x] **Run it a second time:** `python -m crm_review_app.pipeline`. Expected: `"new": 0, "already_known": 38`.

## Step 2: Review the queue and record the demo

- [ ] **Make the first live write carefully.** Nothing has written to the real API yet, so confirm its exact write format on one low-risk change first.
  - Run `python -m crm_review_app.web`, open http://localhost:8000, and check the badge says **LIVE SANDBOX**.
  - Approve only **Update Bellhaven of Portsmouth** (zip 45626 → 45662). The app asks "Write this to the live sandbox?" and shows the exact change: check it, then press **Write to live CRM**.
  - Check that the green banner says "The CRM stored exactly what was sent", and that "View in Meridian ↗" shows the new zip. If anything was stored differently, or you get an error, stop and fix `crm.py`/`apply.py` before approving anything else.
  - Then make the first create and the first CHOW the same way, one at a time, before going faster. Use Batavia and Marietta, and leave Tiffin and Lima pending: the demo shows them. For a CHOW, the banner lists any field the CRM stored differently on the new account or the old account's link; there should be none.
  - Do the same for the first update with an Administrator contact change, best a contact-only one (Wooster, Saline or Meadville): no contact has been written to this CRM before. A replacement adds the new contact first, then makes the old one inactive; if a step fails, press Retry (what landed is not written again, and the new contact is reused). Check the account's page in Meridian lists the new administrator.
  - Do the same for the first phone-only update (for example Goshen): no phone has been written to this CRM before. If the write is refused, stop before approving any other update with a phone change. Numbers are compared by their digits, so a CRM that only reformats them is fine.
  - Do the same for the first two-type care value (Erie or Findlay: "Assisted Living; Memory Care"): no such value has been written to this CRM before. If the banner shows the CRM stored it differently, or the write is refused, stop and switch decision #4 back to a single value before approving the other one.
- [ ] **Start a screen recorder** (Loom, or any free recorder), with the app still open.
- [ ] **Record about 2½ minutes, following [demo-script.md](demo-script.md):** the queue, the Tiffin change of ownership and its billing SOP box, approving Lima, rejecting one proposal, the change in Meridian, and the change history. Keep the Meridian tab's address bar out of the recording (full screen, or crop it): it contains the token.
- [ ] **Work through the rest of the queue.** Read every proposal's evidence before approving. A queue with unapproved proposals scores the same as doing nothing.
- [ ] **Check the Failed tab.** For a stale failure, re-run the pipeline and review again. For an API error, read the error saved on the proposal, fix the code, and approve again. If you reject one by mistake, put it back in the queue with the command below, using the key at the end of its page's address (`/proposals/<key>`):

```powershell
python -c "import sys; from crm_review_app import store; from crm_review_app.config import DB_PATH; c = store.connect(DB_PATH); n = c.execute('UPDATE proposals SET status=?, decided_at=NULL WHERE key=? AND status=?', ('pending', sys.argv[1], 'rejected')).rowcount; c.commit(); print(n, 'reopened')" <key>
```

- [ ] **Run the pipeline again:** `python -m crm_review_app.pipeline`. Expected: `"new": 0`, and the app shows 0 pending.
- [ ] **Share the recording** so anyone with the link can view it, and save the link for the submission form.

## Step 3: Writeup

- [ ] **Review [`WRITEUP.md`](../WRITEUP.md)** (drafted 2026-11-25): the matching approach, how AI tools were used, and what to build next. Its numbers come from the read-only run. After the review, you can add how many proposals you approved and rejected (`store.counts`).
- [ ] **Commit it:** `git add WRITEUP.md`, then `git commit -m "docs: writeup"`.

## Step 4: Final check and submit

- [ ] `python -m crm_review_app.pipeline`: expect `"new": 0`.
- [ ] `python -m crm_review_app.audit --before data\snapshot\crm.json` against the live sandbox (read-only): expect `OK`, or only `still proposed` lines for proposals you rejected on purpose. Investigate any other `PROBLEM` line.
- [ ] `git status`: `.env` and `data/` are not tracked. `git grep -E "bh_[A-Za-z0-9_-]{16,}"` finds nothing (no token in tracked files).
- [ ] Push to the repository you'll submit (private, or as the form asks). Paste the repo link and the demo link into the submission form.
