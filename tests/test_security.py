"""Security regression tests: CORS preflight, API key handling."""

import pytest
from fastapi.testclient import TestClient

from api.main import app, lifespan
from exceptions import ConfigurationError

ALLOWED_ORIGIN = "http://localhost:3000"


@pytest.fixture
def client():
    # No context manager: middleware runs without the DB/Kafka lifespan.
    return TestClient(app)


class TestCorsPreflight:
    def test_preflight_from_allowed_origin_is_2xx_with_cors_headers(self, client):
        resp = client.options(
            "/tickets/00000000-0000-0000-0000-000000000000",
            headers={
                "Origin": ALLOWED_ORIGIN,
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "x-api-key",
            },
        )
        assert 200 <= resp.status_code < 300
        assert resp.headers["access-control-allow-origin"] == ALLOWED_ORIGIN
        assert "GET" in resp.headers["access-control-allow-methods"]
        assert "x-api-key" in resp.headers["access-control-allow-headers"].lower()

    def test_preflight_from_unknown_origin_gets_no_allow_origin(self, client):
        resp = client.options(
            "/webhooks/webform",
            headers={
                "Origin": "https://evil.example",
                "Access-Control-Request-Method": "POST",
            },
        )
        assert "access-control-allow-origin" not in resp.headers

    def test_401_still_carries_cors_headers(self, client):
        resp = client.get(
            "/tickets/00000000-0000-0000-0000-000000000000",
            headers={"Origin": ALLOWED_ORIGIN},
        )
        assert resp.status_code == 401
        assert resp.headers["access-control-allow-origin"] == ALLOWED_ORIGIN


class TestApiKey:
    def test_wrong_key_rejected(self, client):
        resp = client.get(
            "/tickets/00000000-0000-0000-0000-000000000000",
            headers={"X-API-Key": "wrong"},
        )
        assert resp.status_code == 401

    def test_no_configured_key_fails_closed(self, client, monkeypatch):
        monkeypatch.delenv("API_KEY", raising=False)
        monkeypatch.delenv("API_KEY_SECRET", raising=False)
        # The old hardcoded fallback must no longer be accepted.
        resp = client.get(
            "/tickets/00000000-0000-0000-0000-000000000000",
            headers={"X-API-Key": "test-key-12345"},
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_startup_fails_without_key_outside_test_mode(self, monkeypatch):
        monkeypatch.delenv("API_KEY", raising=False)
        monkeypatch.delenv("API_KEY_SECRET", raising=False)
        monkeypatch.setenv("RUN_MODE", "api")
        with pytest.raises(ConfigurationError):
            async with lifespan(app):
                pass


# ---------------------------------------------------------------------------
# Voice webhooks: Twilio signature on /voice/call, API key on /voice/message
# ---------------------------------------------------------------------------
TWILIO_TOKEN = "test-twilio-auth-token"
VOICE_CALL_URL = "http://testserver/webhooks/voice/call"
CALL_PARAMS = {"From": "+15550001111", "SpeechResult": "", "CallSid": "CA123"}


@pytest.fixture
def infra_client(monkeypatch):
    """Client with mocked app.state infra so handlers get past their checks."""
    from unittest.mock import AsyncMock, MagicMock

    for name in ("db_pool", "openai_client"):
        monkeypatch.setattr(app.state, name, MagicMock(), raising=False)
    producer = MagicMock()
    producer.send_message = AsyncMock(return_value="msg")
    monkeypatch.setattr(app.state, "kafka_producer", producer, raising=False)
    return TestClient(app, raise_server_exceptions=False)


class TestVoiceCallSignature:
    def test_missing_signature_is_403(self, infra_client, monkeypatch):
        monkeypatch.setenv("TWILIO_AUTH_TOKEN", TWILIO_TOKEN)
        resp = infra_client.post("/webhooks/voice/call", data=CALL_PARAMS)
        assert resp.status_code == 403

    def test_bad_signature_is_403(self, infra_client, monkeypatch):
        monkeypatch.setenv("TWILIO_AUTH_TOKEN", TWILIO_TOKEN)
        resp = infra_client.post(
            "/webhooks/voice/call",
            data=CALL_PARAMS,
            headers={"X-Twilio-Signature": "forged"},
        )
        assert resp.status_code == 403

    def test_no_auth_token_configured_fails_closed(self, infra_client, monkeypatch):
        monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)
        resp = infra_client.post(
            "/webhooks/voice/call",
            data=CALL_PARAMS,
            headers={"X-Twilio-Signature": "anything"},
        )
        assert resp.status_code == 403

    def test_valid_signature_is_accepted(self, infra_client, monkeypatch):
        from twilio.request_validator import RequestValidator

        monkeypatch.setenv("TWILIO_AUTH_TOKEN", TWILIO_TOKEN)
        signature = RequestValidator(TWILIO_TOKEN).compute_signature(
            VOICE_CALL_URL, CALL_PARAMS
        )
        resp = infra_client.post(
            "/webhooks/voice/call",
            data=CALL_PARAMS,
            headers={"X-Twilio-Signature": signature},
        )
        # Empty SpeechResult -> greeting TwiML.
        assert resp.status_code == 200
        assert "<Gather" in resp.text


class TestVoiceMessageAuth:
    def test_requires_api_key(self, infra_client):
        resp = infra_client.post(
            "/webhooks/voice/message", json={"audio_base64": "AAAA"}
        )
        assert resp.status_code == 401
