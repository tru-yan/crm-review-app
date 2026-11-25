from fastapi.testclient import TestClient

from bellhaven import config, store
from bellhaven.crm import CrmError
from bellhaven.web.app import build, create_app
from tests.review_fixtures import by_kind, make_queue

MERIDIAN = "https://crm.test/crm/tok_secret"


def client_for(live=False):
    crm, conn = make_queue()
    app = create_app(conn, crm, live=live, meridian_base=MERIDIAN if live else None)
    return TestClient(app), app, crm, conn


def form(app, **values):
    return {"csrf_token": app.state.csrf_token} | values


def state(conn, crm):
    """Everything a GET must leave unchanged."""
    proposals = [tuple(r) for r in conn.execute("SELECT key, status, decided_at, comment, result FROM proposals")]
    return proposals, conn.execute("SELECT COUNT(*) FROM api_log").fetchone()[0], list(crm.writes)


def test_every_page_renders_and_no_get_writes():
    http, app, crm, conn = client_for()
    before = state(conn, crm)
    paths = ["/queue", "/queue?status=all", "/queue?kind=chow&q=tiffin", "/history", "/runs"]
    paths += [f"/proposals/{p['key']}" for p in by_kind(conn).values()]
    for path in paths:
        assert http.get(path).status_code == 200, path
    assert http.get("/").url.path == "/queue"
    assert state(conn, crm) == before


def test_an_unknown_proposal_is_404_with_a_way_back():
    http, *_ = client_for()
    r = http.get("/proposals/nope")
    assert r.status_code == 404 and 'href="/queue"' in r.text


def test_queue_lists_pending_proposals_in_order_with_summaries():
    http, *_ = client_for()
    page = http.get("/queue").text
    names = ["Bellhaven of Tiffin", "Bellhaven of Batavia", "Second Bellhaven of Owosso", "Bellhaven of Coldwater",
             "Riverbend Manor"]
    assert [page.index(n) for n in names] == sorted(page.index(n) for n in names)
    assert "Name → Bellhaven of Chagrin Falls" in page and "Duplicate of Bellhaven of Owosso" in page


def test_queue_filters_by_kind_and_search():
    http, *_ = client_for()
    page = http.get("/queue?kind=duplicate").text
    assert "Second Bellhaven of Owosso" in page and "Riverbend Manor" not in page
    page = http.get("/queue?q=coldwater").text
    assert "Bellhaven of Coldwater" in page and "Riverbend Manor" not in page


