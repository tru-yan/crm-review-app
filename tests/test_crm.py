import json

import pytest
import requests

from bellhaven.crm import CrmClient, CrmError


class FakeResponse:
    def __init__(self, body, status=200):
        self.body, self.status_code, self.text = body, status, json.dumps(body)

    def json(self):
        return self.body


class FakeSession:
    def __init__(self, responses):
        self.headers, self.calls, self.responses = {}, [], list(responses)

    def request(self, method, url, timeout, params=None, json=None):
        self.calls.append((method, url, params, json))
        return self.responses.pop(0)


def page(ids, total):
    return FakeResponse({"data": [{"account_id": str(i)} for i in ids], "page": 1, "page_size": 50, "total": total})


def test_list_accounts_follows_pages_and_sends_token():
    s = FakeSession([page(range(50), 60), page(range(50, 60), 60)])
    accounts = CrmClient(token="t", base="https://api.test", session=s).list_accounts()
    assert len(accounts) == 60
    assert s.calls[1][2]["page"] == 2
    assert s.headers["Authorization"] == "Bearer t"


def test_errors_raise_with_status_and_body():
    s = FakeSession([FakeResponse({"detail": "bad status"}, status=422)])
    with pytest.raises(CrmError, match="422"):
        CrmClient(token="t", base="https://api.test", session=s).update_account("A1", {"status": "Nope"})


def client(session, token="t"):
    return CrmClient(token=token, base="https://api.test", session=session)


def test_empty_crm_returns_no_records():
    assert client(FakeSession([page([], 0)])).list_accounts() == []


def test_exact_multiple_of_page_size_stops_without_an_extra_request():
    s = FakeSession([page(range(50), 100), page(range(50, 100), 100)])
    assert len(client(s).list_accounts()) == 100
    assert len(s.calls) == 2


def test_partial_read_raises_instead_of_returning_too_few():
    s = FakeSession([page(range(50), 60), page([], 60)])
    with pytest.raises(CrmError, match="read 50 records"):
        client(s).list_accounts()


def test_network_error_becomes_crm_error():
    class Broken(FakeSession):
        def request(self, *args, **kwargs):
            raise requests.ConnectionError("boom")
    with pytest.raises(CrmError, match="ConnectionError"):
        client(Broken([])).get_account("A1")


def test_error_message_has_status_and_body_but_not_the_token():
    s = FakeSession([FakeResponse({"detail": "bad status"}, status=422)])
    with pytest.raises(CrmError) as err:
        client(s, token="secret-token").update_account("A1", {"status": "Nope"})
    assert "422" in str(err.value) and "bad status" in str(err.value)
    assert "secret-token" not in str(err.value)


def test_write_methods_send_the_right_requests():
    s = FakeSession([FakeResponse({"account_id": "N1"}, 201), FakeResponse({"account_id": "A1"})])
    c = client(s)
    c.create_account({"name": "X"})
    c.update_account("A1", {"note": "n"})
    assert [(m, u, j) for m, u, _, j in s.calls] == [
        ("POST", "https://api.test/accounts", {"name": "X"}),
        ("PATCH", "https://api.test/accounts/A1", {"note": "n"}),
    ]
