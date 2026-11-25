"""What the review pages show, worked out from stored proposals. Pure: no database, no network, no HTML."""
import json
import math
from datetime import datetime

from ..classify import ADMINISTRATOR
from ..config import BELLHAVEN_PARENT_NAME
from ..normalize import norm_phone, norm_street, same_care_types, same_text, zip5
from ..sop import amount
from ..store import STUCK_MINUTES

# The types the reviewer sees. An Administrator contact change is part of the account's update; proposals stored
# before that, with the kind administrator, are shown as updates too.
KIND_LABELS = {"update": "Update", "chow": "Ownership", "create": "New account", "duplicate": "Duplicate",
               "not_on_website": "Not on site"}
TYPE_OF = {"administrator": "update"}
# The type order: the kinds that add an account (which can't be deleted) first, then the other status changes.
KIND_ORDER = ("chow", "create", "duplicate", "not_on_website", "update")
SORTS = ("ar", "revenue", "type", "account", "change", "status")  # one per queue column; the first is the default
DESCENDING = ("ar", "revenue")  # these start highest first; the others start A to Z, or in their order below
STATUS_ORDER = ("pending", "applying", "failed", "applied", "rejected", "superseded")  # what needs action first
FIELD_LABELS = {"name": "Name", "parent_id": "Parent", "billing_street": "Street", "billing_city": "City",
                "billing_state": "State", "billing_zip": "Zip", "care_type": "Care type", "status": "Status",
                "phone": "Phone", "note": "Note", "duplicate_of_account": "Duplicate of",
                "chow_current_account": "CHOW current account", "administrator_added": "Administrator added",
                "administrator_made_inactive": "Administrator made inactive"}
SHORT_FIELDS = ("billing_zip", "billing_state", "status", "phone")  # summaries show the old value: Zip 45626 → 45662
ACCOUNT_ID_FIELDS = ("duplicate_of_account", "chow_current_account")
BANNER_OUTCOMES = ("applied", "already_applied", "stale", "failed", "already_handled", "rejected", "unrecorded",
                   "error")
APPLYING_HINT = ("It is being applied right now, or an earlier apply was interrupted. If it stays like this, check "
                 f"its change log: a pipeline run started {STUCK_MINUTES} or more minutes from now puts it back in "
                 "the queue if the change didn't land.")
# The stored status each banner needs. A link reloaded after the proposal changed again shows no banner,
# so a reopened proposal is never announced as written.
BANNER_STATUS = {"applied": "applied", "already_applied": "applied", "stale": "failed", "failed": "failed",
                 "rejected": "rejected"}


def type_of(kind: str) -> str:
    """The type a proposal kind is shown, filtered and sorted under."""
    return TYPE_OF.get(kind, kind)


def kind_label(kind: str) -> str:
    return KIND_LABELS.get(type_of(kind), kind)


def field_label(field: str) -> str:
    return FIELD_LABELS.get(field, field.replace("_", " ").capitalize())


def parse(row) -> dict:
    """A stored proposal as a plain dict, with its JSON columns decoded."""
    p = dict(row)
    for column in ("changes", "before", "evidence"):
        p[column] = json.loads(p[column])
    p["result"] = json.loads(p["result"]) if p.get("result") else None
    return p


def new_account(p) -> dict:
    """The account a create or a CHOW adds; empty for the other kinds."""
    if p["kind"] == "create":
        return p["changes"]
    if p["kind"] == "chow":
        return p["changes"]["new_account"]
    return {}


def account_names(proposals) -> dict:
    """Account id -> name, from everything the stored proposals know. Used to show ids as names.

    Newer evidence wins: a run that refreshes a proposal also sets its run_id, and decided proposals keep the
    evidence they were made with. So a survivor renamed since an older proposal shows its current name.
    """
    names = {}
    for p in sorted(proposals, key=lambda p: p.get("run_id") or 0):
        ev = p["evidence"]
        for a in [ev.get("crm") or {}] + [c["account"] for c in ev.get("candidates", [])]:
            if a.get("parent_id") and a.get("parent_name"):
                names[a["parent_id"]] = a["parent_name"]
            if a.get("account_id") and a.get("name"):
                names[a["account_id"]] = a["name"]
        survivor = ev.get("survivor") or {}
        if survivor.get("account_id") and survivor.get("name"):
            names[survivor["account_id"]] = survivor["name"]
        created = (p["result"] or {}).get("created")
        if created and new_account(p).get("name"):
            names[created] = new_account(p)["name"]
    return names


