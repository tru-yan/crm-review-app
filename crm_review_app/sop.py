"""Billing SOP for moving an account to a different parent."""

import math


def amount(value) -> float:
    """Parse a billing amount. Blank means 0; anything unreadable (including NaN) means 'money involved'."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return 0.0
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float("inf")  # unreadable billing data: treat as money involved and preserve the account
    return float("inf") if math.isnan(number) else number


def must_preserve_old_account(account: dict) -> bool:
    """True when the account has revenue history AND outstanding AR greater than zero.

    In that case the old account must stay exactly as it is: create a new account under the
    correct parent and set chow_current_account on the old one. Otherwise re-parent it directly.
    """
    return amount(account.get("lifetime_revenue")) > 0 and amount(account.get("outstanding_ar")) > 0
