import pytest

from bellhaven.normalize import crm_care_types, is_po_box, norm_name, norm_phone, norm_street, same_text, zip5


@pytest.mark.parametrize("a, b", [
    ("4850 Northwest Sylvania Avenue", "4850 NW Sylvania Ave"),
    ("199 Barks Road West", "199 Barks Rd W"),
    ("3313 Wilmington Pk", "3313 Wilmington Pike"),
    ("980 West Michigan Avenue", "980 W Michigan Ave"),
    ("100 Main St Suite 4", "100 Main Street"),
    ("100 Main St, Ste. 200", "100 Main St"),
])
def test_street_variants_normalize_equal(a, b):
    assert norm_street(a) == norm_street(b)


def test_street_name_starting_with_ste_is_kept():
    # regression: a prefix match on "ste" once turned "750 Stewart Rd" into "750 Rd"
    assert norm_street("750 Stewart Rd") == "750 stewart rd"


def test_different_streets_stay_different():
    assert norm_street("118 Union Square Dr") != norm_street("240 Market St")


def test_po_box():
    assert is_po_box("PO Box 517")
    assert is_po_box("P.O. Box 5")
    assert not is_po_box("100 Box Elder Rd")


def test_zip5():
    assert zip5("45662-1234") == "45662"
    assert zip5("") == ""


def test_phone():
    assert norm_phone("(614) 250-9447") == "6142509447"
    assert norm_phone("1-614-250-9447") == "6142509447"
    assert norm_phone("") == ""


def test_name_normalization_for_similarity():
    assert norm_name("Bellhaven Rehab and Nursing of Grove City") == norm_name("Bellhaven Rehabilitation & Nursing of Grove City")
    assert norm_name("Bellhaven Health Care Center of Ashland") == norm_name("Bellhaven Healthcare Centre of Ashland")


def test_same_text_ignores_only_case_and_spacing():
    assert same_text(" Bellhaven  of Owosso", "bellhaven of owosso")
    assert not same_text("Bellhaven of Sycamore Ridge", "Bellhaven at Sycamore Ridge")


def test_care_type_map():
    assert crm_care_types(("Short-Term Rehabilitation & Nursing", "Memory Support")) == ["Skilled Nursing", "Memory Care"]
    assert crm_care_types(("Hospice",)) == ["Hospice"]
