"""Billing SOP for moving an account to a different parent."""


def amount(value) -> float:
    if value in (None, ""):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("inf")  # unreadable billing data: treat as money involved and preserve the account


def must_preserve_old_account(account: dict) -> bool:
    """True when the account has revenue history AND outstanding AR greater than zero.

    In that case the old account must stay exactly as it is: create a new account under the
    correct parent and set chow_current_account on the old one. Otherwise re-parent it directly.
    """
    return amount(account.get("lifetime_revenue")) > 0 and amount(account.get("outstanding_ar")) > 0
