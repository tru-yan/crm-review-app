"""Decide whether a CRM account is the same facility as a website location, by named match rules, and rank the matches.

The address carries a match: facilities get renamed and sold but don't move, and phone numbers in this CRM are
often stale. A name alone never makes a match, and a different state rules one out.
"""
from dataclasses import dataclass

from rapidfuzz import fuzz

from .models import Location
from .normalize import norm_name, norm_phone, norm_street, zip5

SIGNALS = ("street", "near_street", "administrator", "phone", "name", "similar_name", "zip", "city")  # display order
SUPPORT = ("administrator", "phone", "name", "similar_name", "zip", "city")  # ranks matches that meet the same rule


def _any(signals, *names) -> bool:
    return any(n in signals for n in names)


# A pair that meets one of these is the same facility. Strongest first: that order is the first part of the rank.
MATCH_RULES = (
    ("same address", lambda s: "street" in s and _any(s, "zip", "city")),
    ("near address", lambda s: "near_street" in s and _any(s, "administrator", "phone")),
    ("same people", lambda s: "administrator" in s and "phone" in s and _any(s, "zip", "city")),
)
# A pair that meets one of these is only a possible match: shown to the reviewer on a create, never acted on.
POSSIBLE_RULES = (
    ("street only", lambda s: _any(s, "street", "near_street")),
    ("administrator or phone nearby", lambda s: _any(s, "administrator", "phone") and _any(s, "zip", "city")),
)


@dataclass
class Match:
    account: dict
    signals: tuple[str, ...] = ()   # the evidence that agrees, in SIGNALS order
    rule: str = ""                  # the match or possible-match rule it meets; "" for neither
    strong: bool = False            # True when it meets a match rule: the same facility
    rank: tuple = (2, 0, 0)         # sort key, smallest first: match rules, then the rule's order, then more support


def _signals(loc: Location, account: dict, contacts: list[dict]) -> tuple[str, ...]:
    found = set()
    same_city = (account.get("billing_city") or "").strip().lower() == loc.city.strip().lower()
    a_street, l_street = norm_street(account.get("billing_street")), norm_street(loc.street)
    if a_street and a_street == l_street:
        found.add("street")
    elif (a_street and l_street and same_city and a_street.split()[0] == l_street.split()[0]
          and fuzz.ratio(a_street, l_street) >= 90):
        found.add("near_street")   # a typo or formatting difference: same city, same house number
    if zip5(account.get("billing_zip")) and zip5(account.get("billing_zip")) == zip5(loc.zip):
        found.add("zip")
    if same_city:
        found.add("city")
    phone = norm_phone(account.get("phone"))
    if phone and phone == norm_phone(loc.phone):
        found.add("phone")
    admin = loc.administrator.strip().lower()
    if admin and any((c.get("name") or "").strip().lower() == admin for c in contacts):
        found.add("administrator")
    name = fuzz.token_sort_ratio(norm_name(account.get("name")), norm_name(loc.name))
    if name >= 90:
        found.add("name")
    elif name >= 75:
        found.add("similar_name")
    return tuple(s for s in SIGNALS if s in found)


def evaluate(loc: Location, account: dict, contacts: list[dict]) -> Match:
    """Which rule, if any, this account meets for this location, and how it ranks."""
    if (account.get("billing_state") or "").strip().upper() != loc.state.upper():
        return Match(account)
    signals = _signals(loc, account, contacts)
    support = sum(s in signals for s in SUPPORT)
    for strong, rules in ((True, MATCH_RULES), (False, POSSIBLE_RULES)):
        for i, (rule, meets) in enumerate(rules):
            if meets(signals):
                return Match(account, signals, rule, strong, (0 if strong else 1, i, -support))
    return Match(account, signals)


def assign_matches(locations, accounts, contacts_by_account, skip_ids) -> dict[str, list[Match]]:
    """Give each account to at most one location (where it ranks best). Returns slug -> matches, best first."""
    pairs = []
    for loc in locations:
        for a in accounts:
            if a["account_id"] in skip_ids:
                continue
            m = evaluate(loc, a, contacts_by_account.get(a["account_id"], []))
            if m.rule:
                pairs.append((m.rank, loc.slug, m))
    pairs.sort(key=lambda p: (p[0], p[1], p[2].account["account_id"]))
    taken, result = set(), {loc.slug: [] for loc in locations}
    for _, slug, m in pairs:
        if m.account["account_id"] not in taken:
            taken.add(m.account["account_id"])
            result[slug].append(m)
    return result
