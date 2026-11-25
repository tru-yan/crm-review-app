import pytest

from bellhaven.sop import must_preserve_old_account


@pytest.mark.parametrize("revenue, ar, expected", [
    (101000, 9000, True),   # history and money owed: keep the old account
    (47000, 0, False),      # history but nothing owed: re-parent directly
    (0, 500, False),        # owed but no history: re-parent directly
    (None, None, False),
    (100, -50, False),      # a credit is not "greater than zero"
    ("100", "5", True),
    ("n/a", 5, True),       # unreadable billing data: be conservative
    (float("nan"), 5, True),  # NaN is unreadable, not zero
    ("nan", 5, True),
    ("  ", 5, False),         # blank revenue means no history
])
def test_billing_sop(revenue, ar, expected):
    assert must_preserve_old_account({"lifetime_revenue": revenue, "outstanding_ar": ar}) is expected


def test_missing_billing_fields_mean_no_money():
    assert must_preserve_old_account({}) is False
