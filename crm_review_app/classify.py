"""Turn website locations and CRM accounts into proposals for a reviewer. Pure: no network, no database."""
import hashlib
import json
from dataclasses import asdict, dataclass

from .config import BELLHAVEN_PARENT_NAME, NOT_ON_WEBSITE_STATUS
from .match import Match, assign_matches
from .models import Location
from .normalize import (care_type_set, crm_care_types, is_po_box, norm_phone, norm_street, same_care_types, same_text,
                        zip5)
from .sop import amount, must_preserve_old_account

CRM_FIELDS = ("account_id", "name", "parent_id", "parent_name", "billing_street", "billing_city",
              "billing_state", "billing_zip", "care_type", "status", "phone", "lifetime_revenue",
              "outstanding_ar", "chow_current_account", "duplicate_of_account", "note")


@dataclass
class Proposal:
    kind: str          # update | chow | duplicate | create | not_on_website
    title: str
    account_id: str    # account to change; "" for create
    slug: str          # website community it came from; "" when the account is not on the website
    changes: dict      # an update's ADMINISTRATOR entry changes contacts; every other entry is an account field
    before: dict       # current CRM values of the changed account fields; checked again just before writing
    evidence: dict
    confidence: str = "high"

    @property
    def key(self) -> str:
        """Same decision -> same key on any day. Notes contain the date, so they are left out."""
        stable = {k: v for k, v in self.changes.items() if k != "note"}
        if isinstance(stable.get("new_account"), dict):
            stable["new_account"] = {k: v for k, v in stable["new_account"].items() if k != "note"}
        raw = json.dumps([self.kind, self.account_id, self.slug, stable], sort_keys=True)
        return hashlib.sha256(raw.encode()).hexdigest()[:20]


def parent_ids(accounts) -> set[str]:
    referenced = {a["parent_id"] for a in accounts if a.get("parent_id")}
    return referenced | {a["account_id"] for a in accounts if a["name"].endswith("(Parent Account)")}


def bellhaven_parent_id(accounts) -> str:
    ids = [a["account_id"] for a in accounts if a["name"] == BELLHAVEN_PARENT_NAME]
    if len(ids) != 1:
        raise ValueError(f"expected exactly one {BELLHAVEN_PARENT_NAME!r}, found {len(ids)}")
    return ids[0]


def already_resolved(account) -> bool:
    """Marked as a duplicate, or kept for billing after a change of ownership: leave it alone."""
    return bool(account.get("duplicate_of_account") or account.get("chow_current_account"))


def _note(existing: str, text: str, today: str, url: str = "") -> str:
    entry = f"[{today}] {text}" + (f" Source: {url}" if url else "")
    return f"{existing} | {entry}" if existing else entry


def _crm_view(account) -> dict:
    return {f: account.get(f) for f in CRM_FIELDS}


def _evidence(loc: Location | None, m: Match | None, account: dict, reasons: list[str]) -> dict:
    ev = {"crm": _crm_view(account), "reasons": list(reasons),
          "sop": {"lifetime_revenue": account.get("lifetime_revenue"),
                  "outstanding_ar": account.get("outstanding_ar"),
                  "preserve_old_account": must_preserve_old_account(account)}}
    if loc:
        ev["website"] = asdict(loc)
        if not loc.in_directory:
            ev["reasons"].append("Linked from the homepage but missing from the community directory.")
    if m:
        ev["match"] = {"rule": m.rule, "signals": list(m.signals)}
    return ev


CARE_TYPE_SEPARATOR = "; "  # several care types in the one CRM field, the way a Salesforce multi-select stores them


def _care_type(loc: Location) -> str:
    """The CRM care_type for a community: every care offering it lists, in the CRM's names (decision #4)."""
    return CARE_TYPE_SEPARATOR.join(crm_care_types(loc.care_offerings))


def _new_account(loc: Location, parent_id: str, note: str) -> dict:
    return {"name": loc.name, "parent_id": parent_id, "billing_street": loc.street, "billing_city": loc.city,
            "billing_state": loc.state, "billing_zip": loc.zip, "phone": loc.phone,
            "care_type": _care_type(loc), "status": "Active", "note": note}


