import pytest

from bellhaven import pipeline, store
from mock_crm import approve_all
from tests.review_fixtures import locations, queue_crm

MOCK = "http://localhost:8001/api/v1"


def on_the_mock(monkeypatch, tmp_path, crm):
    """A test database filled by the pipeline over crm, with approve_all pointed at it and at crm."""
    db = tmp_path / "test.db"
    pipeline.run(store.connect(db, crm_base=MOCK), crm, locations, "2026-11-25")
    for name, value in (("USING_MOCK", True), ("API_BASE", MOCK), ("DB_PATH", db), ("CrmClient", lambda: crm)):
        monkeypatch.setattr(approve_all, name, value)
    return db


def test_refuses_the_live_sandbox(monkeypatch):
    monkeypatch.setattr(approve_all, "USING_MOCK", False)
    with pytest.raises(SystemExit, match="Refusing"):
        approve_all.main()


def test_applies_every_pending_proposal_on_the_mock(monkeypatch, tmp_path):
    crm = queue_crm()
    db = on_the_mock(monkeypatch, tmp_path, crm)
    approve_all.main()
    assert store.counts(store.connect(db)) == {"applied": 5}


def test_reports_what_it_could_not_apply_and_carries_on(monkeypatch, tmp_path):
    crm = queue_crm()
    db = on_the_mock(monkeypatch, tmp_path, crm)
    crm.accounts["A1"]["name"] = "Renamed meanwhile"  # the rename proposal is now stale
    with pytest.raises(SystemExit, match="Update Riverbend Manor: stale"):
        approve_all.main()
    assert store.counts(store.connect(db)) == {"applied": 4, "failed": 1}
