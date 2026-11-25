"""A review queue with one proposal of each kind, made by the real pipeline over the fake CRM."""
from bellhaven import pipeline, store
from bellhaven.web import present
from tests.factories import CEDAR, FakeCrm, make_account, make_location, parent_accounts


def queue_crm() -> FakeCrm:
    return FakeCrm(parent_accounts() + [
        # update: renamed on the website
        make_account(account_id="A1", name="Riverbend Manor"),
        # chow: under another parent, with revenue and AR
        make_account(account_id="A2", name="Bellhaven of Tiffin", parent_id=CEDAR, lifetime_revenue=84000,
                     outstanding_ar=12400, billing_street="45 St Lawrence Dr", billing_city="Tiffin",
                     billing_zip="44883", phone="(614) 829-9278"),
        # duplicate: two copies of Owosso; A3 has the website's phone, so it is kept
        make_account(account_id="A3", name="Bellhaven of Owosso", billing_street="9 Elm St", billing_city="Owosso",
                     billing_state="MI", billing_zip="48867", phone="(989) 555-0101"),
        make_account(account_id="A4", name="Second Bellhaven of Owosso", billing_street="9 Elm St",
                     billing_city="Owosso", billing_state="MI", billing_zip="48867", phone="(989) 555-0199"),
        # not_on_website: under Bellhaven, not listed
        make_account(account_id="A5", name="Bellhaven of Coldwater", billing_street="1 Lake Rd",
                     billing_city="Coldwater", billing_state="MI", billing_zip="49036", phone="(517) 555-0102"),
    ])


def locations():
    site = lambda slug, **values: make_location(slug=f"/communities/{slug}", url=f"https://site.test/communities/{slug}",
                                                **values)
    return [
        site("chagrin-falls", name="Bellhaven of Chagrin Falls"),
        site("tiffin", name="Bellhaven of Tiffin", street="45 St Lawrence Drive", city="Tiffin", zip="44883",
             phone="(614) 829-9278", administrator="Kim Lee"),
        site("owosso", name="Bellhaven of Owosso", street="9 Elm Street", city="Owosso", state="MI", zip="48867",
             phone="(989) 555-0101", administrator="Sam Fox"),
        site("batavia", name="Bellhaven of Batavia", street="7 River Rd", city="Batavia", zip="45103",
             phone="(513) 555-0103", administrator="Dee Ray"),
    ]


def make_queue(crm=None):
    """(crm, conn) with five pending proposals: chow, create, duplicate, not_on_website, update."""
    crm = crm or queue_crm()
    conn = store.connect(":memory:")
    pipeline.run(conn, crm, locations, "2026-11-25")
    return crm, conn


def by_kind(conn) -> dict:
    """kind -> the parsed proposal (one of each)."""
    return {p["kind"]: p for p in (present.parse(r) for r in store.list_proposals(conn))}
