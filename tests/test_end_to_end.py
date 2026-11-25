from fastapi.testclient import TestClient

from bellhaven import pipeline, store
from bellhaven.apply import apply_proposal
from bellhaven.audit import audit
from bellhaven.crm import CrmClient
from mock_crm.server import create_app
from tests.factories import CEDAR, HARBORVIEW, MILLSTONE, make_account, make_location, parent_accounts

TODAY = "2026-11-25"


def world():
    accounts = parent_accounts() + [
        make_account(account_id="A1", name="Old Name"),                                    # update (rename)
        make_account(account_id="A2", name="Cedar Trail of Oakton", parent_id=CEDAR,       # chow
                     billing_street="200 Oak St", billing_city="Oakton", billing_zip="44100",
                     phone="(614) 555-0200", lifetime_revenue=84000, outstanding_ar=12400),
        make_account(account_id="A3", name="Bellhaven of Elmira", billing_street="300 Elm St",  # duplicate survivor
                     billing_city="Elmira", billing_zip="44200", phone="(614) 555-0300"),
        make_account(account_id="A4", name="Harborview of Elmira", parent_id=HARBORVIEW,   # duplicate
                     billing_street="300 Elm Street", billing_city="Elmira", billing_zip="44200",
                     phone="(614) 555-0301"),
        make_account(account_id="A5", name="Bellhaven of Coldwater", billing_street="90 N Michigan Ave",  # not on website
                     billing_city="Coldwater", billing_state="MI", billing_zip="49036", phone="(517) 555-0500"),
        make_account(account_id="A6", name="Bellhaven of Sandusky", billing_street="2715 Columbus Ave",   # not on website
                     billing_city="Sandusky", billing_zip="44870", phone="(419) 555-0600",
                     lifetime_revenue=130000, outstanding_ar=5200),
        make_account(account_id="A7", name="Millstone Care of Sandusky", parent_id=MILLSTONE,
                     billing_street="2715 Columbus Ave", billing_city="Sandusky", billing_zip="44870",
                     phone="(419) 555-0601"),
    ]
    locations = [
        make_location(),
        make_location(slug="/communities/oakton", name="Bellhaven of Oakton", street="200 Oak Street",
                      city="Oakton", zip="44100", phone="(614) 555-0200"),
        make_location(slug="/communities/elmira", name="Bellhaven of Elmira", street="300 Elm St",
                      city="Elmira", zip="44200", phone="(614) 555-0300"),
        make_location(slug="/communities/batavia", name="Bellhaven of Batavia", street="2000 Hospital Dr",  # create
                      city="Batavia", zip="45103", phone="(513) 555-0400"),
    ]
    return {"accounts": accounts, "contacts": []}, locations


def test_full_review_cycle_against_the_mock():
    seed, locations = world()
    crm = CrmClient(token="test", base="http://testserver/api/v1", session=TestClient(create_app(seed)))
    conn = store.connect(":memory:")

    pipeline.run(conn, crm, lambda: locations, TODAY)
    pending = store.list_proposals(conn, "pending")
    assert sorted(r["kind"] for r in pending) == ["chow", "create", "duplicate", "not_on_website", "not_on_website",
                                                  "update"]

    for row in pending:
        store.set_status(conn, row["key"], "applied", "e2e", apply_proposal(conn, crm, row))

    assert pipeline.run(conn, crm, lambda: locations, "2026-11-26")["proposals"] == 0
    assert audit(seed, crm.list_accounts(), crm.list_contacts(), locations, TODAY) == []
    assert {h["action"] for h in store.change_history(conn)} == {"create", "update"}