def display_name(p) -> str:
    """The account the reviewer is deciding about: the new account's name for a create or CHOW."""
    return new_account(p).get("name") or (p["evidence"].get("crm") or {}).get("name") or p["title"]


def place(p) -> str:
    """'City, ST' for the queue, from the website if the community is listed there, else from the CRM."""
    site, crm = p["evidence"].get("website") or {}, p["evidence"].get("crm") or {}
    city = site.get("city") or crm.get("billing_city") or ""
    state = site.get("state") or crm.get("billing_state") or ""
    return ", ".join(part for part in (city, state) if part)


def matches(p, q: str) -> bool:
    """The queue search: account name (current or new) or city."""
    needle = (q or "").strip().casefold()
    if not needle:
        return True
    crm = p["evidence"].get("crm") or {}
    return any(needle in (text or "").casefold() for text in (display_name(p), crm.get("name"), place(p)))


def money(value) -> str:
    x = amount(value)
    if math.isinf(x):
        return str(value)
    sign, x = ("-", -x) if x < 0 else ("", x)
    return f"{sign}${x:,.0f}" if x == int(x) else f"{sign}${x:,.2f}"


def show(field: str, value, names: dict) -> str | None:
    """A field value as the reviewer should read it, or None when it is empty."""
    if value is None or value == "":
        return None
    if field == "parent_id":
        return names.get(value, value)
    if field in ACCOUNT_ID_FIELDS:
        return f"{names[value]} ({value})" if value in names else str(value)
    if field in ("lifetime_revenue", "outstanding_ar"):
        return money(value)
    return str(value)


def summary(p, names: dict) -> str:
    """The queue's one-line description of the change."""
    kind, changes = p["kind"], p["changes"]
    if kind == "chow":
        return "New Bellhaven account; old one kept for billing"
    if kind == "create":
        return "Create under Bellhaven"
    if kind == "duplicate":
        survivor = changes["duplicate_of_account"]
        name = names.get(survivor, survivor)
        same = name == display_name(p)  # two copies with one name: tell them apart by the survivor's id
        return f"Duplicate of {name}" + (f" ({survivor})" if same else "")
    if kind == "not_on_website":
        return f"Status → {changes['status']}"
    if kind == "administrator":
        return _administrator_summary(changes)
    parts = []
    # the phone goes last: only two changes are shown, and a name, parent or administrator change matters more
    for field, value in sorted(changes.items(), key=lambda item: item[0] == "phone"):
        if field == "note":
            continue
        if field == ADMINISTRATOR:
            parts.append(_administrator_summary(value))
            continue
        after = show(field, value, names) or "empty"
        if field == "parent_id":
            after = after.removesuffix(" (Parent Account)")
        if field in SHORT_FIELDS:
            parts.append(f"{field_label(field)} {show(field, p['before'].get(field), names) or 'empty'} → {after}")
        else:
            parts.append(f"{field_label(field)} → {after}")
    return ", ".join(parts[:2]) + (f" +{len(parts) - 2} more" if len(parts) > 2 else "")


def _administrator_summary(change: dict) -> str:
    old, new = " and ".join(o["name"] for o in change["deactivate"]), change["add"]["name"]
    return f"Administrator {old} → {new}" if old else f"Add administrator {new}"


def _administrator_rows(change: dict) -> list[dict]:
    old = change["deactivate"]
    rows = [{"group": "", "field": ADMINISTRATOR, "label": "Administrator contact",
             "now": " and ".join(o["name"] for o in old) or None, "after": f"{change['add']['name']} (new contact)",
             "new": False}]
    return rows + [{"group": "", "field": "is_active", "label": f"{o['name']}'s contact", "now": "active",
                    "after": "inactive", "new": False} for o in old]


def _row(group: str, field: str, now, after, names: dict, new: bool = False) -> dict:
    return {"group": group, "field": field, "label": field_label(field), "now": show(field, now, names),
            "after": show(field, after, names), "new": new}


