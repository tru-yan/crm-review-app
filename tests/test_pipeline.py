import pytest

from bellhaven import pipeline, store
from bellhaven.crm import CrmError
from bellhaven.scrape import ScrapeError
from tests.factories import FakeCrm, make_account, make_location, parent_accounts


def test_pipeline_never_writes_and_reruns_are_safe():
    crm = FakeCrm(parent_accounts() + [make_account(name="Old Name")])
    conn = store.connect(":memory:")
    scrape = lambda: [make_location()]
    first = pipeline.run(conn, crm, scrape, "2026-11-25")
    second = pipeline.run(conn, crm, scrape, "2026-11-26")
    assert first["new"] == 1
    assert second["new"] == 0 and second["already_known"] == 1
    assert crm.writes == []


def test_a_failed_read_changes_nothing():
    crm = FakeCrm(parent_accounts() + [make_account(name="Old Name")])
    conn = store.connect(":memory:")
    pipeline.run(conn, crm, lambda: [make_location()], "2026-11-25")

    def broken_scrape():
        raise ScrapeError("site layout changed")

    class BrokenCrm(FakeCrm):
        def list_accounts(self, **filters):
            raise CrmError("timeout")

    with pytest.raises(ScrapeError):
        pipeline.run(conn, crm, broken_scrape, "2026-11-26")
    with pytest.raises(CrmError):
        pipeline.run(conn, BrokenCrm([]), lambda: [make_location()], "2026-11-26")
    assert [r["status"] for r in store.list_proposals(conn)] == ["pending"]
    assert crm.writes == []
