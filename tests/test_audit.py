from bellhaven.audit import audit, changes_since
from tests.factories import CEDAR, make_account, make_location, parent_accounts

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
