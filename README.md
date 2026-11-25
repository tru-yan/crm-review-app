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
