import json

import pytest

from bellhaven import store
from bellhaven.apply import AlreadyHandled, StaleProposal, apply_proposal
from bellhaven.classify import build_proposals
from bellhaven.crm import CrmError
from tests.factories import BH, CEDAR, FakeCrm, make_account, make_location, parent_accounts


def setup_with(crm, locations):
    [p] = build_proposals(locations, list(crm.accounts.values()), [], "2026-11-25")
    conn = store.connect(":memory:")
    store.add_proposals(conn, store.start_run(conn), [p])
    return conn, store.get(conn, p.key)


def setup(accounts, locations):
    crm = FakeCrm(parent_accounts() + accounts)
    conn, row = setup_with(crm, locations)
    return crm, conn, row


def chow_account():
    return make_account(parent_id=CEDAR, lifetime_revenue=84000, outstanding_ar=12400)


def test_update_is_written_and_logged():
    crm, conn, row = setup([make_account(name="Old Name")], [make_location()])
    result = apply_proposal(conn, crm, row)
    assert crm.accounts["A1"]["name"] == "Bellhaven of Testville"
    assert result["mismatches"] == {}
    assert conn.execute("SELECT COUNT(*) FROM api_log").fetchone()[0] == 1


def test_stale_proposal_writes_nothing():
    crm, conn, row = setup([make_account(name="Old Name")], [make_location()])
    crm.accounts["A1"]["name"] = "Someone Else Renamed It"
    with pytest.raises(StaleProposal, match="Someone Else Renamed It"):
        apply_proposal(conn, crm, row)
    assert crm.writes == []


def test_chow_creates_new_account_and_only_links_the_old_one():
    crm, conn, row = setup([chow_account()], [make_location()])
    old_before = dict(crm.accounts["A1"])
    result = apply_proposal(conn, crm, row)
    new, old = crm.accounts[result["created"]], crm.accounts["A1"]
    assert new["parent_id"] == BH
    assert old["chow_current_account"] == new["account_id"]
    unchanged = lambda acct: {k: v for k, v in acct.items() if k != "chow_current_account"}
    assert unchanged(old) == unchanged(old_before)
    assert result["mismatches"] == {"new_account": {}, "link": {}}


def test_create_is_not_repeated_after_a_retry():
    crm, conn, row = setup([], [make_location(name="Bellhaven of Batavia", zip="45103")])
    apply_proposal(conn, crm, row)
    store.set_status(conn, row["key"], "failed")  # say the app lost track and the reviewer presses Retry
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
    crm, conn, row = setup([chow_account()], [make_location()])
    result = apply_proposal(conn, crm, row)
    [link] = store.change_history(conn, account_id="A1")  # the old account: only the link changed
    assert (link["field"], link["old_value"], link["new_value"]) == ("chow_current_account", "", result["created"])
    created_fields = {h["field"] for h in store.change_history(conn, account_id=result["created"])}
    assert {"name", "parent_id", "billing_street"} <= created_fields


def test_a_proposal_cannot_be_applied_twice_at_once():
    crm, conn, row = setup([make_account(name="Old Name")], [make_location()])
    apply_proposal(conn, crm, row)  # status is now 'applying'; the app marks it applied afterwards
    with pytest.raises(AlreadyHandled, match="applying"):
        apply_proposal(conn, crm, row)  # a second click
    assert [w[0] for w in crm.writes] == ["PATCH"]


def test_rejected_proposal_is_never_written():
    crm, conn, row = setup([make_account(name="Old Name")], [make_location()])
    store.set_status(conn, row["key"], "rejected")
    with pytest.raises(AlreadyHandled):
        apply_proposal(conn, crm, row)
    assert crm.writes == []


def test_chow_is_refused_if_the_billing_rule_no_longer_applies():
    crm, conn, row = setup([chow_account()], [make_location()])
    crm.accounts["A1"]["outstanding_ar"] = 0  # paid down after the proposal was made
    with pytest.raises(StaleProposal, match="billing"):
        apply_proposal(conn, crm, row)
    assert crm.writes == []


def test_update_is_refused_if_the_account_was_resolved_meanwhile():
    crm, conn, row = setup([make_account(name="Old Name")], [make_location()])
    crm.accounts["A1"]["duplicate_of_account"] = "A9"  # someone marked it a duplicate in the CRM
    with pytest.raises(StaleProposal, match="duplicate"):
        apply_proposal(conn, crm, row)
    assert crm.writes == []


def test_chow_crash_between_create_and_link_recovers_on_retry():
    class LinkFailsOnce(FakeCrm):
        failed = False

        def update_account(self, account_id, body):
            if not self.failed:
                self.failed = True
                raise CrmError("PATCH timed out")
            return super().update_account(account_id, body)

    crm = LinkFailsOnce(parent_accounts() + [chow_account()])
    conn, row = setup_with(crm, [make_location()])
    with pytest.raises(CrmError, match="created NEW1"):
        apply_proposal(conn, crm, row)
    store.set_status(conn, row["key"], "failed")
    result = apply_proposal(conn, crm, row)
    assert result["created"] == "NEW1" and crm.accounts["A1"]["chow_current_account"] == "NEW1"
    assert [w[0] for w in crm.writes] == ["POST", "PATCH"]
    assert "timed out" in conn.execute("SELECT response FROM api_log WHERE method='PATCH' ORDER BY id").fetchone()[0]