def change_rows(p, names: dict) -> list[dict]:
    """The Now → After table: only the fields that change. A create or CHOW lists the new account's fields."""
    kind, changes, before = p["kind"], p["changes"], p["before"]
    if kind == "administrator":
        return _administrator_rows(changes)
    if kind not in ("create", "chow"):
        rows = [_row("", f, before.get(f), v, names) for f, v in changes.items() if f not in (ADMINISTRATOR, "note")]
        if kind == "update" and ADMINISTRATOR in changes:
            rows += _administrator_rows(changes[ADMINISTRATOR])
        if "note" in changes:  # last, after the contact rows
            rows.append(_row("", "note", before.get("note"), changes["note"], names))
        return rows
    group = "New account" if kind == "chow" else ""
    rows = [_row(group, f, None, v, names, new=True) for f, v in new_account(p).items()]
    if kind == "chow":
        created = (p["result"] or {}).get("created")
        rows.append(_row(f"Old account {p['account_id']}", "chow_current_account",
                         before.get("chow_current_account"), created or "the new account's id", names))
    return rows


def _state(value) -> str:
    return (value or "").strip().upper()


def _differences(site: dict, crm: dict) -> set:
    """Which website-vs-CRM pairs disagree, using the same comparisons as the matching."""
    out = set()
    if not same_text(site["name"], crm.get("name")):
        out.add("name")
    if norm_street(site["street"]) != norm_street(crm.get("billing_street")):
        out.add("street")
    if (not same_text(site["city"], crm.get("billing_city"))
            or _state(site["state"]) != _state(crm.get("billing_state"))
            or zip5(site["zip"]) != zip5(crm.get("billing_zip"))):
        out.add("place")
    if norm_phone(site["phone"]) != norm_phone(crm.get("phone")):
        out.add("phone")
    if site["care_offerings"] and not same_care_types(crm.get("care_type"), site["care_offerings"]):
        out.add("care")
    if crm.get("parent_name") != BELLHAVEN_PARENT_NAME:
        out.add("parent")
    if crm.get("status") != "Active":
        out.add("status")
    return out


def _cell(label: str, value, differs: bool = False, alert: bool = False) -> dict:
    return {"label": label, "value": value if value not in (None, "") else None, "differs": differs, "alert": alert}


def side_by_side(p, names: dict) -> dict:
    """Website and CRM field grids, with the fields that disagree flagged. A side is None when absent."""
    site, crm = p["evidence"].get("website"), p["evidence"].get("crm")
    diff = _differences(site, crm) if site and crm else set()
    website = account = None
    if site:
        website = [_cell("Name", site["name"], "name" in diff), _cell("Street", site["street"], "street" in diff),
                   _cell("City / State / Zip", f"{site['city']}, {site['state']} {site['zip']}", "place" in diff),
                   _cell("Phone", site["phone"], "phone" in diff), _cell("Administrator", site["administrator"]),
                   _cell("Care offerings", ", ".join(site["care_offerings"]), "care" in diff)]
    if crm:
        csz = f"{crm.get('billing_city') or ''}, {crm.get('billing_state') or ''} {crm.get('billing_zip') or ''}"
        account = [_cell("Name", crm.get("name"), "name" in diff),
                   _cell("Parent", show("parent_id", crm.get("parent_id"), names), "parent" in diff),
                   _cell("Street", crm.get("billing_street"), "street" in diff),
                   _cell("City / State / Zip", csz.strip(" ,"), "place" in diff),
                   _cell("Phone", crm.get("phone"), "phone" in diff),
                   _cell("Care type", crm.get("care_type"), "care" in diff),
                   _cell("Status", crm.get("status"), "status" in diff),
                   _cell("Lifetime revenue", money(crm.get("lifetime_revenue"))),
                   _cell("Outstanding AR", money(crm.get("outstanding_ar")),
                         alert=amount(crm.get("outstanding_ar")) > 0)]
        contacts = p["evidence"].get("contacts")
        if contacts is not None:  # an update: the account's Administrator contacts, beside the website's
            admins = [c["name"] for c in contacts if "administrator" in (c.get("title") or "").lower()]
            differs = bool(site) and not any(same_text(n, site["administrator"]) for n in admins)
            account.append(_cell("Administrator contact", ", ".join(admins), differs))
            if website:
                website[4]["differs"] = differs  # the website's Administrator cell
    return {"website": website, "crm": account, "url": (site or {}).get("url")}


