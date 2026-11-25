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




def test_superseded_proposal_that_comes_back_is_reopened():
    conn = store.connect(":memory:")
    p = proposal()
    store.add_proposals(conn, store.start_run(conn), [p])
    store.supersede_missing(conn, [])
    store.add_proposals(conn, store.start_run(conn), [p])
    assert store.get(conn, p.key)["status"] == "pending"


def test_approval_made_while_a_run_reads_the_crm_is_kept():
    conn = store.connect(":memory:")
    p = proposal()
    store.add_proposals(conn, store.start_run(conn), [p])
    run = store.start_run(conn)                                         # the next run starts reading the CRM...
    store.set_status(conn, p.key, "applied", None, {"updated": "A1"})  # ...the reviewer approves meanwhile...
    store.add_proposals(conn, run, [p])                                # ...and the run's older snapshot has it
    assert store.get(conn, p.key)["status"] == "applied"


def test_supersede_leaves_decided_proposals_alone():
    conn = store.connect(":memory:")
    run = store.start_run(conn)
    applied, rejected, pending = proposal(), proposal(account_id="A2"), proposal(account_id="A3")
    store.add_proposals(conn, run, [applied, rejected, pending])
    store.set_status(conn, applied.key, "applied")
    store.set_status(conn, rejected.key, "rejected")
    assert store.supersede_missing(conn, []) == 1
    assert [store.get(conn, p.key)["status"] for p in (applied, rejected, pending)] == ["applied", "rejected",
                                                                                          "superseded"]


def test_failed_batch_leaves_nothing_behind():
    conn = store.connect(":memory:")
    run = store.start_run(conn)
    good, broken = proposal(), proposal(account_id="A2", evidence={"bad": object()})  # not JSON-serialisable
    with pytest.raises(TypeError):
        store.add_proposals(conn, run, [good, broken])
    assert not conn.in_transaction
    assert conn.execute("SELECT COUNT(*) FROM proposals").fetchone()[0] == 0


def test_reject_only_works_on_undecided_proposals():
    conn = store.connect(":memory:")
    run = store.start_run(conn)
    busy, waiting = proposal(), proposal(account_id="A2")
    store.add_proposals(conn, run, [busy, waiting])
    assert store.claim(conn, busy.key)  # an apply is in progress in another tab
    assert store.reject(conn, busy.key, "too late") is False
    assert store.get(conn, busy.key)["status"] == "applying"
    assert store.reject(conn, waiting.key, "not this one") is True
    assert (store.get(conn, waiting.key)["status"], store.get(conn, waiting.key)["comment"]) == ("rejected",
                                                                                                "not this one")


def test_an_apply_still_in_flight_is_not_reopened():
    conn = store.connect(":memory:")
    p = proposal()
    store.add_proposals(conn, store.start_run(conn), [p])
    assert store.claim(conn, p.key)  # Approve was clicked...
    store.add_proposals(conn, store.start_run(conn), [p])  # ...and a pipeline run starts while it is writing
    assert store.get(conn, p.key)["status"] == "applying"


def test_a_long_stuck_apply_is_reopened():
    conn = store.connect(":memory:")
    p = proposal()
    store.add_proposals(conn, store.start_run(conn), [p])
    store.claim(conn, p.key)
    with conn:
        conn.execute("UPDATE proposals SET decided_at='2000-01-01T00:00:00.000000+00:00' WHERE key=?", (p.key,))
    store.add_proposals(conn, store.start_run(conn), [p])
    assert store.get(conn, p.key)["status"] == "pending"


def test_failed_proposal_with_a_logged_write_is_not_superseded():
    conn = store.connect(":memory:")
    p = proposal()
    store.add_proposals(conn, store.start_run(conn), [p])
    store.claim(conn, p.key)
    store.log_call(conn, p.key, "PATCH", "/accounts/A1", {"name": "X"}, {"error": "timed out"})
    store.set_status(conn, p.key, "failed")
    assert store.supersede_missing(conn, []) == 0
    assert store.get(conn, p.key)["status"] == "failed"
