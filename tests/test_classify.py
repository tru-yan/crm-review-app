from bellhaven.classify import build_proposals
from tests.factories import BH, CEDAR, HARBORVIEW, MILLSTONE, make_account, make_location, parent_accounts

TODAY = "2026-11-25"


def run(locations, accounts, contacts=()):
    return build_proposals(locations, parent_accounts() + accounts, list(contacts), TODAY)


def admin(account_id, name="Pat Admin"):
    return {"account_id": account_id, "name": name, "is_active": True}


def by_kind(proposals):
    out = {}
    for p in proposals:
        out.setdefault(p.kind, []).append(p)
    return out


def test_clean_match_produces_nothing():
    assert run([make_location()], [make_account()]) == []


def test_formatting_only_address_difference_produces_nothing():
    assert run([make_location(street="100 Main Street")], [make_account(billing_street="100 Main St.")]) == []


def test_renamed_facility_gets_name_update():
    [p] = run([make_location(name="Bellhaven of Chagrin Falls")],
              [make_account(name="Riverbend Manor Care Center")], [admin("A1")])
    assert p.kind == "update"
    assert p.changes["name"] == "Bellhaven of Chagrin Falls"
    assert p.before["name"] == "Riverbend Manor Care Center"
    assert p.changes["note"].startswith(f"[{TODAY}]")


def test_wrong_parent_without_outstanding_ar_is_reparented():
    [p] = run([make_location()], [make_account(parent_id=HARBORVIEW, lifetime_revenue=47000, outstanding_ar=0)])
    assert p.kind == "update" and p.changes["parent_id"] == BH


def test_wrong_parent_with_revenue_and_ar_uses_chow():
    [p] = run([make_location()], [make_account(parent_id=CEDAR, lifetime_revenue=84000, outstanding_ar=12400)])
    assert p.kind == "chow" and p.account_id == "A1"
    assert p.changes["new_account"]["parent_id"] == BH
    assert set(p.changes) == {"new_account"}  # nothing else on the old account changes


def test_already_under_bellhaven_with_ar_is_not_touched():
    assert run([make_location()], [make_account(lifetime_revenue=101000, outstanding_ar=9000)]) == []


def test_duplicates_keep_the_copy_with_the_administrator():
    accounts = [make_account(account_id="A1", phone="(330) 383-5991"), make_account(account_id="A2")]
    ps = by_kind(run([make_location()], accounts, [admin("A2")]))
    [dup] = ps["duplicate"]
    assert dup.account_id == "A1"
    assert dup.changes["duplicate_of_account"] == "A2" and dup.changes["status"] == "Inactive"
    assert "update" not in ps


def test_old_owner_copy_is_duplicate_of_bellhaven_copy():
    accounts = [make_account(account_id="A1"),
                make_account(account_id="A2", name="Harborview Shores of Testville", parent_id=HARBORVIEW)]
    [dup] = run([make_location()], accounts)
    assert dup.account_id == "A2" and dup.changes["duplicate_of_account"] == "A1"


def test_location_without_account_is_created_under_bellhaven():
    [p] = run([make_location(name="Bellhaven of Batavia", street="2000 Hospital Dr", city="Batavia", zip="45103")], [])
    assert p.kind == "create" and p.changes["parent_id"] == BH and p.changes["name"] == "Bellhaven of Batavia"


def test_same_name_in_other_state_is_not_matched():
    loc = make_location(name="Amberly Manor", street="4390 Darrow Rd", city="Hudson", zip="44236")
    other = make_account(name="Amberly Manor", parent_id=HARBORVIEW, billing_street="918 S Nevada Ave",
                         billing_city="Colorado Springs", billing_state="CO", billing_zip="80903")
    assert [p.kind for p in run([loc], [other])] == ["create"]


def test_bellhaven_account_missing_from_website_needs_review():
    [p] = run([], [make_account(name="Bellhaven of Coldwater")])
    assert p.kind == "not_on_website" and p.changes["status"] == "Needs Review"


def test_possibly_sold_facility_is_flagged_not_linked():
    # decision #2: same address under another operator is a clue, not proof, so nothing is linked automatically
    old = make_account(account_id="A1", name="Bellhaven of Sandusky", lifetime_revenue=130000, outstanding_ar=5200)
    new_owner = make_account(account_id="A2", name="Millstone Care of Sandusky", parent_id=MILLSTONE)
    [p] = run([], [old, new_owner])
    assert p.kind == "not_on_website" and p.changes["status"] == "Needs Review"
    assert "chow_current_account" not in p.changes and "duplicate_of_account" not in p.changes
    assert "Millstone Care of Sandusky" in p.changes["note"]


def test_resolved_accounts_are_left_alone():
    accounts = [make_account(account_id="A1", chow_current_account="NEW1", parent_id=CEDAR,
                             lifetime_revenue=1, outstanding_ar=1),
                make_account(account_id="NEW1", created_by_candidate=True)]
    assert run([make_location()], accounts) == []


def test_keys_do_not_depend_on_the_date():
    loc, acct = make_location(name="Bellhaven of New Name"), make_account()
    k1 = build_proposals([loc], parent_accounts() + [acct], [], "2026-11-25")[0].key
    k2 = build_proposals([loc], parent_accounts() + [acct], [], "2026-11-26")[0].key
    assert k1 == k2


def test_reparenting_rechecks_billing_before_writing():
    [p] = run([make_location()], [make_account(parent_id=HARBORVIEW, lifetime_revenue=47000, outstanding_ar=0)])
    assert p.before["lifetime_revenue"] == 47000 and p.before["outstanding_ar"] == 0


def test_chow_with_a_bellhaven_copy_is_flagged_for_joint_review():
    accounts = [make_account(account_id="A1", parent_id=CEDAR, lifetime_revenue=84000, outstanding_ar=12400),
                make_account(account_id="A2")]
    ps = run([make_location()], accounts, [admin("A2")])
    assert sorted(p.kind for p in ps) == ["chow", "duplicate"]
    assert all(p.confidence == "low" for p in ps)


def test_weak_match_next_to_a_strong_one_is_not_hidden():
    other = make_account(account_id="A2", name="Gardens Senior Care", billing_street="900 Other Rd",
                         phone="(614) 555-0900")
    [p] = run([make_location()], [make_account(), other], [admin("A2")])
    assert p.kind == "not_on_website" and p.account_id == "A2"


def test_unlinked_chow_account_is_not_proposed_as_a_duplicate():
    # a CHOW whose link failed: its new account exists, but the old one isn't linked to it yet
    old = make_account(account_id="A1", parent_id=CEDAR, lifetime_revenue=84000, outstanding_ar=12400)
    new = make_account(account_id="NEW1", created_by_candidate=True)
    assert [p.kind for p in run([make_location()], [old, new])] == ["chow"]


def test_duplicate_evidence_names_the_survivor():
    accounts = [make_account(account_id="A1"),
                make_account(account_id="A2", name="Harborview Shores of Testville", parent_id=HARBORVIEW)]
    [dup] = run([make_location()], accounts)
    assert dup.evidence["survivor"] == {"account_id": "A1", "name": "Bellhaven of Testville"}
