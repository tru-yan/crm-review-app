import json

from bellhaven import audit as audit_module
from bellhaven.audit import audit, changes_since
from tests.factories import CEDAR, HARBORVIEW, make_account, make_location, parent_accounts

TODAY = "2026-11-25"


def test_clean_crm_has_no_problems():
    accounts = parent_accounts() + [make_account()]
    assert audit({"accounts": accounts}, accounts, [], [make_location()], TODAY) == []


def test_reports_what_the_pipeline_would_still_propose():
    accounts = parent_accounts() + [make_account(name="Old Name")]
    [problem] = audit({"accounts": accounts}, accounts, [], [make_location()], TODAY)
    assert problem.startswith("still proposed: update")


def test_catches_a_broken_billing_sop():
    before = parent_accounts() + [make_account(parent_id=CEDAR, lifetime_revenue=84000, outstanding_ar=12400)]
    after = parent_accounts() + [make_account(lifetime_revenue=84000, outstanding_ar=12400)]  # moved directly
    problems = audit({"accounts": before}, after, [], [make_location()], TODAY)
    assert any("billing SOP broken" in p for p in problems)


def test_catches_a_duplicate_pointing_nowhere():
    accounts = parent_accounts() + [make_account(), make_account(account_id="A2", name="Copy", billing_street="9 Other St",
                                                                 duplicate_of_account="GONE", status="Inactive")]
    problems = audit({"accounts": accounts}, accounts, [], [make_location()], TODAY)
    assert any("missing account GONE" in p for p in problems)


def test_counts_created_and_changed_accounts():
    before = {"accounts": [make_account()]}
    after = [make_account(name="New Name"), make_account(account_id="NEW1")]
    assert changes_since(before, after) == {"created": 1, "changed": 1}


def test_chow_old_account_must_stay_exactly_as_it_was():
    old = make_account(parent_id=CEDAR, lifetime_revenue=84000, outstanding_ar=12400)
    after = dict(old, chow_current_account="NEW1", billing_street="1 Other St")
    new = make_account(account_id="NEW1", created_by_candidate=True)
    problems = audit({"accounts": parent_accounts() + [old]}, parent_accounts() + [after, new], [], [make_location()],
                     TODAY)
    assert problems == ["billing SOP broken: 'Bellhaven of Testville' was kept for billing after a change of "
                        "ownership but was also edited: billing_street"]


def test_links_must_point_to_live_accounts():
    copy_a = make_account(account_id="A2", name="Copy", billing_street="9 Other St", duplicate_of_account="A3",
                          status="Inactive")
    copy_b = make_account(account_id="A3", name="Also Copy", billing_street="8 Other St", duplicate_of_account="A1",
                          status="Inactive")
    accounts = parent_accounts() + [make_account(), copy_a, copy_b]
    problems = audit({"accounts": accounts}, accounts, [], [make_location()], TODAY)
    assert problems == ["'Copy': duplicate_of_account points to A3, which isn't a live account"]


def test_flags_changes_this_tool_never_makes():
    elsewhere = make_account(account_id="A2", name="Elsewhere", billing_street="5 Far Rd", billing_city="Far",
                             parent_id=HARBORVIEW)
    before = parent_accounts() + [make_account(), elsewhere]
    after = parent_accounts() + [make_account(), dict(elsewhere, phone="(000) 000-0000", status="Inactive")]
    problems = audit({"accounts": before}, after, [], [make_location()], TODAY)
    assert "'Elsewhere': fields this tool never changes were changed: phone" in problems
    assert "'Elsewhere': set to Inactive without being marked a duplicate" in problems


def test_flags_an_account_created_twice():
    twins = [make_account(account_id=f"NEW{n}", created_by_candidate=True, name="Bellhaven of Batavia",
                          billing_street="2000 Hospital Dr", billing_city="Batavia", billing_zip="45103") for n in (1, 2)]
    accounts = parent_accounts() + [make_account()] + twins
    problems = audit({"accounts": parent_accounts() + [make_account()]}, accounts, [], [make_location()], TODAY)
    assert "created more than once: 'Bellhaven of Batavia' at 2000 Hospital Dr (2 accounts)" in problems


def test_wrong_snapshot_is_reported_once():
    problems = audit({"accounts": [make_account(account_id="ZZZ")]}, parent_accounts() + [make_account()], [],
                     [make_location()], TODAY)
    assert problems == ["the before-snapshot shares no accounts with this CRM: wrong snapshot, or wrong CRM?"]


def test_main_only_reads_and_returns_the_exit_code(tmp_path, monkeypatch):
    accounts = parent_accounts() + [make_account()]

    class ReadOnly:
        def __init__(self, *args, **kwargs):
            pass

        def list_accounts(self, **filters):
            return [dict(a) for a in accounts]

        def list_contacts(self, **filters):
            return []

        def create_account(self, body):
            raise AssertionError("the audit must never write")

        def update_account(self, account_id, body):
            raise AssertionError("the audit must never write")

    before = tmp_path / "crm.json"
    before.write_text(json.dumps({"accounts": accounts, "contacts": []}), encoding="utf-8")
    monkeypatch.setattr(audit_module, "CrmClient", ReadOnly)
    monkeypatch.setattr(audit_module, "scrape", lambda: [make_location()])
    assert audit_module.main(["--before", str(before)]) == 0
    monkeypatch.setattr(audit_module, "scrape", lambda: [make_location(name="Renamed on the website")])
    assert audit_module.main(["--before", str(before)]) == 1
