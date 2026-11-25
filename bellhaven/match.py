"""Score how well a CRM account matches a website location.

Address and administrator carry the most weight: names change when facilities are rebranded,
and phone numbers in this CRM are often stale. A different state rules a match out.
"""
from dataclasses import dataclass, field

from rapidfuzz import fuzz

from .models import Location
from .normalize import norm_name, norm_phone, norm_street, zip5

MATCH_THRESHOLD = 60   # at or above: the same facility
WEAK_THRESHOLD = 40    # in between: shown to the reviewer as a possible match, never acted on
WEIGHTS = {"street": 50, "street_fuzzy": 35, "administrator": 25, "phone": 15,
           "name": 15, "name_partial": 8, "zip": 10, "city": 10}


@dataclass
class Match:
    account: dict
    score: int
    signals: dict[str, int] = field(default_factory=dict)


def score(loc: Location, account: dict, contacts: list[dict]) -> Match:
    if (account.get("billing_state") or "").strip().upper() != loc.state.upper():
        return Match(account, 0, {})
    signals = {}
    same_city = (account.get("billing_city") or "").strip().lower() == loc.city.strip().lower()
    a_street, l_street = norm_street(account.get("billing_street")), norm_street(loc.street)
    if a_street and a_street == l_street:
        signals["street"] = WEIGHTS["street"]
    elif (a_street and l_street and same_city and a_street.split()[0] == l_street.split()[0]
          and fuzz.ratio(a_street, l_street) >= 90):
        signals["street_fuzzy"] = WEIGHTS["street_fuzzy"]
    if zip5(account.get("billing_zip")) and zip5(account.get("billing_zip")) == zip5(loc.zip):
        signals["zip"] = WEIGHTS["zip"]
    if same_city:
        signals["city"] = WEIGHTS["city"]
    phone = norm_phone(account.get("phone"))
    if phone and phone == norm_phone(loc.phone):
        signals["phone"] = WEIGHTS["phone"]
    admin = loc.administrator.strip().lower()
    if admin and any((c.get("name") or "").strip().lower() == admin for c in contacts):
        signals["administrator"] = WEIGHTS["administrator"]
    name_score = fuzz.token_sort_ratio(norm_name(account.get("name")), norm_name(loc.name))
    if name_score >= 90:
        signals["name"] = WEIGHTS["name"]
    elif name_score >= 75:
        signals["name_partial"] = WEIGHTS["name_partial"]
    return Match(account, sum(signals.values()), signals)


def assign_matches(locations, accounts, contacts_by_account, skip_ids) -> dict[str, list[Match]]:
    """Give each account to at most one location (its best). Returns slug -> matches, best first."""
    pairs = []
    for loc in locations:
        for a in accounts:
            if a["account_id"] in skip_ids:
                continue
            m = score(loc, a, contacts_by_account.get(a["account_id"], []))
            if m.score >= WEAK_THRESHOLD:
                pairs.append((m.score, loc.slug, m))
    pairs.sort(key=lambda p: (-p[0], p[1], p[2].account["account_id"]))
    taken, result = set(), {loc.slug: [] for loc in locations}
    for _, slug, m in pairs:
        if m.account["account_id"] not in taken:
            taken.add(m.account["account_id"])
            result[slug].append(m)
    return result
