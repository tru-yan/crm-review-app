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

Run these from the repo root, with the virtual environment active:

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

`mock_crm/` is a local copy of the CRM API, filled from a read-only snapshot of the live sandbox (`python -m mock_crm.snapshot`, which only reads and refuses to overwrite an existing snapshot). The snapshot must exist before the mock starts.

In one terminal, start the mock (it keeps running; restart it to reset it):

```powershell
$env:BH_MOCK_SEED = "data\snapshot\crm.json"; uvicorn mock_crm.server:build --factory --port 8001
```

In a second terminal, point the pipeline and the app at it with a separate database:

```powershell
$env:BH_API_BASE = "http://localhost:8001/api/v1"; $env:BH_DB_PATH = "data\test.db"
```

Each database is tied to the CRM it was first used with, so test decisions can't leak into the live run. The app shows a green banner for the mock and a red one for the live sandbox. `python -m bellhaven.audit --before data\snapshot\crm.json` checks the end state of whichever CRM is configured (read-only).

## Schedule

`deploy/crontab.txt` runs the pipeline daily at 06:00. Edit the path in it to your checkout; the token comes from the `.env` file there. Re-runs are safe:

- rejected proposals are never proposed again
- pending, failed and superseded ones are refreshed with current data
- undecided ones that a run no longer produces are marked superseded
- an applied one comes back only if the CRM has changed back, so the fix is needed again

On Windows, the equivalent is the command below (adjust the path):

```powershell
schtasks /Create /SC DAILY /ST 06:00 /TN "Bellhaven pipeline" /TR "cmd /c cd /d C:\Users\463995\rt_pg\crm && (if not exist logs mkdir logs) && .venv\Scripts\python.exe -m bellhaven.pipeline >> logs\pipeline.log 2>&1"
```
