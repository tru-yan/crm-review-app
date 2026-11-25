import pytest

from bellhaven import pipeline, store
from mock_crm import approve_all
from tests.review_fixtures import locations, queue_crm

MOCK = "http://localhost:8001/api/v1"


def test_refuses_the_live_sandbox(monkeypatch):
    monkeypatch.setattr(approve_all, "USING_MOCK", False)
    with pytest.raises(SystemExit, match="Refusing"):
        approve_all.main()


def test_applies_every_pending_proposal_on_the_mock(monkeypatch, tmp_path):
    crm, db = queue_crm(), tmp_path / "test.db"
    pipeline.run(store.connect(db, crm_base=MOCK), crm, locations, "2026-11-25")
    monkeypatch.setattr(approve_all, "USING_MOCK", True)
    monkeypatch.setattr(approve_all, "API_BASE", MOCK)
    monkeypatch.setattr(approve_all, "DB_PATH", db)
    monkeypatch.setattr(approve_all, "CrmClient", lambda: crm)
    approve_all.main()
    assert store.counts(store.connect(db)) == {"applied": 5}


def test_reports_what_it_could_not_apply_and_carries_on(monkeypatch, tmp_path):
    crm, db = queue_crm(), tmp_path / "test.db"
    pipeline.run(store.connect(db, crm_base=MOCK), crm, locations, "2026-11-25")
    crm.accounts["A1"]["name"] = "Renamed meanwhile"  # the rename proposal is now stale
    monkeypatch.setattr(approve_all, "USING_MOCK", True)
    monkeypatch.setattr(approve_all, "API_BASE", MOCK)
    monkeypatch.setattr(approve_all, "DB_PATH", db)
    monkeypatch.setattr(approve_all, "CrmClient", lambda: crm)
    with pytest.raises(SystemExit, match="Update Riverbend Manor: stale"):
        approve_all.main()
    assert store.counts(store.connect(db)) == {"applied": 4, "failed": 1}
