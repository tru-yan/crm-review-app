import json

import pytest

from mock_crm import snapshot


class FakeClient:
    accounts = [{"account_id": "A1", "created_by_candidate": False}]

    def __init__(self, base):
        self.base = base

    def list_accounts(self):
        return list(self.accounts)

    def list_contacts(self):
        return []


def test_refuses_to_snapshot_anything_but_the_graded_sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(snapshot, "LIVE_API_BASE", "http://localhost:8001/api/v1")
    with pytest.raises(SystemExit, match="not the graded sandbox"):
        snapshot.main(["--out", str(tmp_path / "crm.json")])
    assert not (tmp_path / "crm.json").exists()


def test_refuses_to_overwrite_an_existing_snapshot(tmp_path):
    out = tmp_path / "crm.json"
    out.write_text("{}")
    with pytest.raises(SystemExit, match="already exists"):
        snapshot.main(["--out", str(out)])
    assert out.read_text() == "{}"


def test_refuses_a_crm_that_already_has_our_writes(tmp_path, monkeypatch):
    class Written(FakeClient):
        accounts = [{"account_id": "001MOCK1", "created_by_candidate": True}]

    monkeypatch.setattr(snapshot, "CrmClient", Written)
    with pytest.raises(SystemExit, match="created by this tool"):
        snapshot.main(["--out", str(tmp_path / "crm.json")])
    assert not (tmp_path / "crm.json").exists()


def test_writes_the_snapshot_in_one_step(tmp_path, monkeypatch):
    monkeypatch.setattr(snapshot, "CrmClient", FakeClient)
    out = tmp_path / "sub" / "crm.json"
    snapshot.main(["--out", str(out)])
    assert len(json.loads(out.read_text())["accounts"]) == 1
    assert not list(out.parent.glob("*.tmp"))
