import pytest
from fastapi.testclient import TestClient

from bellhaven.crm import CrmClient, CrmError
from mock_crm.server import create_app
from tests.factories import BH, HARBORVIEW, make_account, parent_accounts


def mock(extra_accounts=0):
    accounts = parent_accounts() + [make_account()] + [
        make_account(account_id=f"X{i}", name=f"Other {i}", parent_id=HARBORVIEW) for i in range(extra_accounts)]
    contacts = [{"contact_id": "C1", "account_id": "A1", "name": "Pat Admin", "title": "Administrator",
                 "email": "", "phone": "", "is_active": True}]
    http = TestClient(create_app({"accounts": accounts, "contacts": contacts}))
    return CrmClient(token="test", base="http://testserver/api/v1", session=http), http


def test_lists_page_through_all_accounts():
    crm, _ = mock(extra_accounts=60)
    assert len(crm.list_accounts()) == 4 + 1 + 60
    assert [c["name"] for c in crm.list_contacts()] == ["Pat Admin"]


def test_requires_a_token():
    # a fresh client: CrmClient adds the token to the session it is given
    bare = TestClient(create_app({"accounts": parent_accounts(), "contacts": []}))
    assert bare.get("/api/v1/accounts").status_code == 401


def test_create_marks_account_as_candidate_created_and_fills_parent_name():
    crm, _ = mock()
    created = crm.create_account({"name": "Bellhaven of Batavia", "parent_id": BH, "billing_zip": "45103"})
    assert created["created_by_candidate"] is True
    assert created["parent_name"] == "Bellhaven Senior Living (Parent Account)"
    assert crm.get_account(created["account_id"])["name"] == "Bellhaven of Batavia"


def test_reparenting_updates_parent_name():
    crm, _ = mock()
    assert crm.update_account("A1", {"parent_id": HARBORVIEW})["parent_name"] == "Harborview Care Group (Parent Account)"


@pytest.mark.parametrize("body", [
    {"status": "Closed"},                # not Active / Inactive / Needs Review
    {"lifetime_revenue": 0},             # billing fields are read-only
    {"parent_id": "NOPE"},               # references must point to real accounts
    {"duplicate_of_account": "NOPE"},
])
def test_rejects_what_the_real_api_might_reject(body):
    crm, _ = mock()
    with pytest.raises(CrmError, match="422"):
        crm.update_account("A1", body)


def test_filters_by_zip():
    crm, _ = mock()
    assert [a["account_id"] for a in crm.list_accounts(zip="44000")] == ["A1"]



@pytest.mark.parametrize("body", [
    {},                              # nothing to update
    {"billing_zip": 45103},          # not a string, e.g. a scraper that lost a leading zero
    {"parent_id": ["x"]},
    {"name": "  "},                  # blank name
    {"duplicate_of_account": "A1"},  # points to the account itself
])
def test_rejects_bad_updates(body):
    crm, _ = mock()
    with pytest.raises(CrmError, match="422"):
        crm.update_account("A1", body)


def test_rejects_unknown_filters():
    _, http = mock()
    assert http.get("/api/v1/accounts", params={"zipp": "44000"}).status_code == 422


def test_rejects_malformed_json():
    _, http = mock()
    r = http.patch("/api/v1/accounts/A1", content="{not json", headers={"Content-Type": "application/json"})
    assert r.status_code == 422


def test_writes_need_a_token_and_unknown_ids_are_404():
    bare = TestClient(create_app({"accounts": parent_accounts() + [make_account()], "contacts": []}))
    assert bare.post("/api/v1/accounts", json={"name": "X"}).status_code == 401
    assert bare.patch("/api/v1/accounts/A1", json={"note": "n"}).status_code == 401
    crm, _ = mock()
    with pytest.raises(CrmError, match="404"):
        crm.get_account("NOPE")


def test_seed_is_not_mutated():
    seed = {"accounts": parent_accounts() + [make_account()], "contacts": []}
    crm = CrmClient(token="t", base="http://testserver/api/v1", session=TestClient(create_app(seed)))
    crm.update_account("A1", {"note": "changed"})
    assert seed["accounts"][-1]["note"] == ""
