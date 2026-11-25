import json

import pytest

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
