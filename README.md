# CRM Review App

Keeps the CRM's record of Bellhaven Senior Living's communities correct. A daily pipeline scrapes every Bellhaven community from the website, matches each one to the CRM, and queues proposed fixes. A reviewer approves or rejects each fix in a small web app; only approved fixes are written back through the CRM API.

## Setup (Windows PowerShell, Python 3.12)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env   # then put your API token in .env
```

Settings come from `.env` or environment variables:

| Setting | Required | Default | What it is |
|---|---|---|---|
| `BH_API_TOKEN` | yes | — | Your CRM API token. It's also part of the CRM browser's address, so don't share that tab's address. |
| `BH_DB_PATH` | no | `data\state.db` | Where the SQLite database lives |
| `BH_NOT_ON_WEBSITE_STATUS` | no | `Needs Review` | Status proposed for a Bellhaven account the website no longer lists |
| `BH_SITE_BASE` | no | the exercise site | The website and API host |

## Use

Run these from the repo root, with the virtual environment active:

```powershell
python -m crm_review_app.pipeline      # daily job: scrape, read CRM, store proposals (never writes to the CRM)
python -m crm_review_app.web           # review app at http://localhost:8000: approve writes to the CRM, reject records it
python -m crm_review_app.audit --before data\snapshot\crm.json   # read-only check of the CRM's end state
```

State lives in `data\state.db` (SQLite): pipeline runs, proposals and decisions, every API call, and a field-by-field change log. Delete it only if you want to start reviewing from scratch. To see every change made through the API, with its old and new value:

```powershell
python -c "from crm_review_app import store; from crm_review_app.config import DB_PATH; [print(dict(r)) for r in store.change_history(store.connect(DB_PATH))]"
```

## Review app

`python -m crm_review_app.web` serves the app at http://localhost:8000, on this machine only. It looks like the Meridian CRM, with a red **LIVE SANDBOX** badge on every page:

- **Review queue:** pending proposals with each account's lifetime revenue and outstanding AR, highest outstanding AR first (then lifetime revenue), so the proposals with money at stake get reviewed first. Click any column header to sort by it, and click it again to reverse the order. Filter by type, or search by name or city.
- **Proposal page:** the reason it was proposed, the billing SOP check, the exact fields that change (now → after), the website and the CRM side by side with differences highlighted, and the match signals. Approve or reject with an optional comment; the app then opens the next pending proposal.
- **Change history** (every field written through the API) and **Pipeline runs**.

Every approval asks "Write this to the live sandbox?" and shows the exact change first. "View in Meridian ↗" links open the CRM browser. They go through the app, so the token is never in the page, but it is in the CRM browser's own address bar: don't share that address. The design is in `docs/review-app-design.md`.

## End-state check

`data\snapshot\crm.json` is a read-only copy of the sandbox, taken before anything was written to it. `python -m crm_review_app.audit --before data\snapshot\crm.json` compares the CRM as it is now with that copy, and only reads. It reports:

- fixes the pipeline would still propose (expected for proposals you rejected on purpose)
- billing SOP breaks: an account with revenue and AR that changed parent, or a kept CHOW account that was edited
- fields changed that this tool never edits, and accounts whose status changed to something other than Active or Needs Review without a duplicate link
- duplicate or CHOW links that don't point at a live account (a duplicate must also be Inactive, and a CHOW's new account must be under Bellhaven)
- accounts that disappeared, or were created more than once

It prints the CRM it checked, how many accounts were created and changed since the snapshot, and a `PROBLEM:` line for each issue, then `OK` or the number of problems (exiting with an error code if there are any).

## Schedule

`deploy/crontab.txt` runs the pipeline daily at 06:00, logging to `logs/pipeline.log`. Edit the path in it to your checkout; the token comes from the `.env` file there. Re-runs are safe:

- rejected proposals are never proposed again
- pending, failed and superseded ones are refreshed with current data
- undecided ones that a run no longer produces are marked superseded
- an applied one comes back only if the CRM has changed back, so the fix is needed again

On Windows, the equivalent is the command below (adjust the path):

```powershell
schtasks /Create /SC DAILY /ST 06:00 /TN "Bellhaven pipeline" /TR "cmd /c cd /d C:\Users\463995\rt_pg\crm && (if not exist logs mkdir logs) && .venv\Scripts\python.exe -m crm_review_app.pipeline >> logs\pipeline.log 2>&1"
```

## Project layout

| Path | What's there |
|---|---|
| `crm_review_app/` | The pipeline: `config`, `scrape`, `crm` (API client), `normalize`, `match`, `sop` (billing rule), `classify` (proposals), `store` (SQLite), `apply` (the only CRM writer), `pipeline` (daily job), `audit` (end-state check), `models` |
| `crm_review_app/web/` | The review app: `app` (routes), `review` (approve and reject), `present` (what the pages show), templates and static files |
| `deploy/crontab.txt` | The daily schedule |
| `docs/runbook.md` | What exploring the site and API found, the decisions, how the pieces fit, and the remaining live-phase steps |
| `docs/review-app-design.md` | The review app's design |
| `data/` (not in git) | `state.db`, and `snapshot/crm.json` for the audit |