SIGNAL_LABELS = {"street": "street", "near_street": "near-same street", "administrator": "administrator",
                 "phone": "phone", "name": "name", "similar_name": "similar name", "zip": "zip", "city": "city"}


def signals(p) -> dict:
    """The match rule and the evidence that agrees; for a create, the possible matches that were not used."""
    ev = p["evidence"]
    match = ev.get("match") or {}

    def chips(found):
        return [SIGNAL_LABELS.get(s, s) for s in found or ()]

    return {"rule": match.get("rule"), "chips": chips(match.get("signals")),
            "candidates": [{"name": c["account"].get("name"), "account_id": c["account"].get("account_id"),
                            "rule": c.get("rule"), "chips": chips(c.get("signals"))} for c in ev.get("candidates", [])]}


def sop(p) -> dict | None:
    """The billing SOP check, shown when the proposal moves an account to another parent."""
    s = p["evidence"].get("sop")
    if not s or not (p["kind"] == "chow" or "parent_id" in p["changes"]):
        return None
    verdict = ("keep the old account and use a change of ownership" if s["preserve_old_account"]
               else "re-parenting allowed")
    return {"preserve": s["preserve_old_account"],
            "text": f"Lifetime revenue {money(s['lifetime_revenue'])}, outstanding AR "
                    f"{money(s['outstanding_ar'])}: {verdict}"}


def billing(p) -> tuple[float, float] | None:
    """(lifetime revenue, outstanding AR) of the CRM account a proposal is about; None for a create, which has none.
    Unreadable amounts count as money involved (infinite), as the billing SOP does: highest of all."""
    crm = p["evidence"].get("crm")
    if not crm:
        return None
    return amount(crm.get("lifetime_revenue")), amount(crm.get("outstanding_ar"))


def billing_cells(p) -> dict:
    """The queue's revenue and AR cells: formatted amounts (None for a create), and whether AR is owed."""
    crm = p["evidence"].get("crm")
    if not crm:
        return {"revenue": None, "ar": None, "ar_due": False}
    return {"revenue": money(crm.get("lifetime_revenue")), "ar": money(crm.get("outstanding_ar")),
            "ar_due": amount(crm.get("outstanding_ar")) > 0}


def _rank(order: tuple, value) -> int:
    return order.index(value) if value in order else len(order)


def queue_order(proposals, sort: str = "ar", descending: bool | None = None, names: dict | None = None) -> list:
    """The queue's order, for one of SORTS. descending=None gives the sort's usual direction (DESCENDING):
    - ar (the default): outstanding AR, then lifetime revenue, highest first
    - revenue: lifetime revenue, then outstanding AR, highest first
    - type: KIND_ORDER; status: STATUS_ORDER
    - account, change: A to Z, by the account's name or by the one-line summary (which needs names)
    Ties fall back to the type order, then the account name. A create has no CRM account, so no billing history:
    it comes last in the money sorts, in either direction."""
    if descending is None:
        descending = sort in DESCENDING
    # sorted() is stable, also in reverse, so this order breaks every tie
    ties = sorted(proposals, key=lambda p: (_rank(KIND_ORDER, type_of(p["kind"])), display_name(p).casefold(),
                                            p["key"]))
    if sort in ("ar", "revenue"):
        known = [p for p in ties if billing(p) is not None]
        first = 1 if sort == "ar" else 0  # billing() is (revenue, ar)
        known.sort(key=lambda p: (billing(p)[first], billing(p)[1 - first]), reverse=descending)
        return known + [p for p in ties if billing(p) is None]
    keys = {"type": lambda p: _rank(KIND_ORDER, type_of(p["kind"])),
            "status": lambda p: _rank(STATUS_ORDER, p["status"]),
            "account": lambda p: display_name(p).casefold(),
            "change": lambda p: summary(p, names or {}).casefold()}
    return sorted(ties, key=keys[sort], reverse=descending)


def position(proposals, key: str) -> tuple[int, int] | None:
    """(n, N) for 'n of N pending', or None when the proposal isn't pending."""
    keys = [p["key"] for p in queue_order([p for p in proposals if p["status"] == "pending"])]
    return (keys.index(key) + 1, len(keys)) if key in keys else None


