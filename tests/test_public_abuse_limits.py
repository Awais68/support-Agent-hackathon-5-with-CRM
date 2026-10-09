"""S8: the public, LLM-triggering web form is capped in size, total and per client.

`/webhooks/webform` needs no key and every accepted submission costs LLM
calls. The audit showed:
- a 2 MB body was read, parsed and echoed back in the 422;
- the only limit was 10/min per TCP peer, with no global budget, so many
  sources (or one source behind several IPs) could spend without bound;
- behind the web form proxy every browser shared the proxy's bucket, while a
  direct caller could not be told apart from a proxy.
"""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from api.main import app
from api.rate_limiter import limiter

URL = "/webhooks/webform"
MARKER = "ECHO-MARKER-"


def _form(i: int = 0) -> dict[str, str]:
    return {
        "name": "Abuse Test",
        "email": f"abuse{i}@example.com",
        "subject": "Spend test",
        "message": "Please help me with my account settings.",
    }


@pytest.fixture
def make_client(monkeypatch):
    monkeypatch.setattr(app.state, "db_pool", MagicMock(), raising=False)
    monkeypatch.setattr(app.state, "kafka_producer", None, raising=False)
    monkeypatch.setattr(app.state, "kafka_enabled", False, raising=False)
    monkeypatch.setattr(app.state, "openai_client", None, raising=False)
    created = AsyncMock(
        side_effect=lambda *a, **k: {
            "id": uuid4(),
            "ticket_number": "TF-1",
            "customer_id": uuid4(),
        }
    )
    monkeypatch.setattr("api.main.db.create_ticket", created)
    monkeypatch.delenv("TRUSTED_PROXIES", raising=False)
    monkeypatch.delenv("PUBLIC_LLM_BUDGET_PER_HOUR", raising=False)
    limiter.reset()

    def make(peer: str = "203.0.113.7") -> TestClient:
        return TestClient(app, raise_server_exceptions=False, client=(peer, 40000))

    yield make
    limiter.reset()


def test_oversized_body_is_refused_before_parsing(make_client):
    body = {**_form(), "message": MARKER + "x" * (2 * 1024 * 1024)}
    resp = make_client().post(URL, json=body)
    assert resp.status_code == 413
    assert MARKER not in resp.text


def test_validation_errors_do_not_echo_the_input(make_client):
    body = {**_form(), "message": MARKER + "x" * 1500}
    resp = make_client().post(URL, json=body)
    assert resp.status_code == 422
    assert MARKER not in resp.text


def test_global_budget_caps_submissions_across_clients(make_client, monkeypatch):
    monkeypatch.setenv("PUBLIC_LLM_BUDGET_PER_HOUR", "3")
    codes = [make_client(f"198.51.100.{i}").post(URL, json=_form(i)).status_code for i in range(4)]
    assert codes == [201, 201, 201, 429]


def test_spoofed_forwarded_for_from_an_untrusted_peer_is_ignored(make_client):
    # Exploit: rotating X-Forwarded-For must not give a direct caller new buckets.
    client = make_client("203.0.113.7")
    codes = [
        client.post(URL, json=_form(i), headers={"X-Forwarded-For": f"192.0.2.{i}"}).status_code
        for i in range(11)
    ]
    assert codes[-1] == 429


def test_clients_behind_a_trusted_proxy_get_their_own_bucket(make_client, monkeypatch):
    # Before: everyone behind the web form proxy shared the proxy's bucket.
    monkeypatch.setenv("TRUSTED_PROXIES", "10.0.0.0/8")
    proxy = make_client("10.0.0.5")
    codes = [
        proxy.post(URL, json=_form(i), headers={"X-Forwarded-For": f"192.0.2.{i}"}).status_code
        for i in range(11)
    ]
    assert codes == [201] * 11
