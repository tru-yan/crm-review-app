# Review app redesign: Meridian-style FastAPI app

Status: approved design, 2026-11-24. It replaces the Streamlit `review_app.py`.

## Goal

A review app that looks and feels like the Meridian CRM (the sandbox's read-only front end) and is intuitive for the reviewer. Reviewing about 29 proposals, recording the demo, and showing an approved change landing in the CRM should all be quick and obvious. Safety must be at least as strong as today's.

## Decisions

| Topic | Decision |
|---|---|
| Screens | Review queue, proposal detail, change history, pipeline runs |
| Layout | A queue table, then one detail page per proposal (Meridian's list → detail pattern), with "Next pending" after each decision |
| Live confirmation | Every approval on the live sandbox asks for confirmation. The mock never does. |
| Technology | FastAPI and Jinja2 pages rendered on the server, Meridian's CSS, and a little vanilla JavaScript. No Node and no build step: FastAPI, Jinja2, uvicorn and python-multipart are already installed. |
| Streamlit | Removed, together with its dependency, once the new app does everything the old one did, in the same change |

## Look (Meridian style)

- **Colours** (from Meridian's CSS):
  - navy `#1b4f72` for the top bar, buttons and links
  - page background `#eef1f4`, ink `#22303a`, lines `#d5dce2`, muted text `#64748b`
- **Text and controls:** a system font stack at 14px. White table boxes with 6px corners. Uppercase 11px table headers and field labels.
- **Status pills:** Active is green (`#e2f2e6` / `#1e6e46`), Inactive is red (`#f1e4e4` / `#9c3a3a`), and Needs Review or Pending is amber (`#f6ecd9` / `#8a5a10`).
- **Kind pills:**

  | Kind | Pill label | Colours |
  |---|---|---|
  | update | Update | blue `#e6f1fb` / `#0c447c` |
  | chow | Ownership | purple `#eeedfe` / `#3c3489` |
  | create | New account | teal `#e1f5ee` / `#085041` |
  | duplicate | Duplicate | coral `#faece7` / `#712b13` |
  | not_on_website | Not on site | gray `#f1efe8` / `#444441` |

- **Environment badge,** at the right of the top bar: green "Mock CRM" (`#e2f2e6` / `#1e6e46`) or red "LIVE SANDBOX" (`#fcebeb` / `#791f1f`). It follows `config.USING_MOCK`.

## Screens

### Top bar (every page)
- "Bellhaven review", then links to Queue (with the pending count), Change history and Pipeline runs, then the environment badge.
- On live only, an "Open Meridian ↗" link to the CRM browser's accounts list.

### Review queue: `GET /queue`
- **Header:** the title "Review queue", plus the last pipeline run's time and what it found (communities and accounts). If there has never been a run, say so.
- **Status tabs with counts:** Pending, Applied, Rejected, Failed, Superseded, All. The default is Pending. Rows with status `applying` appear under All and are counted there.
- **Filters:** a kind select and a text search on account name or city. Filters are query parameters: `status`, `kind`, `q`.
- **Table columns:** Kind (pill), Account (name as a link to the detail page, with city and state underneath), Change (the one-line summary), Status (pill).
- **Low-confidence rows** show a small "Review carefully" tag.
- **Sort order:**
  1. Kind: ownership, then create, duplicate, not_on_website, update. The irreversible kinds come first.
  2. Account name, within each kind.
- **One-line summaries:**

  | Kind | Summary |
  |---|---|
  | update | The changed fields, using the labels from "Field labels" below, for example "Name → Bellhaven of Chagrin Falls" or "Zip 45626 → 45662". The parent is shown by name, as "Parent → Bellhaven Senior Living". More than two fields: the first two, then "+N more". |
  | chow | "New Bellhaven account; old one kept for billing" |
  | create | "Create under Bellhaven" |
  | duplicate | "Duplicate of {survivor name}" |
  | not_on_website | "Status → Needs Review" |

- **Empty state:** "No pending proposals. Run the pipeline to check for changes."

### Proposal detail: `GET /proposals/{key}`
- **Breadcrumb and position:** "Queue / {kind}", plus "n of N pending" when the proposal is pending.
- **Heading line:** the account name (or the new account's name for create and CHOW), the kind pill, the status pill, and on live a "View in Meridian ↗" link to `{SITE_BASE}/crm/{token}/accounts/{account_id}`. For create and an applied CHOW, the link goes to the created account.
- **"Why" box:** `evidence.reasons` as plain sentences. Low confidence adds "Review carefully" with the reason.
- **Billing SOP box** (amber, like the brief's), for chow, or for an update that changes `parent_id`: "Lifetime revenue $X, outstanding AR $Y: keep the old account and use a change of ownership", or "...: re-parenting allowed".
- **"Now → After" table:** only the fields that change.
  - The current value is struck through and the new one shown in green.
  - Empty values show as "empty".
  - For create and CHOW, the table lists the new account's fields (with "new" as the current value), and for CHOW also the old account's single link field.
  - Notes are shown in full.
  - `parent_id` is shown by parent name.
- **Website vs CRM side by side:** two Meridian-style field grids, with fields that differ highlighted amber. Website fields: name, street, city/state/zip, phone, administrator, care offerings. CRM fields: name, parent, street, city/state/zip, phone, care type, status, revenue, AR. Either side shows "Not on the website" or "No CRM account" when absent.
- **Match signals:** "Match {score}" followed by one chip per signal, for example "street +50" or "administrator +25". For a create, the weak candidates are listed instead.
- **Action box** (pending or failed proposals only):
  - an optional reviewer comment, then Approve (primary) and Reject (secondary)
  - shortcut hints: A approve, R reject, N next pending, Q queue
- **Failed proposals** show "Last attempt failed at {time}: {reason}" above the action box.
- **Applying proposals** show the stuck-apply explanation: wait, or re-run the pipeline 10 or more minutes later.
- **Decided proposals** show "{status} at {time} · {comment}", plus a change-log table for that proposal (account, action, field, before → after, when).

### Live confirmation
- On live, Approve opens a confirmation. It's a native `<dialog>` when JavaScript is on, or a server-rendered confirmation page when it's off. It shows:
  - the title "Write this to the live sandbox?" and the note "This is the graded CRM. It has no delete."
  - the exact change: the account name and id, then each field's now → after (the same rows as the Now → After table)
  - for create and CHOW, also: "Creates 1 account that can't be deleted."
  - Cancel, and a red "Write to live CRM" button, which submits the form with `confirmed=yes`
- **The server enforces it.** On live, `POST /proposals/{key}/approve` without `confirmed=yes` returns the confirmation page (HTTP 200) and writes nothing. On the mock, `confirmed` is ignored.

### After a decision
- The response is a `303` redirect to the next pending proposal (in queue order) with `?last={key}`, or to `/queue?last={key}` when none are left.
- The page reads the last proposal's result from the database and shows one banner:
  - **Green "Written":** a one-line summary, plus "The CRM stored exactly what was sent" when there were no mismatches, or the list of mismatched fields if there were. On live, "View in Meridian ↗".
  - **Green "Already written":** "This change was already in the CRM (an earlier response was lost); it's now recorded."
  - **Amber "Not written":** the record changed since the proposal was made. The `StaleProposal` text names the field, and it suggests "Re-run the pipeline."
  - **Red "Failed":** the error text, and for a CHOW that failed after its create, "Retry it now, before the next pipeline run" with a Retry button that re-posts approve.
  - **Gray "Rejected":** the proposal title.
  - **Amber "Already handled":** the proposal's current status.

### Change history: `GET /history`
- Newest first: when, account (a link to the proposal, plus "↗" to Meridian on live), action (create or update), field, before → after, and the reviewer's comment.
- **Filter:** `q` matches the account name or id.
- **Data:** it reads `store.change_history`. Account names come from the proposal (the `crm.name` in its evidence, or the new account's `name` for create and CHOW), and fall back to the account id when no name is known.

### Pipeline runs: `GET /runs`
- Newest first: started, result (OK when `finished_at` is set; "Failed: {error}" when the stats hold an error; "Running or interrupted" otherwise), communities, accounts, contacts, proposals, and new / already known / superseded.

## Architecture

New package `bellhaven/web/`:

| File | Responsibility |
|---|---|
| `review.py` | `approve(conn, client, key, comment, confirmed: bool, live: bool) -> Outcome` and `reject(conn, key, comment) -> Outcome`. They map every `apply_proposal` exception to an outcome (`applied`, `already_applied`, `stale`, `failed`, `already_handled`, `rejected`, `needs_confirmation`) and set the proposal status exactly as `review_app.decide` does today. `mock_crm/approve_all.py` uses it too. |
| `present.py` | Pure view-model functions over store rows: `summary(row)`, `change_rows(row)` (Now → After), `side_by_side(row)` (website vs CRM with differences flagged), `signals(row)`, `kind_label(kind)`, `queue_order(rows)`, `field_label(field)`. They make no database or network calls. |
| `app.py` | `create_app(conn, client, live: bool, meridian_base: str or None, allowed_origins=None) -> FastAPI`, holding the routes above, the CSRF check, the static files and the templates. `build()` reads config (`DB_PATH`, `API_BASE`, `USING_MOCK`, the token) for uvicorn. |
| `__main__.py` | `python -m bellhaven.web` runs uvicorn on `127.0.0.1:8000`. |
| `templates/` | `base.html` (top bar, badge, banner), `queue.html`, `proposal.html`, `confirm.html`, `history.html`, `runs.html` |
| `static/` | `meridian.css` (Meridian's rules plus the additions above), `review.js` (keyboard shortcuts and the confirm dialog, as a progressive enhancement) |

**Field labels for display** (`present.field_label`):

| Field | Label |
|---|---|
| `name` | Name |
| `parent_id` | Parent |
| `billing_street` | Street |
| `billing_city` | City |
| `billing_state` | State |
| `billing_zip` | Zip |
| `care_type` | Care type |
| `status` | Status |
| `phone` | Phone |
| `note` | Note |
| `duplicate_of_account` | Duplicate of |
| `chow_current_account` | CHOW current account |

Other changes:
- `store.fail_run(conn, run_id, error)` records `{"error": ...}` in `runs.stats` and leaves `finished_at` NULL.
- `pipeline.run` calls it in an `except` block and re-raises.
- `store.latest_run(conn)` returns the most recent run for the queue header.
- No schema changes.

## Safety

- **Writes only happen on POST** (`/approve`, `/reject`). GET handlers never call `review.approve` or `review.reject`, and a test enforces this.
- **CSRF:**
  - A random token is generated when the app starts. It goes into a hidden field in every form and must match on every POST, otherwise the request gets a 403.
  - POSTs whose `Origin` (or `Referer`) header is present and doesn't match one of the app's own origins also get a 403. Those origins are passed to `create_app(allowed_origins=…)` and default to `http://127.0.0.1:8000` and `http://localhost:8000`. A POST with neither header is allowed, because the CSRF token still applies.
- **Local only:** uvicorn binds to `127.0.0.1`.
- **Live confirmation is enforced on the server,** as described above.
- **Existing guards apply unchanged:**
  - the `apply.claim` double-click guard
  - the freshness and rule re-checks
  - create-once, and the latest-run check for creates
  - the `store.connect(crm_base=…)` tie between a database and one CRM
- **The token appears only in rendered links on live pages.** It's never written to disk by the app, and isn't used at all on the mock.
- **Escaping:** Jinja2 autoescape stays on for all templates, since proposal text comes from the website and the CRM.

## Error handling

- `review.approve` never lets an exception escape to the route. Unexpected errors become `failed`, with `type(e).__name__` and the message stored in the proposal's `result`, as today.
- An unknown proposal key returns 404 with a link back to the queue.
- A database tied to a different CRM (the `RuntimeError` from `store.connect`) stops the app at startup with that message.

## Testing

| File | Covers |
|---|---|
| `tests/test_review_service.py` | Each outcome: applied, already applied, stale, failed (CrmError and an unexpected exception), already handled, rejected, a rejected proposal that is no longer pending, and needs-confirmation on live without `confirmed` (with no writes) |
| `tests/test_present.py` | Summaries for every kind (including "+N more"), change rows for update, create, chow, duplicate and not_on_website, parent shown by name, side-by-side difference flags, queue ordering |
| `tests/test_web.py` | FastAPI `TestClient` with `FakeCrm`, an in-memory store and fixture proposals |

`tests/test_web.py` checks that:
- every GET page returns 200, an unknown key returns 404, and no GET writes
- approve on the mock writes and redirects (303) to the next pending proposal, with the banner on the next page
- approve on live without `confirmed` returns the confirmation page and doesn't write; with `confirmed=yes` it writes
- a missing or wrong CSRF token gets 403, and a foreign `Origin` gets 403
- reject is guarded
- the environment badge text matches mock and live
- the Meridian link appears only on live, and never contains the token on mock pages

Also:
- `tests/test_pipeline.py`: a failed run records its error.
- The existing suite (147 tests) stays green, including the end-to-end test. `approve_all` keeps its refusal on live.
- **Manual:** a controller click-through in a browser against the mock (queue, filters, detail, approve, reject, keyboard shortcuts, history, runs), and the confirmation page with JavaScript off.

## Switching over

- Same branch, as one change: add `bellhaven/web`, then delete `review_app.py` and remove `streamlit` from `requirements.txt` once the app does everything the old one did.
- Update `README.md`: start the app with `python -m bellhaven.web`, at http://localhost:8000.
- Update the plan's live-phase steps: the app's address, and the confirmation step.

## Out of scope

- Bulk approve.
- Editing a proposal's values in the app.
- Multiple users or sign-in.
- Dark mode.
- Deployment beyond the local machine.
- Linking a CHOW to an existing Bellhaven copy, which is deferred as before.