def _field_changes(loc: Location, a: dict) -> tuple[dict, list[str]]:
    """The website is the source of truth. Address formatting differences (Rd vs Road) are not changes."""
    changes, reasons = {}, []
    if not same_text(a["name"], loc.name):
        changes["name"] = loc.name
        reasons.append(f"Name differs: CRM has '{a['name']}', website has '{loc.name}'.")
    if norm_street(a["billing_street"]) != norm_street(loc.street):
        changes["billing_street"] = loc.street
        crm_street = "a PO Box" if is_po_box(a["billing_street"]) else f"'{a['billing_street']}'"
        reasons.append(f"Street differs: CRM has {crm_street}, website has '{loc.street}'.")
    if not same_text(a["billing_city"], loc.city):
        changes["billing_city"] = loc.city
        reasons.append(f"City differs: CRM has '{a['billing_city']}', website has '{loc.city}'.")
    if zip5(a["billing_zip"]) != zip5(loc.zip):
        changes["billing_zip"] = loc.zip
        reasons.append(f"Zip differs: CRM has '{a['billing_zip']}', website has '{loc.zip}'.")
    if loc.phone and norm_phone(a["phone"]) != norm_phone(loc.phone):  # a phone missing from the website stays
        changes["phone"] = loc.phone
        reasons.append(f"Phone differs: CRM has '{a['phone'] or 'blank'}', website has '{loc.phone}'.")
    if loc.care_offerings and not same_care_types(a["care_type"], loc.care_offerings):
        changes["care_type"] = _care_type(loc)
        reasons.append(f"Care type: CRM has '{a['care_type'] or 'blank'}', website lists {', '.join(loc.care_offerings)}.")
    if a["status"] != "Active":
        changes["status"] = "Active"
        reasons.append(f"Status is '{a['status']}' but the community is listed on the website.")
    return changes, reasons


def choose_survivor(strong: list[Match], bh: str) -> tuple[Match, str]:
    def rank(m):
        a = m.account
        return (amount(a.get("lifetime_revenue")) > 0, a["parent_id"] == bh, "administrator" in m.signals,
                "phone" in m.signals, a["status"] == "Active", tuple(-x for x in m.rank))  # last: closest match

    best = max(sorted(strong, key=lambda m: m.account["account_id"]), key=rank)
    labels = ["has billing history", "is already under Bellhaven", "has the website's administrator as a contact",
              "has the website's phone number", "is Active", f"matches the website most closely ({best.rule})"]
    others = [m for m in strong if m is not best]
    why = "tied, so the lowest account id was kept"
    if others:
        for i, label in enumerate(labels):
            if rank(best)[i] > max(rank(o)[i] for o in others):
                why = label
                break
    return best, why


def _survivor_proposals(loc: Location, m: Match, bh: str, today: str) -> list[Proposal]:
    a = m.account
    changes, reasons = _field_changes(loc, a)
    wrong_parent = a["parent_id"] != bh
    if wrong_parent and must_preserve_old_account(a):
        reasons.append(
            f"Parent is '{a['parent_name'] or 'none'}' but the website lists it as Bellhaven. It has lifetime revenue "
            f"{a['lifetime_revenue']} and outstanding AR {a['outstanding_ar']}, so the billing SOP applies: leave this "
            "account as it is, create a new account under Bellhaven and point chow_current_account at it.")
        reasons.append("The new account takes the website's name and address; the old account keeps its current values.")
        new = _new_account(loc, bh, _note("", f"New account after change of ownership; old account {a['account_id']} "
                                              "kept for billing.", today, loc.url))
        return [Proposal("chow", f"Change of ownership: {loc.name}", a["account_id"], loc.slug, {"new_account": new},
                         {"parent_id": a["parent_id"], "chow_current_account": a["chow_current_account"]},
                         _evidence(loc, m, a, reasons))]
    if wrong_parent:
        changes["parent_id"] = bh
        reasons.append(f"Parent is '{a['parent_name'] or 'none'}' but the website lists it as Bellhaven. No revenue "
                       "history or no outstanding AR, so it is re-parented directly.")
    if not changes:
        return []
    changes["note"] = _note(a["note"], f"Updated {', '.join(changes)} to match the Bellhaven website.", today, loc.url)
    before = {k: a.get(k) for k in changes}
    if "parent_id" in changes:
        # re-checked before writing: if billing changed since, the SOP decision may no longer hold
        before.update(lifetime_revenue=a.get("lifetime_revenue"), outstanding_ar=a.get("outstanding_ar"))
    return [Proposal("update", f"Update {a['name']}", a["account_id"], loc.slug, changes, before,
                     _evidence(loc, m, a, reasons))]


