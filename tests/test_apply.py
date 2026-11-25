import pytest

from bellhaven import store
from bellhaven.apply import StaleProposal, apply_proposal
from bellhaven.classify import build_proposals
from tests.factories import BH, CEDAR, FakeCrm, make_account, make_location, parent_accounts


def setup(accounts, locations):
    crm = FakeCrm(parent_accounts() + accounts)
    [p] = build_proposals(locations, list(crm.accounts.values()), [], "2026-11-25")
    conn = store.connect(":memory:")
    store.add_proposals(conn, store.start_run(conn), [p])
    return crm, conn, store.get(conn, p.key)


def test_update_is_written_and_logged():
    crm, conn, row = setup([make_account(name="Old Name")], [make_location()])
    result = apply_proposal(conn, crm, row)
    assert crm.accounts["A1"]["name"] == "Bellhaven of Testville"
    assert result["mismatches"] == {}
    assert conn.execute("SELECT COUNT(*) FROM api_log").fetchone()[0] == 1


def test_stale_proposal_writes_nothing():
    crm, conn, row = setup([make_account(name="Old Name")], [make_location()])
    crm.accounts["A1"]["name"] = "Someone Else Renamed It"
    with pytest.raises(StaleProposal):
        apply_proposal(conn, crm, row)
    assert crm.writes == []


def test_chow_creates_new_account_and_only_links_the_old_one():
    crm, conn, row = setup([make_account(parent_id=CEDAR, lifetime_revenue=84000, outstanding_ar=12400)],
                           [make_location()])
    old_before = dict(crm.accounts["A1"])
    result = apply_proposal(conn, crm, row)
    new, old = crm.accounts[result["created"]], crm.accounts["A1"]
    assert new["parent_id"] == BH
    assert old["chow_current_account"] == new["account_id"]
    unchanged = lambda acct: {k: v for k, v in acct.items() if k != "chow_current_account"}
    assert unchanged(old) == unchanged(old_before)


def test_create_is_not_repeated_after_a_retry():
    crm, conn, row = setup([], [make_location(name="Bellhaven of Batavia", zip="45103")])
    apply_proposal(conn, crm, row)
    apply_proposal(conn, crm, row)
    assert [w[0] for w in crm.writes] == ["POST"]
    assert conn.execute("SELECT COUNT(DISTINCT api_call_id) FROM change_log").fetchone()[0] == 1


def test_update_is_recorded_field_by_field():
    crm, conn, row = setup([make_account(name="Old Name")], [make_location()])
    apply_proposal(conn, crm, row)
    history = store.change_history(conn, account_id="A1")
    assert ("name", "Old Name", "Bellhaven of Testville") in {(h["field"], h["old_value"], h["new_value"]) for h in history}
    assert {h["action"] for h in history} == {"update"}


def test_chow_logs_the_new_account_and_the_link():
    crm, conn, row = setup([make_account(parent_id=CEDAR, lifetime_revenue=84000, outstanding_ar=12400)],
                           [make_location()])
    result = apply_proposal(conn, crm, row)
    [link] = store.change_history(conn, account_id="A1")  # the old account: only the link changed
    assert (link["field"], link["old_value"], link["new_value"]) == ("chow_current_account", "", result["created"])
    created_fields = {h["field"] for h in store.change_history(conn, account_id=result["created"])}
    assert {"name", "parent_id", "billing_street"} <= created_fields