def next_pending(proposals, after_key: str) -> str | None:
    """The pending proposal after after_key in queue order, wrapping round. None when nothing else is pending."""
    ordered = [p["key"] for p in queue_order([p for p in proposals
                                              if p["status"] == "pending" or p["key"] == after_key])]
    if after_key in ordered:
        i = ordered.index(after_key)
        ordered = ordered[i + 1:] + ordered[:i]
    return ordered[0] if ordered else None


def linked_account(p) -> str | None:
    """The CRM account a proposal's page links to: the account it created once it exists, else the one it changes."""
    return (p["result"] or {}).get("created") or p["account_id"] or None


def failure(p) -> str | None:
    """Why the last attempt failed, as stored on the proposal."""
    result = p["result"] or {}
    return result.get("stale") or result.get("error")


def mismatches(result) -> list[str]:
    """Fields the CRM stored differently from what was sent, one line each."""
    found = (result or {}).get("mismatches") or {}
    if set(found) == {"new_account", "link"}:  # a CHOW: the new account, then the old account's link
        items = [("New account: ", f, d) for f, d in found["new_account"].items()]
        items += [("Old account: ", f, d) for f, d in found["link"].items()]
    else:
        items = [("", f, d) for f, d in found.items()]
    # an account field by its label; a contact's line is already readable ("Administrator contact title")
    return [f"{where}{FIELD_LABELS.get(f, f)} sent {d['sent']!r}, the CRM stored {d['returned']!r}"
            for where, f, d in items]


def banner(p, outcome: str, names: dict) -> dict | None:
    """The message after a decision, read from the stored proposal. outcome names the decision that was made."""
    if outcome not in BANNER_OUTCOMES or p["status"] != BANNER_STATUS.get(outcome, p["status"]):
        return None
    result = p["result"] or {}
    b = {"key": p["key"], "name": p["title"], "details": [], "account_id": None, "retry": False}
    if outcome == "applied":
        lines = mismatches(result)
        text = (f"{summary(p, names)}. "
                + ("The CRM stored exactly what was sent." if not lines else "Some fields were stored differently:"))
        return b | {"tone": "ok", "title": "Written", "text": text, "details": lines, "account_id": linked_account(p)}
    if outcome == "already_applied":
        return b | {"tone": "ok", "title": "Already written", "account_id": linked_account(p),
                    "text": "This change was already in the CRM (an earlier response was lost); it's now recorded."}
    if outcome == "stale":
        return b | {"tone": "warn", "title": "Not written",
                    "text": f"The CRM record changed since this proposal was made: {result.get('stale')}. "
                            "Re-run the pipeline."}
    if outcome == "failed":
        return b | {"tone": "error", "title": "Failed", "text": result.get("error") or "", "retry": True}
    if outcome == "rejected":
        return b | {"tone": "info", "title": "Rejected", "text": ""}
    if outcome == "error":
        return b | {"tone": "error", "title": "Not saved", "account_id": linked_account(p),
                    "text": "The database failed while handling this decision, so it may not be fully recorded. "
                            "The server output has the error. Check the account in the CRM before trying again."}
    if outcome == "unrecorded":
        return b | {"tone": "error", "title": "Written, but not recorded",
                    "text": "The CRM write went through, but saving the decision failed. The API log and change "
                            "log have the write; the server output has the error."}
    text = f"It is {p['status']} now, so nothing was done."
    return b | {"tone": "warn", "title": "Already handled",
                "text": text + (" " + APPLYING_HINT if p["status"] == "applying" else "")}


def when(timestamp: str | None) -> str:
    """A stored UTC timestamp in local time, to the minute."""
    if not timestamp:
        return ""
    return datetime.fromisoformat(timestamp).astimezone().strftime("%Y-%m-%d %H:%M")


def run_view(run) -> dict:
    """One pipeline run for the runs page and the queue header."""
    stats = json.loads(run["stats"]) if run["stats"] else {}
    if run["finished_at"]:
        result = "OK"
    elif "error" in stats:
        result = f"Failed: {stats['error']}"
    else:
        result = "Running or interrupted"
    return {"id": run["id"], "started": when(run["started_at"]), "ok": bool(run["finished_at"]),
            "result": result, "stats": stats}