def test_reuse_relies_on_our_own_log_even_if_the_api_stored_fields_differently():
    crm, conn, row = setup([], [make_location(name="Bellhaven of Batavia", zip="45103")])
    apply_proposal(conn, crm, row)
    crm.accounts["NEW1"]["parent_id"] = ""  # the API stored the parent differently from what was sent
    store.set_status(conn, row["key"], "failed")
    result = apply_proposal(conn, crm, row)
    assert [w[0] for w in crm.writes] == ["POST"]
    assert result["mismatches"]["parent_id"] == {"sent": BH, "returned": ""}


def test_same_name_in_the_same_zip_at_another_street_is_not_reused():
    crm, conn, row = setup([], [make_location(name="Bellhaven of Batavia", zip="45103")])
    crm.create_account({"name": "Bellhaven of Batavia", "parent_id": BH, "billing_street": "1 Other St",
                        "billing_zip": "45103"})
    apply_proposal(conn, crm, row)
    assert [w[0] for w in crm.writes] == ["POST", "POST"]


def test_lost_response_is_recognised_as_already_applied():
    crm, conn, row = setup([make_account(name="Old Name")], [make_location()])
    crm.accounts["A1"].update(json.loads(row["changes"]))  # the PATCH landed but its response never arrived
    result = apply_proposal(conn, crm, row)
    assert result["already_applied"] is True and crm.writes == []
    assert {h["field"] for h in store.change_history(conn, account_id="A1")} >= {"name"}


def test_a_dropped_field_in_a_created_chow_account_is_reported():
    class DropsParent(FakeCrm):
        def create_account(self, body):
            return super().create_account({**body, "parent_id": ""})

    crm = DropsParent(parent_accounts() + [chow_account()])
    conn, row = setup_with(crm, [make_location()])
    result = apply_proposal(conn, crm, row)
    assert result["mismatches"]["new_account"]["parent_id"] == {"sent": BH, "returned": ""}


def test_chow_is_refused_if_the_account_was_resolved_meanwhile():
    crm, conn, row = setup([chow_account()], [make_location()])
    crm.accounts["A1"]["duplicate_of_account"] = "A9"  # marked a duplicate in the CRM after the proposal was made
    with pytest.raises(StaleProposal, match="duplicate"):
        apply_proposal(conn, crm, row)
    assert crm.writes == []


def test_apply_uses_the_latest_stored_proposal_not_the_one_the_page_loaded():
    crm, conn, row = setup([make_account(name="Old Name")], [make_location()])
    crm.accounts["A1"]["note"] = "edited by a rep"  # the CRM changed after the page was loaded...
    [p] = build_proposals([make_location()], list(crm.accounts.values()), [], "2026-11-26")
    store.add_proposals(conn, store.start_run(conn), [p])  # ...and the next run refreshed the proposal
    apply_proposal(conn, crm, row)  # the page still holds the old row
    assert crm.accounts["A1"]["note"].startswith("edited by a rep | ")


def test_duplicate_is_refused_if_its_survivor_is_no_longer_live():
    crm = FakeCrm(parent_accounts() + [make_account(account_id="A1", phone="(330) 383-5991"), make_account(account_id="A2")])
    conn, row = setup_with(crm, [make_location()])
    assert row["kind"] == "duplicate" and json.loads(row["changes"])["duplicate_of_account"] == "A2"
    crm.accounts["A2"]["status"] = "Inactive"  # the survivor was retired meanwhile
    with pytest.raises(StaleProposal, match="survivor"):
        apply_proposal(conn, crm, row)
    assert crm.writes == []


def test_a_create_the_pipeline_no_longer_proposes_is_never_posted():
    class RejectsOnce(FakeCrm):
        rejected = False

        def create_account(self, body):
            if not self.rejected:
                self.rejected = True
                raise CrmError("POST /accounts -> 422: bad body")
            return super().create_account(body)

    crm = RejectsOnce(parent_accounts())
    conn, row = setup_with(crm, [make_location(name="Bellhaven of Batavia", zip="45103")])
    store.finish_run(conn, row["run_id"], {})
    with pytest.raises(CrmError):
        apply_proposal(conn, crm, row)
    store.set_status(conn, row["key"], "failed")
    later = store.start_run(conn)  # the community left the website: the next run doesn't propose it any more
    store.add_proposals(conn, later, [])
    store.supersede_missing(conn, [])
    store.finish_run(conn, later, {})
    assert store.get(conn, row["key"])["status"] == "failed"  # kept, because a write attempt was logged
    with pytest.raises(StaleProposal, match="latest pipeline run"):
        apply_proposal(conn, crm, row)
    assert crm.writes == []
