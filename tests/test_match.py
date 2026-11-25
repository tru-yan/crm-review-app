from bellhaven.match import MATCH_THRESHOLD, WEAK_THRESHOLD, assign_matches, score
from tests.factories import make_account, make_location


def test_same_address_written_differently_is_a_strong_match():
    m = score(make_location(street="199 Barks Rd W"), make_account(billing_street="199 Barks Road West"), [])
    assert "street" in m.signals and m.score >= MATCH_THRESHOLD


def test_rebranded_facility_matches_on_address_and_administrator():
    loc = make_location(name="Bellhaven of Chagrin Falls")
    acct = make_account(name="Riverbend Manor Care Center", phone="(517) 457-9548")
    m = score(loc, acct, [{"name": "Pat Admin"}])
    assert m.score >= MATCH_THRESHOLD and "administrator" in m.signals


def test_same_name_in_another_state_is_not_a_match():
    acct = make_account(name="Amberly Manor", billing_state="CO", billing_city="Colorado Springs")
    assert score(make_location(name="Amberly Manor"), acct, []).score == 0


def test_same_town_different_address_is_not_a_match():
    loc = make_location(name="Bellhaven at Union Square", street="118 Union Square Dr")
    acct = make_account(name="Union Square Senior Living", billing_street="240 Market St", phone="(260) 782-4620")
    assert score(loc, acct, [{"name": "Dale Croft"}]).score < WEAK_THRESHOLD


def test_different_house_number_is_not_the_same_street():
    m = score(make_location(street="102 Main Street"), make_account(billing_street="100 Main St"), [])
    assert "street" not in m.signals and "street_fuzzy" not in m.signals


def test_po_box_account_still_matches_on_phone_and_administrator():
    acct = make_account(billing_street="PO Box 517")
    assert score(make_location(), acct, [{"name": "Pat Admin"}]).score >= MATCH_THRESHOLD


def test_each_account_goes_to_its_best_location_only():
    exact = make_location(slug="/communities/exact")
    nearby = make_location(slug="/communities/nearby", name="Bellhaven Annex", phone="(614) 555-0199")
    result = assign_matches([nearby, exact], [make_account()], {}, skip_ids=set())
    assert [m.account["account_id"] for m in result["/communities/exact"]] == ["A1"]
    assert result["/communities/nearby"] == []