def _duplicate(loc: Location, m: Match, survivor: Match, why: str, today: str) -> Proposal:
    a, s = m.account, survivor.account
    reasons = [f"Same facility as '{s['name']}' ({s['account_id']}), which is kept because it {why}."]
    confidence = "high"
    if amount(a.get("lifetime_revenue")) > 0:
        confidence = "low"
        reasons.append("This copy also has billing history; check with billing before marking it inactive.")
    if a["care_type"] and s["care_type"] and care_type_set(a["care_type"]) != care_type_set(s["care_type"]):
        confidence = "low"
        reasons.append(f"Care types differ ({a['care_type']} vs {s['care_type']}); it could be a separate building.")
    changes = {"duplicate_of_account": s["account_id"], "status": "Inactive",
               "note": _note(a["note"], f"Duplicate of {s['account_id']} ({s['name']}).", today, loc.url)}
    evidence = _evidence(loc, m, a, reasons)
    evidence["survivor"] = {"account_id": s["account_id"], "name": s["name"]}  # so the review app can name it
    return Proposal("duplicate", f"Duplicate: '{a['name']}' -> '{s['name']}'", a["account_id"], loc.slug, changes,
                    {k: a.get(k) for k in changes}, evidence, confidence)


def _create(loc: Location, bh: str, possible: list[Match], today: str) -> Proposal:
    reasons = ["No CRM account matches this community's address, phone or administrator."]
    confidence = "high"
    if possible:
        confidence = "low"
        reasons.append("Possible matches: " + "; ".join(
            f"'{m.account['name']}' ({m.account['account_id']}, {m.rule})" for m in possible))
    if not loc.in_directory:
        reasons.append("Linked from the homepage but missing from the community directory.")
    payload = _new_account(loc, bh, _note("", "Created from the Bellhaven website; no existing CRM account found.",
                                          today, loc.url))
    evidence = {"website": asdict(loc), "reasons": reasons,
                "candidates": [{"account": _crm_view(m.account), "rule": m.rule, "signals": list(m.signals)}
                               for m in possible]}
    return Proposal("create", f"Create {loc.name}", "", loc.slug, payload, {}, evidence, confidence)


ADMIN_TITLE = "Administrator"
ADMINISTRATOR = "administrator"  # the key, in an update's changes, of its Administrator contact change


def is_administrator(contact: dict) -> bool:
    return "administrator" in (contact.get("title") or "").lower()


def _administrator(loc: Location, a: dict, contacts: list[dict]) -> tuple[dict | None, list[str]]:
    """The change that brings the account's Administrator contact in line with the website, and its reasons
    (contacts: the account's active ones). None when the website's administrator is already a contact.

    The website's administrator is added as a new contact, and any other active Administrator contact is made
    inactive rather than renamed: people change jobs, and the CRM keeps who held the role. Only the name and title
    are set, because the website lists no direct phone or email for the administrator.
    """
    name = loc.administrator.strip()
    if not name or any(same_text(c.get("name"), name) for c in contacts):
        return None, []
    old = [{"contact_id": c["contact_id"], "name": c["name"]} for c in contacts if is_administrator(c)]
    if old:
        reasons = [f"The website lists {name} as administrator; the CRM's Administrator contact is "
                   f"{' and '.join(o['name'] for o in old)}.",
                   f"Adds {name} as a contact and makes the old one inactive (not renamed), so the CRM keeps who "
                   "held the role."]
    else:
        reasons = [f"The website lists {name} as administrator; this account has no Administrator contact.",
                   f"Adds {name} as a contact."]
    return {"add": {"account_id": a["account_id"], "name": name, "title": ADMIN_TITLE}, "deactivate": old}, reasons


def _with_administrator(group: list[Proposal], loc: Location, m: Match, contacts: list[dict]) -> list[Proposal]:
    """Fold the Administrator contact change into the survivor's update, so each account has one review. An account
    whose fields all agree gets an update with only the contact change."""
    a = m.account
    change, reasons = _administrator(loc, a, contacts)
    update = next((p for p in group if p.kind == "update" and p.account_id == a["account_id"]), None)
    if update is None:
        if change is None:
            return group
        update = Proposal("update", f"Update {a['name']}", a["account_id"], loc.slug, {}, {}, _evidence(loc, m, a, []))
        group = [update] + group
    if change is not None:
        update.changes[ADMINISTRATOR] = change
        update.evidence["reasons"] += reasons
    update.evidence["contacts"] = [{"name": c.get("name"), "title": c.get("title")} for c in contacts]
    return group


