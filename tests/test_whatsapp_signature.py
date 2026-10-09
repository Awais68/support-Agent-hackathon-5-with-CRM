"""S10: the WhatsApp webhook must fail closed and verify against the public URL.

`REQUIRE_TWILIO_SIGNATURE` defaulted to false, so a deployment without
TWILIO_AUTH_TOKEN accepted forged inbound messages from any phone number,
i.e. as any customer. And the signature was checked against `request.url`,
which behind TLS termination is not the URL Twilio signed, so with the token
set every real webhook was rejected.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from twilio.request_validator import RequestValidator

from api.main import app
from api.rate_limiter import limiter
from channels.whatsapp_handler import WhatsAppHandler

URL = "/webhooks/whatsapp"
PUBLIC_URL = "https://support.example.com/webhooks/whatsapp"
TOKEN = "test-twilio-token"
FORM = {"From": "whatsapp:+15550001111", "Body": "I am someone else", "MessageSid": "SM1"}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(app.state, "kafka_producer", MagicMock(), raising=False)
    monkeypatch.setattr(app.state, "db_pool", MagicMock(), raising=False)
    handled = AsyncMock()
    monkeypatch.setattr(WhatsAppHandler, "handle_incoming_message", handled)
    for name in (
        "TWILIO_AUTH_TOKEN",
        "REQUIRE_TWILIO_SIGNATURE",
        "TWILIO_WHATSAPP_WEBHOOK_URL",
        "TWILIO_WEBHOOK_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    limiter.reset()
    c = TestClient(app, raise_server_exceptions=False)
    c.handled = handled
    return c


def _sign(url: str, form: dict[str, str]) -> str:
    return RequestValidator(TOKEN).compute_signature(url, form)


def test_unsigned_message_is_refused_without_a_token(client):
    # Exploit: no token configured, nothing set -> forged message was accepted.
    resp = client.post(URL, data=FORM)
    assert resp.status_code == 403
    client.handled.assert_not_awaited()


def test_explicit_opt_out_still_allows_local_unsigned_testing(client, monkeypatch):
    monkeypatch.setenv("REQUIRE_TWILIO_SIGNATURE", "false")
    resp = client.post(URL, data=FORM)
    assert resp.status_code == 200
    client.handled.assert_awaited_once()


def test_forged_signature_is_refused(client, monkeypatch):
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", TOKEN)
    resp = client.post(URL, data=FORM, headers={"X-Twilio-Signature": "forged"})
    assert resp.status_code == 403
    client.handled.assert_not_awaited()


def test_signature_for_the_public_url_is_accepted_behind_a_proxy(client, monkeypatch):
    # Twilio signs the public https URL; the app sees http://testserver/...
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", TOKEN)
    monkeypatch.setenv("TWILIO_WHATSAPP_WEBHOOK_URL", PUBLIC_URL)
    resp = client.post(URL, data=FORM, headers={"X-Twilio-Signature": _sign(PUBLIC_URL, FORM)})
    assert resp.status_code == 200
    client.handled.assert_awaited_once()


def test_signature_for_another_url_is_refused_when_the_override_is_set(client, monkeypatch):
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", TOKEN)
    monkeypatch.setenv("TWILIO_WHATSAPP_WEBHOOK_URL", PUBLIC_URL)
    sig = _sign("http://testserver/webhooks/whatsapp", FORM)
    resp = client.post(URL, data=FORM, headers={"X-Twilio-Signature": sig})
    assert resp.status_code == 403
    client.handled.assert_not_awaited()
