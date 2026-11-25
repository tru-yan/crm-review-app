from bellhaven import config


def test_env_file_handles_bom_quotes_and_export(tmp_path, monkeypatch):
    fake_env = {}
    monkeypatch.setattr(config.os, "environ", fake_env)
    env = tmp_path / ".env"
    env.write_bytes('﻿BH_TEST_A="quoted"\nexport BH_TEST_B=plain\n# comment\n'.encode("utf-8"))
    config._load_env_file(env)
    assert fake_env == {"BH_TEST_A": "quoted", "BH_TEST_B": "plain"}


def test_real_environment_wins_over_env_file(tmp_path, monkeypatch):
    fake_env = {"BH_TEST_A": "real"}
    monkeypatch.setattr(config.os, "environ", fake_env)
    env = tmp_path / ".env"
    env.write_text("BH_TEST_A=from-file\n", encoding="utf-8")
    config._load_env_file(env)
    assert fake_env["BH_TEST_A"] == "real"


def test_graded_crm_is_recognised_however_written():
    assert config.is_graded_crm("https://analyst-assessment-production.up.railway.app/api/v1")
    assert config.is_graded_crm("HTTPS://Analyst-Assessment-Production.up.railway.app/api/v1/")
    assert not config.is_graded_crm("http://localhost:8001/api/v1")


def test_only_local_hosts_count_as_the_mock():
    assert config.is_mock_crm("http://localhost:8001/api/v1")
    assert config.is_mock_crm("http://127.0.0.1:8001/api/v1")
    assert not config.is_mock_crm("https://analyst-assessment-production.up.railway.app./api/v1")
    assert not config.is_mock_crm("https://example.com/api/v1")
    assert config.is_graded_crm("https://analyst-assessment-production.up.railway.app./api/v1")
