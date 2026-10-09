"""N5: a human agent's reply is published to notifications.outbound.

/tickets/{id}/reply only wrote the DB and the WebSocket, so email and
WhatsApp customers never received it.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from api.main import app

KEY = {"X-API-Key": "test-key-12345"}
TICKET_ID = UUID("11111111-2222-3333-4444-555555555555")
MESSAGE_ID = UUID("aaaaaaaa-0000-0000-0000-000000000001")


@pytest.fixture
def client(monkeypatch):
    producer = MagicMock()
    producer.send_message = AsyncMock(return_value="msg")
    monkeypatch.setattr(app.state, "db_pool", MagicMock(), raising=False)
    monkeypatch.setattr(app.state, "kafka_producer", producer, raising=False)
    ticket = {
        "id": TICKET_ID,
        "customer_id": UUID("99999999-0000-0000-0000-000000000000"),
        "channel": "email",
        "customer_email": "jane@example.com",
    }
    monkeypatch.setattr("api.main.db.get_ticket", AsyncMock(return_value=ticket))
    monkeypatch.setattr(
        "api.main.db.add_message",
        AsyncMock(return_value={"id": MESSAGE_ID, "created_at": datetime.now(UTC)}),
    )
    c = TestClient(app, raise_server_exceptions=False)
    c.producer = producer
    return c


def test_reply_is_published_to_the_outbound_topic(client):
    resp = client.post(f"/tickets/{TICKET_ID}/reply", json={"message": "Fixed it."}, headers=KEY)
    assert resp.status_code == 201, resp.text
    assert resp.json()["delivery"] == "queued"

    topic, payload = client.producer.send_message.await_args.args[:2]
    assert topic == "notifications.outbound"
    assert payload["ticket_id"] == str(TICKET_ID)
    assert payload["channel"] == "email"
    assert payload["customer_email"] == "jane@example.com"
    assert payload["customer_reply"] == "Fixed it."
    assert payload["reply_message_id"] == str(MESSAGE_ID)
    assert payload["source"] == "human"
    assert "agent_run_id" not in payload


def test_publish_failure_is_reported_not_swallowed(client):
    client.producer.send_message.side_effect = RuntimeError("broker down")
    resp = client.post(f"/tickets/{TICKET_ID}/reply", json={"message": "Fixed it."}, headers=KEY)
    assert resp.status_code == 503
    assert str(MESSAGE_ID) in resp.text
