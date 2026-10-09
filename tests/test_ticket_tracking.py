"""S1/S13: customer ticket tracking without the master API key.

The web-form proxy used to forward `/api/tickets/<id>` to the API with the
master key, so `..%2F` reached every authenticated GET. The customer view now
goes through a public endpoint that needs a per-ticket tracking token (or the
ticket's email) and returns a redacted ticket.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from api import tracking
from api.main import app

TICKET_UUID = UUID("11111111-2222-3333-4444-555555555555")
TICKET_NUMBER = "TKT-20261009-ABC123"
NOW = datetime(2026, 10, 9, tzinfo=UTC)

TICKET_ROW = {
    "id": TICKET_UUID,
    "ticket_number": TICKET_NUMBER,
    "customer_id": UUID("99999999-0000-0000-0000-000000000000"),
    "subject": "Login broken",
    "category": "technical",
    "priority": "high",
    "status": "open",
    "channel": "webform",
    "created_at": NOW,
    "updated_at": NOW,
    "resolved_at": None,
    "assigned_to": "agent-7",
    "customer_email": "Alice@AcmeCorp.com",
    "customer_name": "Alice",
    "tier": "enterprise",
}
MESSAGES = [
    {
        "id": UUID("aaaaaaaa-0000-0000-0000-000000000001"),
        "direction": "inbound",
        "content": "I cannot log in",
        "channel": "webform",
        "created_at": NOW,
        "sentiment_score": -0.4,
        "metadata": {"internal": "x"},
    },
    {
        "id": UUID("aaaaaaaa-0000-0000-0000-000000000002"),
        "direction": "outbound",
        "content": "Try resetting your password",
        "channel": "webform",
        "created_at": NOW,
        "sentiment_score": None,
        "metadata": {},
    },
]


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(app.state, "db_pool", MagicMock(), raising=False)

    async def by_number(_pool, number):
        return dict(TICKET_ROW) if number == TICKET_NUMBER else None

    monkeypatch.setattr("api.main.db.get_ticket_by_number", by_number)
    monkeypatch.setattr("api.main.db.get_ticket_messages", AsyncMock(return_value=MESSAGES))
    runs = AsyncMock(return_value=[{"prompt": "SYSTEM PROMPT"}])
    monkeypatch.setattr("api.main.db.get_agent_runs", runs)
    return TestClient(app, raise_server_exceptions=False)


def url(number: str = TICKET_NUMBER) -> str:
    return f"/public/tickets/{number}"


class TestTrackingToken:
    def test_token_round_trips(self):
        token = tracking.tracking_token(TICKET_UUID)
        assert tracking.verify_tracking_token(TICKET_UUID, token)

    def test_token_is_per_ticket(self):
        token = tracking.tracking_token(TICKET_UUID)
        other = UUID("11111111-2222-3333-4444-000000000000")
        assert not tracking.verify_tracking_token(other, token)

    def test_no_secret_fails_closed(self, monkeypatch):
        monkeypatch.delenv("TRACKING_TOKEN_SECRET", raising=False)
        monkeypatch.delenv("API_KEY", raising=False)
        monkeypatch.delenv("API_KEY_SECRET", raising=False)
        assert tracking.tracking_token(TICKET_UUID) is None
        assert not tracking.verify_tracking_token(TICKET_UUID, "anything")


class TestPublicTicketEndpoint:
    def test_no_credential_is_404(self, client):
        assert client.get(url()).status_code == 404

    def test_wrong_token_is_404(self, client):
        assert client.get(url(), headers={"X-Tracking-Token": "0" * 32}).status_code == 404

    def test_wrong_email_is_404(self, client):
        resp = client.get(url(), params={"email": "mallory@evil.example"})
        assert resp.status_code == 404

    def test_unknown_ticket_is_404(self, client):
        token = tracking.tracking_token(TICKET_UUID)
        resp = client.get(url("TKT-20261009-FFFFFF"), headers={"X-Tracking-Token": token})
        assert resp.status_code == 404

    def test_malformed_ticket_number_is_rejected(self, client):
        for bad in ("..%2Ftickets", "%2E%2E%2Fmetrics", "abc", "TKT-1-%00"):
            # Decoded "../" leaves the public prefix and hits the key check (401).
            assert client.get(url(bad)).status_code in (400, 401, 404), bad

    def test_valid_token_returns_redacted_view(self, client):
        token = tracking.tracking_token(TICKET_UUID)
        resp = client.get(url(), headers={"X-Tracking-Token": token})
        assert resp.status_code == 200
        body = resp.json()
        assert body["ticket_number"] == TICKET_NUMBER
        assert body["ticket_id"] == str(TICKET_UUID)
        assert body["tracking_token"] == token
        for leaked in (
            "agent_runs",
            "customer_email",
            "customer_id",
            "customer_name",
            "tier",
            "assigned_to",
        ):
            assert leaked not in body, leaked
        assert [m["sender_type"] for m in body["messages"]] == ["customer", "agent"]
        for m in body["messages"]:
            assert "metadata" not in m and "sentiment_score" not in m

    def test_token_in_query_string_is_accepted(self, client):
        token = tracking.tracking_token(TICKET_UUID)
        assert client.get(url(), params={"token": token}).status_code == 200

    def test_matching_email_is_case_insensitive(self, client):
        resp = client.get(url(), params={"email": "  alice@acmecorp.COM "})
        assert resp.status_code == 200
        assert resp.json()["tracking_token"] == tracking.tracking_token(TICKET_UUID)

    def test_master_key_routes_still_need_the_key(self, client):
        assert client.get(f"/tickets/{TICKET_NUMBER}").status_code == 401