def _same_address(a: dict, b: dict) -> bool:
    street = norm_street(a["billing_street"])
    return bool(street) and street == norm_street(b["billing_street"]) and zip5(a["billing_zip"]) == zip5(b["billing_zip"])


def _not_on_website(accounts, bh: str, seen: set, skip: set, today: str) -> list[Proposal]:
    out = []
    for a in accounts:
        if a["parent_id"] != bh or a["account_id"] in seen or a["account_id"] in skip or a["status"] != "Active":
            continue
        reasons = ["Under the Bellhaven parent but not listed anywhere on the Bellhaven website."]
        text = "Not listed on the Bellhaven website; closed or sold, new owner unknown."
        # Decision #2: flag for review, never link or merge automatically. The CRM alone can't prove who owns it now.
        twin = next((b for b in accounts if b["account_id"] != a["account_id"] and b.get("parent_id") and b["parent_id"] != bh
                     and b["account_id"] not in skip and _same_address(a, b)), None)
        if twin:
            reasons.append(f"'{twin['name']}' ({twin['account_id']}) is at the same address under "
                           f"'{twin['parent_name']}', so the facility may have changed owner. Not linked "
                           "automatically: confirm the new owner first.")
            text = (f"Not listed on the Bellhaven website. Possibly sold: '{twin['name']}' ({twin['account_id']}) "
                    f"under '{twin['parent_name']}' is at the same address.")
        if must_preserve_old_account(a):
            reasons.append(f"Lifetime revenue {a['lifetime_revenue']} and outstanding AR {a['outstanding_ar']}: if it "
                           "did change owner, the billing SOP applies (keep this account and link a new one with "
                           "chow_current_account).")
        changes = {"status": NOT_ON_WEBSITE_STATUS, "note": _note(a["note"], text, today)}
        out.append(Proposal("not_on_website", f"Not on website: {a['name']}", a["account_id"], "", changes,
                            {k: a.get(k) for k in changes} | {"parent_id": a["parent_id"]},
                            _evidence(None, None, a, reasons)))
    return out


def build_proposals(locations, accounts, contacts, today: str) -> list[Proposal]:
    bh = bellhaven_parent_id(accounts)
    skip = parent_ids(accounts) | {a["account_id"] for a in accounts if already_resolved(a)}
    contacts_by_account = {}
    for c in contacts:
        if c.get("is_active", True):
            contacts_by_account.setdefault(c["account_id"], []).append(c)
    matches = assign_matches(locations, accounts, contacts_by_account, skip)
    proposals, seen = [], set()
    for loc in locations:
        found = matches[loc.slug]
        strong = [m for m in found if m.strong]       # meets a match rule: the same facility
        possible = [m for m in found if not m.strong]     # only a possible match: never acted on
        seen |= {m.account["account_id"] for m in strong}
        if not strong:
            seen |= {m.account["account_id"] for m in possible}  # shown as candidates on the create proposal
            proposals.append(_create(loc, bh, possible, today))
            continue
        survivor, why = choose_survivor(strong, bh)
        group = _survivor_proposals(loc, survivor, bh, today)
        chow_pending = any(p.kind == "chow" for p in group)
        # an account this tool created for a change of ownership that isn't linked yet is that CHOW's new account,
        # not a duplicate: retrying the CHOW reuses it
        group += [_duplicate(loc, m, survivor, why, today) for m in strong
                  if m is not survivor and not (chow_pending and m.account.get("created_by_candidate"))]
        if len(group) > 1 and any(p.kind == "chow" for p in group):
            for p in group:
                p.confidence = "low"
                p.evidence["reasons"].append(
                    "This community has both a change of ownership and duplicate copies: review these proposals "
                    "together. If a copy is already under Bellhaven, linking to it may be better than a new account.")
        if not chow_pending:  # a CHOW's old account stays exactly as it is, for billing
            group = _with_administrator(group, loc, survivor,
                                        contacts_by_account.get(survivor.account["account_id"], []))
        proposals += group
    return proposals + _not_on_website(accounts, bh, seen, skip, today)
