from bellhaven import pipeline, store
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
