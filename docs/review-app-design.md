# Review app design: CRM Review App

The review app is where a person approves or rejects each fix the pipeline proposes. Only approved fixes are written to the graded sandbox. It looks like the Meridian CRM (the sandbox's read-only front end), so reviewing about 29 proposals and showing an approved change landing in the CRM are both quick and obvious.

This describes the app as it is now. The code in `crm_review_app/web/` is the reference.

## Decisions

| Topic | Decision |
|---|---|
| Screens | Review queue, proposal detail, change history, pipeline runs |
| Layout | A queue table, then one page per proposal (Meridian's list → detail pattern), moving to the next pending proposal after each decision |
| Confirmation | Every approval asks "Write this to the live sandbox?" first, and the server enforces it |
| Technology | FastAPI with Jinja2 pages rendered on the server, Meridian's CSS, and a little vanilla JavaScript. No Node and no build step. It replaced an earlier Streamlit app. |
| Where it runs | This machine only: `python -m crm_review_app.web` serves http://127.0.0.1:8000 |

## Look (Meridian style)

- **Colours** (from Meridian's CSS): navy `#1b4f72` for the top bar, buttons and links; page background `#eef1f4`, ink `#22303a`, lines `#d5dce2`, muted text `#64748b`.
- **Text and controls:** a system font stack at 14px. White table boxes with 6px corners. Uppercase 11px table headers and field labels.
- **Status pills** (a proposal's status): pending amber (`#f6ecd9` / `#8a5a10`), applying blue, applied green (`#e2f2e6` / `#1e6e46`), failed red (`#f1e4e4` / `#9c3a3a`), rejected and superseded grey.
- **Type pills:**

  | Type | Pill label | Colours |
  |---|---|---|
  | update | Update | blue `#e6f1fb` / `#0c447c` |
  | chow | Ownership | purple `#eeedfe` / `#3c3489` |
  | create | New account | teal `#e1f5ee` / `#085041` |
  | duplicate | Duplicate | coral `#faece7` / `#712b13` |
  | not_on_website | Not on site | grey `#f1efe8` / `#444441` |

  An Administrator contact change is part of the account's **Update**, so each account has one review. An account whose fields all agree gets an update with only the contact change. Older proposals stored with the kind `administrator` (now superseded) also show as Update.

- **LIVE SANDBOX badge** in red (`#fcebeb` / `#791f1f`) at the right of the top bar, on every page: every approved write lands in the graded copy.

## Screens

### Top bar (every page)
"CRM Review App", then Queue (with the pending count), Change history, Pipeline runs, "Open Meridian ↗" (the CRM browser's account list), and the LIVE SANDBOX badge.

### Review queue: `GET /queue`
- **Header:** "Review queue", with the last pipeline run's time and what it found (communities, CRM accounts, proposals), why it failed, or "Running or interrupted". Before any run: "No pipeline run yet."
- **Status tabs with counts:** Pending (the default), Applied, Rejected, Failed, Superseded, All. Proposals being applied appear under All.
- **Filters** (query parameters): a type select (`kind`, "All types") and a search on account name or city (`q`).
- **Columns:** Type (pill), Account (name linking to its page, with city, state and account id underneath, and a "Review carefully" tag for low confidence), Change (a one-line summary), Lifetime revenue and Outstanding AR (the CRM account's, right-aligned; AR in red when above zero; "—" for a new account, which has no CRM record yet), Status (pill). Clicking anywhere on a row opens it.
- **Order** (`sort`, chosen by clicking any column header; clicking the sorted column again reverses it, with `order=asc` or `desc`; the arrow shows the direction, and the tabs and filters keep both):
  - **Outstanding AR** (the default): highest first, then lifetime revenue, so the proposals with money at stake come first.
  - **Lifetime revenue:** highest first, then outstanding AR.
  - **Type:** the kinds that add an account first (ownership, new account), then duplicate, not on site, update.
  - **Account** and **Change:** A to Z, by the account's name or by the one-line summary.
  - **Status:** what still needs a decision first: pending, applying, failed, applied, rejected, superseded.
  - Ties fall back to the type order, then the account name. New accounts have no billing history, so they come last in the money sorts, in either direction. Unreadable amounts count as money involved: highest of all.
  - A proposal's "n of N pending" and "Next pending" follow the default order.
- **One-line summaries:**

  | Type | Summary |
  |---|---|
  | update | The changes, for example "Name → Bellhaven of Chagrin Falls" or "Zip 45626 → 45662" (short fields and the phone show their old value too). The parent is shown by name, as "Parent → Bellhaven Senior Living", and the contact change as "Administrator {old} → {new}", or "Add administrator {new}" when the account has none. After two, "+N more"; the phone is listed last, so a name, parent or administrator change is never hidden behind it. The note is never shown or counted. |
  | chow | "New Bellhaven account; old one kept for billing" |
  | create | "Create under Bellhaven" |
  | duplicate | "Duplicate of {survivor name}", plus the survivor's id when the two copies have the same name |
  | not_on_website | "Status → Needs Review" |

- **Empty states:** "No pending proposals. Run the pipeline to check for changes.", "No {status} proposals yet.", or "Nothing matches these filters."

### Proposal page: `GET /proposals/{key}`
- **Breadcrumb:** "Queue / {type}", "n of N pending" when it's pending, and a "Next pending →" link.
- **Heading:** the account name (the new account's name for a create or CHOW), the type and status pills, and "View in Meridian ↗", with the proposal's title and account id underneath. For a create, the link appears once the account exists; for an applied CHOW, it goes to the new account.
- **Reason:** the reasons, as plain sentences. Low confidence adds "Review carefully".
- **Billing SOP box** (pale yellow), for a CHOW or an update that changes the parent: "Lifetime revenue $X, outstanding AR $Y: keep the old account and use a change of ownership", or "…: re-parenting allowed".
- **Now → After:** only the fields that change. The old value is struck through, the new one is green, and an empty value reads "empty". A create or CHOW lists the new account's fields (marked "new"). An update's contact change adds a row for the Administrator contact, now → the new one, and one for "{old name}'s contact: active → inactive", after the account's fields and before the note. On an update, the CRM grid adds the account's Administrator contact next to the website's administrator. A CHOW groups its rows under "New account" and "Old account {id}", the old account's only row being its link field. The parent shows by name, and duplicate and CHOW links as "Name (id)".
- **Website vs CRM when proposed:** two field grids, with fields that disagree highlighted, and outstanding AR in red when above zero. The website side links to the community's page. A missing side reads "Not on the website" or "No CRM account".
- **How it matched:** "Matched on: {rule}" (same address, near address or same people) and one chip per piece of evidence that agrees (street, administrator, phone, name, zip, city). A create lists its possible matches instead ("Possible matches, not used"), each with the rule that made it possible.
- **Actions** (pending or failed proposals): an optional reviewer comment ("Add comment..."), then Approve (Retry after a failure) and Reject.
- **Failed:** "Last attempt failed at {time} · {reason}". **Applying:** how to tell whether it's still being written, and that a pipeline run 10 or more minutes later reopens it if the change didn't land.
- **Decided:** "{Status} at {time} · {comment}", any fields the CRM stored differently, and the change log for this proposal (account, action, field, before → after, when).

### Confirmation before a write
- Approve opens a dialog (or, with JavaScript off, the server's own confirmation page) titled "Write this to the live sandbox?", noting "This is the graded CRM. It has no delete." It lists the account and the exact Now → After rows, and for a create or CHOW, "Creates 1 account that can't be deleted." (for an update with a contact change, "Adds 1 contact that can't be deleted.") Cancel, or a red "Write to live CRM" button.
- **The server enforces it:** `POST /proposals/{key}/approve` without `confirmed=yes` returns the confirmation page for a pending or failed proposal, and writes nothing. A proposal that's already decided or being applied gets the "Already handled" banner instead.

### After a decision
- The response is a 303 redirect to the next pending proposal in queue order, or to the queue when none are left, carrying `?last={key}&outcome={outcome}`.
- That page shows one banner, read from the stored proposal. Written, Already written, Not written, Failed and Rejected show only if the proposal's status still matches (so an old link never announces a write); the others always show:

  | Banner | When |
  |---|---|
  | Written (green) | Applied: the one-line summary, then "The CRM stored exactly what was sent" or the fields stored differently, and "View in Meridian ↗" |
  | Already written (green) | The change was already in the CRM (an earlier response was lost); it's now recorded |
  | Not written (amber) | The record changed since the proposal was made; names the field, and says to re-run the pipeline |
  | Failed (red) | The error, with a Retry button |
  | Rejected (grey) | The proposal's title |
  | Already handled (amber) | It was decided already, or another click is applying it |
  | Written, but not recorded (red) | The CRM write went through, but saving the decision failed |
  | Not saved (red) | The database failed while handling the decision |

### Change history: `GET /history`
Every field written through the API, newest first: when, account (linking to its proposal, with "↗" to Meridian and the id underneath), action (create or update), field, before → after, and the reviewer's comment. `q` filters by account name or id. Names come from the stored proposals, falling back to the id.

### Pipeline runs: `GET /runs`
Newest first: started, result (OK in green; "Failed: {error}" in red; "Running or interrupted" in amber), communities, accounts, contacts, proposals, and new / already known / superseded.

### Errors
An unknown proposal shows "Proposal not found" (404). A refused request (403), an unknown page (404) or a wrong method (405) shows "That didn't go through" with the reason and a link back to the queue.

### CRM browser links: `GET /meridian/accounts[/{account_id}]`
Every "Meridian ↗" link points here, and the app answers with a 303 redirect to the CRM browser. The CRM browser's address contains the API token, so the token never appears in a page, where hovering a link would show it on screen.

## Architecture

Package `crm_review_app/web/`:

| File | Responsibility |
|---|---|
| `review.py` | `approve(conn, client, key, comment=None, *, confirmed=False)` and `reject(conn, key, comment=None)`, returning an `Outcome`: `applied`, `already_applied`, `stale`, `failed`, `already_handled`, `rejected`, `needs_confirmation`, `unrecorded` or `error`. `approve` calls `apply.apply_proposal`, stores the result on the proposal, logs errors, and never raises. |
| `present.py` | Pure functions from stored proposals to what the pages show: `summary`, `change_rows`, `side_by_side`, `signals`, `sop`, `queue_order`, `position`, `next_pending`, `banner`, `account_names`, `run_view` and the labels. No database, network or HTML. |
| `app.py` | `create_app(conn, client, meridian_base)` builds the FastAPI app: the routes above, the CSRF and origin checks, host and framing protection, templates and static files. `build()` makes it from `.env` (database, CRM client, CRM browser address with the token). |
| `__main__.py` | `python -m crm_review_app.web` runs uvicorn on 127.0.0.1:8000 |
| `templates/` | `base.html` (top bar, badge, banner), `queue.html`, `proposal.html`, `_confirm_body.html` (shared by the dialog and the confirmation page), `confirm.html`, `_values.html` (how old, new and empty values look), `history.html`, `runs.html`, `error.html` |
| `static/` | `meridian.css` (Meridian's rules plus the app's), `review.js` (the confirmation dialog, one submit per page, a reload for pages restored from the browser's cache, clickable queue rows). Every page works without it. |

**Field labels** (`present.field_label`): `name` Name, `parent_id` Parent, `billing_street` Street, `billing_city` City, `billing_state` State, `billing_zip` Zip, `care_type` Care type, `status` Status, `phone` Phone, `note` Note, `duplicate_of_account` Duplicate of, `chow_current_account` CHOW current account.

**What it relies on elsewhere:** `store.latest_run` and `store.list_runs` for the run pages, `store.fail_run` (the pipeline records why a run failed), and the survivor's name in a duplicate's evidence (from `classify.py`).

## Safety

- **Writes only on POST** (`/approve`, `/reject`). GET pages only read.
- **CSRF:** a random token, made when the app starts, goes into a hidden field in every form and must match on every POST, or the request gets a 403. A POST whose `Origin` (or `Referer`) names a site other than `http://127.0.0.1:8000` or `http://localhost:8000` also gets a 403.
- **This machine only:** uvicorn binds to 127.0.0.1, and the app answers only to the host names 127.0.0.1 and localhost (so a page fetched under another name, as in DNS rebinding, is refused). Pages can't be framed by another site (`X-Frame-Options: DENY`, `frame-ancestors 'none'`).
- **Confirmation is enforced by the server,** as above. After the dialog submits, the confirmation value is cleared, so the browser's Back button can't restore it.
- **The token** only appears in the `/meridian` redirect, never in a page.
- **Escaping:** Jinja2 autoescape is on for every template, because proposal text comes from the website and the CRM.
- **One request at a time** uses the shared SQLite connection (a lock).
- **The write guards in `apply.py` apply unchanged:** the atomic claim (a double click can't write twice), the freshness and rule re-checks, create-once, and the latest-run check for creates.

## Error handling

- `review.approve` and `review.reject` never let an exception reach the route. An API or unexpected error becomes `failed`, with `Type: message` stored on the proposal; a database error becomes `error`. Both are logged to the server output.
- A missing API token stops the app at startup with a message saying how to set it.

## Out of scope

Bulk approve; editing a proposal's values in the app; multiple users or sign-in; dark mode; running anywhere but this machine; linking a CHOW to an existing Bellhaven copy instead of creating a new account.