def test_approve_on_the_mock_writes_and_moves_to_the_next_pending():
    http, app, crm, conn = client_for()
    ps = by_kind(conn)
    chow, create = ps["chow"]["key"], ps["create"]["key"]
    r = http.post(f"/proposals/{chow}/approve", data=form(app, comment="SOP applies"), follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == f"/proposals/{create}?last={chow}&outcome=applied"
    assert [w[0] for w in crm.writes] == ["POST", "PATCH"]
    assert store.get(conn, chow)["comment"] == "SOP applies"
    page = http.get(r.headers["location"]).text
    assert "Written" in page and "The CRM stored exactly what was sent." in page


def test_the_last_decision_goes_back_to_the_queue():
    http, app, crm, conn = client_for()
    for p in by_kind(conn).values():
        r = http.post(f"/proposals/{p['key']}/approve", data=form(app), follow_redirects=False)
    assert r.headers["location"].startswith("/queue?last=")
    assert "No pending proposals" in http.get(r.headers["location"]).text


def test_live_approve_asks_for_confirmation_first():
    http, app, crm, conn = client_for(live=True)
    key = by_kind(conn)["create"]["key"]
    r = http.post(f"/proposals/{key}/approve", data=form(app, comment="new community"))
    assert r.status_code == 200
    assert "Write this to the live sandbox?" in r.text and "Creates 1 account that can't be deleted." in r.text
    assert 'name="confirmed" value="yes"' in r.text and 'value="new community"' in r.text
    assert crm.writes == [] and store.get(conn, key)["status"] == "pending"
    r = http.post(f"/proposals/{key}/approve", data=form(app, comment="new community", confirmed="yes"),
                  follow_redirects=False)
    assert r.status_code == 303 and [w[0] for w in crm.writes] == ["POST"]


def test_the_mock_ignores_confirmation():
    http, app, crm, conn = client_for()
    key = by_kind(conn)["create"]["key"]
    assert http.post(f"/proposals/{key}/approve", data=form(app), follow_redirects=False).status_code == 303
    assert [w[0] for w in crm.writes] == ["POST"]


def test_posts_without_the_token_or_from_another_site_are_refused():
    http, app, crm, conn = client_for()
    key = by_kind(conn)["update"]["key"]
    for path in (f"/proposals/{key}/approve", f"/proposals/{key}/reject"):
        assert http.post(path, data={"comment": "x"}).status_code == 403
        assert http.post(path, data={"csrf_token": "wrong"}).status_code == 403
        assert http.post(path, data=form(app), headers={"Origin": "https://evil.test"}).status_code == 403
        assert http.post(path, data=form(app), headers={"Referer": "https://evil.test/page"}).status_code == 403
    assert crm.writes == [] and store.get(conn, key)["status"] == "pending"
    ok = http.post(f"/proposals/{key}/reject", data=form(app), headers={"Origin": "http://127.0.0.1:8000"},
                   follow_redirects=False)
    assert ok.status_code == 303


def test_reject_is_recorded_and_guarded():
    http, app, crm, conn = client_for()
    key = by_kind(conn)["duplicate"]["key"]
    r = http.post(f"/proposals/{key}/reject", data=form(app, comment="separate building"), follow_redirects=False)
    assert r.status_code == 303 and "outcome=rejected" in r.headers["location"]
    assert store.get(conn, key)["status"] == "rejected"
    again = http.post(f"/proposals/{key}/reject", data=form(app), follow_redirects=False)
    assert "outcome=already_handled" in again.headers["location"]
    assert "Already handled" in http.get(again.headers["location"]).text
    assert crm.writes == []


def test_a_failed_write_shows_the_error_and_a_retry_button():
    http, app, crm, conn = client_for()
    key = by_kind(conn)["update"]["key"]

    def refuse(account_id, body):
        raise CrmError("PATCH /accounts/A1 -> 422: bad body")

    crm.update_account = refuse
    page = http.post(f"/proposals/{key}/approve", data=form(app)).text
    assert "Failed" in page and "422: bad body" in page and ">Retry</button>" in page
    assert "Last attempt failed at" in http.get(f"/proposals/{key}").text


def test_history_shows_each_written_field_newest_first():
    http, app, crm, conn = client_for()
    ps = by_kind(conn)
    for kind in ("update", "duplicate"):
        http.post(f"/proposals/{ps[kind]['key']}/approve", data=form(app))
    page = http.get("/history").text
    assert page.index("Second Bellhaven of Owosso") < page.index("Riverbend Manor")
    assert "Bellhaven of Chagrin Falls" in page and "Bellhaven of Owosso (A3)" in page
    assert "Riverbend Manor" in http.get("/history?q=a1").text
    assert "Second Bellhaven" not in http.get("/history?q=riverbend").text


def test_runs_page_and_queue_header_show_a_failed_run():
    http, app, crm, conn = client_for()
    store.fail_run(conn, store.start_run(conn), "CrmError: timeout")
    page = http.get("/runs").text
    assert "Failed: CrmError: timeout" in page and ">OK<" in page
    assert "Failed: CrmError: timeout" in http.get("/queue").text


def test_environment_badge_and_meridian_links():
    mock, *_ = client_for()
    page = mock.get("/queue").text
    assert "Mock CRM" in page and "LIVE SANDBOX" not in page and "/crm/" not in page
    live, app, crm, conn = client_for(live=True)
    page = live.get(f"/proposals/{by_kind(conn)['update']['key']}").text
    assert "LIVE SANDBOX" in page and f'href="{MERIDIAN}/accounts/A1"' in page


def test_the_token_never_appears_on_mock_pages(monkeypatch, tmp_path):
    monkeypatch.setenv("BH_API_TOKEN", "tok_do_not_show")
    monkeypatch.setattr(config, "USING_MOCK", True)
    monkeypatch.setattr(config, "API_BASE", "http://localhost:8001/api/v1")
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    http = TestClient(build())
    for path in ("/queue", "/history", "/runs"):
        assert "tok_do_not_show" not in http.get(path).text
