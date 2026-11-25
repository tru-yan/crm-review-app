import json
import sqlite3

import pytest

from bellhaven import store
from bellhaven.crm import CrmError
from bellhaven.web import review
from tests.review_fixtures import by_kind, make_queue


def setup(kind):
    crm, conn = make_queue()
    return crm, conn, by_kind(conn)[kind]["key"]


def locked(*args, **kwargs):
    raise sqlite3.OperationalError("database is locked")


def test_approve_writes_and_records_applied():
    crm, conn, key = setup("update")
    assert review.approve(conn, crm, key, "looks right", live=False).kind == review.APPLIED
    row = store.get(conn, key)
    assert (row["status"], row["comment"]) == ("applied", "looks right")
    assert crm.accounts["A1"]["name"] == "Bellhaven of Chagrin Falls"


def test_live_must_be_named():
    crm, conn, key = setup("update")
    with pytest.raises(TypeError):
        review.approve(conn, crm, key, "", False, True)  # positional flags could be swapped by mistake
    with pytest.raises(TypeError):
        review.approve(conn, crm, key)  # and there is no default
    assert crm.writes == []


def test_a_change_already_in_the_crm_is_recorded_not_written_again():
    crm, conn, key = setup("update")
    crm.accounts["A1"].update(json.loads(store.get(conn, key)["changes"]))
    assert review.approve(conn, crm, key, live=False).kind == review.ALREADY_APPLIED
    assert crm.writes == [] and store.get(conn, key)["status"] == "applied"


def test_a_stale_proposal_fails_with_the_reason_and_writes_nothing():
    crm, conn, key = setup("update")
    crm.accounts["A1"]["name"] = "Someone Renamed It"
    outcome = review.approve(conn, crm, key, "checked", live=False)
    assert outcome.kind == review.STALE and "Someone Renamed It" in outcome.message
    row = store.get(conn, key)
    assert row["status"] == "failed" and "Someone Renamed It" in json.loads(row["result"])["stale"]
    assert row["comment"] == "checked"
    assert crm.writes == []


def test_a_crm_error_fails_with_the_error():
    crm, conn, key = setup("update")

    def refuse(account_id, body):
        raise CrmError("PATCH /accounts/A1 -> 422: bad body")

    crm.update_account = refuse
    assert review.approve(conn, crm, key, live=False).kind == review.FAILED
    assert json.loads(store.get(conn, key)["result"]) == {"error": "CrmError: PATCH /accounts/A1 -> 422: bad body"}


def test_an_unexpected_error_never_escapes():
    crm, conn, key = setup("update")
    crm.get_account = lambda account_id: 1 / 0
    outcome = review.approve(conn, crm, key, live=False)
    assert outcome.kind == review.FAILED and outcome.message.startswith("ZeroDivisionError")
    assert store.get(conn, key)["status"] == "failed"


def test_database_errors_never_escape(monkeypatch):
    crm, conn, key = setup("update")
    monkeypatch.setattr(store, "get", locked)
    assert review.approve(conn, crm, key, live=False).kind == review.ERROR
    monkeypatch.setattr(store, "reject", locked)
    assert review.reject(conn, key).kind == review.ERROR
    assert crm.writes == []


def test_a_failure_that_cannot_be_saved_is_an_error(monkeypatch):
    crm, conn, key = setup("update")
    crm.accounts["A1"]["name"] = "Someone Renamed It"  # stale
    real = store.set_status

    def broken(conn, key, status, *args, **kwargs):
        if status == "failed":
            raise sqlite3.OperationalError("database is locked")
        return real(conn, key, status, *args, **kwargs)

    monkeypatch.setattr(store, "set_status", broken)
    assert review.approve(conn, crm, key, live=False).kind == review.ERROR
    assert store.get(conn, key)["status"] == "applying"  # claimed, so no other click can write it
    assert crm.writes == []


def test_a_decided_proposal_is_already_handled():
    crm, conn, key = setup("update")
    review.approve(conn, crm, key, live=False)
    again = review.approve(conn, crm, key, live=False)
    assert again.kind == review.ALREADY_HANDLED and "applied" in again.message
    assert review.approve(conn, crm, key, live=True).kind == review.ALREADY_HANDLED  # no confirmation asked
    assert review.approve(conn, crm, "no-such-key", live=False).kind == review.ALREADY_HANDLED
    assert len(crm.writes) == 1


def test_a_proposal_being_applied_is_already_handled():
    crm, conn, key = setup("update")
    store.claim(conn, key)  # another click is applying it
    assert review.approve(conn, crm, key, live=False).kind == review.ALREADY_HANDLED
    assert crm.writes == []


def test_a_click_that_loses_the_claim_race_changes_nothing(monkeypatch):
    crm, conn, key = setup("update")
    real_claim = store.claim

    def other_click_wins(conn, key):
        real_claim(conn, key)  # the other click claims it between our check and our claim
        return real_claim(conn, key)

    monkeypatch.setattr(store, "claim", other_click_wins)
    assert review.approve(conn, crm, key, "mine", live=False).kind == review.ALREADY_HANDLED
    row = store.get(conn, key)
    assert (row["status"], row["comment"], row["result"]) == ("applying", None, None)
    assert crm.writes == []


def test_live_needs_confirmation_before_writing():
    crm, conn, key = setup("create")
    assert review.approve(conn, crm, key, live=True).kind == review.NEEDS_CONFIRMATION
    assert crm.writes == [] and store.get(conn, key)["status"] == "pending"
    assert review.approve(conn, crm, key, live=True, confirmed=True).kind == review.APPLIED
    assert [w[0] for w in crm.writes] == ["POST"]


def test_live_retry_of_a_failed_proposal_needs_confirmation_too():
    crm, conn, key = setup("update")
    store.set_status(conn, key, "failed", "first try", {"error": "CrmError: timeout"})
    assert review.approve(conn, crm, key, live=True).kind == review.NEEDS_CONFIRMATION
    assert store.get(conn, key)["status"] == "failed" and crm.writes == []


def test_a_write_that_cannot_be_recorded_says_so(monkeypatch):
    crm, conn, key = setup("update")
    real = store.set_status

    def broken(conn, key, status, *args, **kwargs):
        if status == "applied":
            raise sqlite3.OperationalError("database is locked")
        return real(conn, key, status, *args, **kwargs)

    monkeypatch.setattr(store, "set_status", broken)
    outcome = review.approve(conn, crm, key, live=False)
    assert outcome.kind == review.UNRECORDED and "database is locked" in outcome.message
    assert outcome.result["updated"] == "A1"
    assert store.get(conn, key)["status"] == "applying"
    assert crm.accounts["A1"]["name"] == "Bellhaven of Chagrin Falls"


def test_reject_records_the_decision():
    crm, conn, key = setup("duplicate")
    assert review.reject(conn, key, "separate building").kind == review.REJECTED
    row = store.get(conn, key)
    assert (row["status"], row["comment"]) == ("rejected", "separate building")
    assert crm.writes == []


def test_reject_of_a_decided_proposal_is_already_handled():
    crm, conn, key = setup("update")
    review.approve(conn, crm, key, live=False)
    outcome = review.reject(conn, key)
    assert outcome.kind == review.ALREADY_HANDLED and "applied" in outcome.message
    assert store.get(conn, key)["status"] == "applied"
