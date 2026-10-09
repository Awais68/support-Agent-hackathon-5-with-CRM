"""S4: /ws/tickets/{id} needs the ticket's tracking token (or the master key).

Before the fix anyone who knew or guessed a ticket UUID could subscribe and
receive every status change and new message on that ticket.
"""

from unittest.mock import MagicMock
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from api import tracking
from api.main import app

TICKET = UUID("11111111-2222-3333-4444-555555555555")
OTHER = UUID("11111111-2222-3333-4444-000000000000")
API_KEY = "test-master-key"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("API_KEY", API_KEY)
    monkeypatch.setattr(app.state, "db_pool", MagicMock(), raising=False)
    return TestClient(app)


def _rejected(client, url, headers=None) -> bool:
    # A rejected handshake raises on connect; getting a socket means accepted.
    try:
        with client.websocket_connect(url, headers=headers or {}):
            return False
    except WebSocketDisconnect as e:
        return e.code == 1008


def test_no_credential_is_rejected(client):
    assert _rejected(client, f"/ws/tickets/{TICKET}")


def test_wrong_token_is_rejected(client):
    assert _rejected(client, f"/ws/tickets/{TICKET}?token={'0' * 32}")


def test_other_tickets_token_is_rejected(client):
    token = tracking.tracking_token(OTHER)
    assert _rejected(client, f"/ws/tickets/{TICKET}?token={token}")


def test_wrong_api_key_is_rejected(client):
    assert _rejected(client, f"/ws/tickets/{TICKET}", headers={"X-API-Key": "nope"})


def test_tracking_token_is_accepted(client):
    token = tracking.tracking_token(TICKET)
    with client.websocket_connect(f"/ws/tickets/{TICKET}?token={token}") as ws:
        ws.send_text("ping")


def test_master_key_header_is_accepted(client):
    with client.websocket_connect(f"/ws/tickets/{TICKET}", headers={"X-API-Key": API_KEY}) as ws:
        ws.send_text("ping")
