"""SQLite state (relational, foreign keys enforced): runs -> proposals -> api_log -> change_log.

Makes re-runs safe (decided proposals are never proposed again) and keeps a field-level audit trail
of every change made through the CRM API. Every write is one transaction: it commits or rolls back.
"""
import json
import sqlite3
from datetime import datetime, timedelta, timezone
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


_last = datetime.min.replace(tzinfo=timezone.utc)


def _now() -> str:
    # microseconds, strictly increasing within the process (the Windows clock ticks ~1 ms, so two calls could tie),
    # so "decided before this run started" can be told apart from "decided during it"
    global _last
    _last = max(datetime.now(timezone.utc), _last + timedelta(microseconds=1))
    return _last.isoformat(timespec="microseconds")


def connect(path, crm_base: str | None = None) -> sqlite3.Connection:
    """Open the database. With crm_base, the database is tied to that CRM on first use and refuses any other,
    so decisions made against the mock can never make the live run skip real fixes (or the reverse)."""
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")  # SQLite leaves them off unless asked, per connection
    conn.execute("PRAGMA journal_mode = WAL")  # the daily job and the review app may use the file at once
    conn.executescript(SCHEMA)
    if crm_base:
        with conn:
            conn.execute("INSERT OR IGNORE INTO meta (key, value) VALUES ('crm_base', ?)", (crm_base,))
        stored = conn.execute("SELECT value FROM meta WHERE key='crm_base'").fetchone()["value"]
        if stored != crm_base:
            conn.close()
            raise RuntimeError(f"{path} holds decisions for {stored}, not {crm_base}. "
                               "Use a separate database per CRM (set BH_DB_PATH).")
    return conn


def start_run(conn) -> int:
    with conn:
        cur = conn.execute("INSERT INTO runs (started_at) VALUES (?)", (_now(),))
    return cur.lastrowid


def finish_run(conn, run_id: int, stats: dict) -> None:
    with conn:
        conn.execute("UPDATE runs SET finished_at=?, stats=? WHERE id=?", (_now(), json.dumps(stats), run_id))


def add_proposals(conn, run_id: int, proposals) -> tuple[int, int]:
    """Insert new proposals, in one transaction. Returns (new, already_known).

    Rejected proposals are never touched again. Pending, failed and superseded ones are refreshed with today's
    CRM values and put back in the queue, so the freshness check before writing uses current data.
    An applied (or stuck applying) one that is produced again also goes back to pending: after a correct apply
    the next run no longer produces it, so seeing it again means the CRM changed back. That only counts for
    decisions made before this run started, so an approval made while this run was reading the CRM is kept.
    The status checks are part of each UPDATE, so a decision the review app makes meanwhile is never overwritten.
    """
    new = known = 0
    with conn:
        run = conn.execute("SELECT started_at FROM runs WHERE id=?", (run_id,)).fetchone()
        started_at = run["started_at"] if run else ""
        for p in proposals:
            values = (json.dumps(p.changes), json.dumps(p.before), json.dumps(p.evidence), p.confidence, run_id)
            if conn.execute("SELECT 1 FROM proposals WHERE key=?", (p.key,)).fetchone() is None:
                conn.execute(
                    "INSERT INTO proposals (key, kind, title, account_id, slug, changes, before, evidence, confidence,"
                    " run_id, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (p.key, p.kind, p.title, p.account_id, p.slug, *values, _now()))
                new += 1
                continue
            known += 1
            conn.execute(
                "UPDATE proposals SET changes=?, before=?, evidence=?, confidence=?, run_id=?, status='pending'"
                " WHERE key=? AND (status IN ('pending', 'failed', 'superseded')"
                " OR (status IN ('applied', 'applying') AND decided_at < ?))",
                (*values, p.key, started_at))
    return new, known


def supersede_missing(conn, current_keys) -> int:
    """Mark undecided proposals that this run no longer produces (fixed elsewhere, or the data changed).

    Call it only after a complete, successful run: an empty key list supersedes every undecided proposal.
    A superseded proposal that is produced again later is reopened by add_proposals.
    """
    keys = set(current_keys)
    with conn:
        stale = [r["key"] for r in conn.execute("SELECT key FROM proposals WHERE status IN ('pending', 'failed')")
                 if r["key"] not in keys]
        conn.executemany("UPDATE proposals SET status='superseded', decided_at=?"
                         " WHERE key=? AND status IN ('pending', 'failed')", [(_now(), k) for k in stale])
    return len(stale)


def list_proposals(conn, status: str | None = None):
    if status:
        return conn.execute("SELECT * FROM proposals WHERE status=? ORDER BY kind, title", (status,)).fetchall()
    return conn.execute("SELECT * FROM proposals ORDER BY kind, title").fetchall()


def get(conn, key: str):
    return conn.execute("SELECT * FROM proposals WHERE key=?", (key,)).fetchone()


def set_status(conn, key: str, status: str, comment: str | None = None, result: dict | None = None) -> None:
    with conn:
        conn.execute("UPDATE proposals SET status=?, decided_at=?, comment=COALESCE(?, comment),"
                     " result=COALESCE(?, result) WHERE key=?",
                     (status, _now(), comment, json.dumps(result) if result is not None else None, key))


def claim(conn, key: str) -> bool:
    """Mark a pending or failed proposal as being applied. False if it was already claimed or decided."""
    with conn:
        cur = conn.execute("UPDATE proposals SET status='applying', decided_at=?"
                           " WHERE key=? AND status IN ('pending', 'failed')", (_now(), key))
    return cur.rowcount == 1


def log_call(conn, key: str, method: str, path: str, body: dict, response: dict) -> int:
    with conn:
        cur = conn.execute("INSERT INTO api_log (proposal_key, method, path, body, response, at) VALUES (?,?,?,?,?,?)",
                           (key, method, path, json.dumps(body), json.dumps(response), _now()))
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
    with conn:
        conn.executemany("INSERT INTO change_log (api_call_id, proposal_key, account_id, action, field, old_value,"
                         " new_value, changed_at) VALUES (?,?,?,?,?,?,?,?)", rows)
    return len(rows)


def change_history(conn, account_id: str | None = None, proposal_key: str | None = None):
    sql, args = "SELECT * FROM change_history WHERE 1=1", []
    if account_id is not None:
        sql, args = sql + " AND account_id=?", args + [account_id]
    if proposal_key is not None:
        sql, args = sql + " AND proposal_key=?", args + [proposal_key]
    return conn.execute(sql, args).fetchall()


def counts(conn) -> dict:
    return {r[0]: r[1] for r in conn.execute("SELECT status, COUNT(*) FROM proposals GROUP BY status")}
