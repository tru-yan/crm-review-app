"""Test data builders and an in-memory CRM. Shapes copy the real API responses."""
from bellhaven.models import Location

BH, HARBORVIEW, CEDAR, MILLSTONE = "P_BELLHAVEN", "P_HARBORVIEW", "P_CEDAR", "P_MILLSTONE"
PARENT_NAMES = {
    BH: "Bellhaven Senior Living (Parent Account)",
    HARBORVIEW: "Harborview Care Group (Parent Account)",
    CEDAR: "Cedar Trail Communities (Parent Account)",
    MILLSTONE: "Millstone Health Partners (Parent Account)",
}


def make_account(**overrides) -> dict:
    account = {
        "account_id": "A1", "name": "Bellhaven of Testville", "parent_id": BH, "parent_name": PARENT_NAMES[BH],
        "billing_street": "100 Main St", "billing_city": "Testville", "billing_state": "OH", "billing_zip": "44000",
        "care_type": "Assisted Living", "status": "Active", "phone": "(614) 555-0100",
        "lifetime_revenue": 0, "outstanding_ar": 0, "chow_current_account": "", "duplicate_of_account": "",
        "note": "", "created_by_candidate": False, "updated_at": "2026-11-24 00:00:00Z",
    }
    account.update(overrides)
    if "parent_id" in overrides and "parent_name" not in overrides:
        account["parent_name"] = PARENT_NAMES.get(overrides["parent_id"], "")
    return account


def parent_accounts() -> list[dict]:
    return [make_account(account_id=pid, name=name, parent_id="", parent_name="", billing_street="",
                         billing_city="", billing_state="", billing_zip="", care_type="", phone="")
            for pid, name in PARENT_NAMES.items()]


def make_location(**overrides) -> Location:
    values = {
        "slug": "/communities/bellhaven-of-testville", "url": "https://site.test/communities/bellhaven-of-testville",
        "name": "Bellhaven of Testville", "street": "100 Main Street", "city": "Testville", "state": "OH",
        "zip": "44000", "phone": "(614) 555-0100", "administrator": "Pat Admin",
        "care_offerings": ("Assisted Living",), "in_directory": True,
    }
    values.update(overrides)
    return Location(**values)


class FakeCrm:
    """Stands in for CrmClient. Records every write in self.writes."""

    def __init__(self, accounts, contacts=()):
        self.accounts = {a["account_id"]: dict(a) for a in accounts}
        self.contacts = list(contacts)
        self.writes = []
        self._next_id = 0

    def list_accounts(self, **filters):
        field_for = {"zip": "billing_zip", "parent_id": "parent_id"}
        return [dict(a) for a in self.accounts.values()
                if all(a.get(field_for[k]) == v for k, v in filters.items())]

    def list_contacts(self, **filters):
        return list(self.contacts)

    def get_account(self, account_id):
        return dict(self.accounts[account_id])

    def create_account(self, body):
        self._next_id += 1
        record = {f: "" for f in make_account()} | {"status": "Active", "lifetime_revenue": 0, "outstanding_ar": 0}
        record |= dict(body)
        record |= {"account_id": f"NEW{self._next_id}", "created_by_candidate": True,
                   "parent_name": PARENT_NAMES.get(record["parent_id"], ""), "updated_at": "2026-11-25 00:00:00Z"}
        self.accounts[record["account_id"]] = record
        self.writes.append(("POST", dict(body)))
        return dict(record)

    def update_account(self, account_id, body):
        self.accounts[account_id].update(body)
        self.writes.append(("PATCH", account_id, dict(body)))
        return dict(self.accounts[account_id])
