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
        signature = RequestValidator(TWILIO_TOKEN).compute_signature(VOICE_CALL_URL, CALL_PARAMS)
        resp = infra_client.post(
            "/webhooks/voice/call",
            data=CALL_PARAMS,
            headers={"X-Twilio-Signature": signature},
        )
        # Empty SpeechResult -> greeting TwiML.
        assert resp.status_code == 200
        assert "<Gather" in resp.text


class TestVoiceMessageAuth:
    def test_audio_url_requires_api_key(self, infra_client):
        # AUDIT S7: the endpoint is public (the browser calls it through a
        # key-less proxy) and capped; the server-side fetch stays key-only.
        # Size caps are covered in tests/test_voice_public_limits.py.
        resp = infra_client.post(
            "/webhooks/voice/message", json={"audio_url": "https://example.com/a.wav"}
        )
        assert resp.status_code == 403

    def test_internal_audio_url_rejected(self, infra_client):
        resp = infra_client.post(
            "/webhooks/voice/message",
            json={"audio_url": "http://169.254.169.254/latest/meta-data/"},
            headers={"X-API-Key": "test-key-12345"},
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# SSRF guard for audio_url
# ---------------------------------------------------------------------------
import httpx  # noqa: E402

from channels.voice_handler import VoiceHandler  # noqa: E402
from utils import safe_fetch  # noqa: E402


def _fake_dns(monkeypatch, addr: str):
    async def fake_getaddrinfo(self, host, port, **kwargs):
        return [(2, 1, 6, "", (addr, port))]

    monkeypatch.setattr("asyncio.base_events.BaseEventLoop.getaddrinfo", fake_getaddrinfo)


def _transport_never_called():
    def handler(request):  # pragma: no cover - failing here is the point
        raise AssertionError(f"unexpected outbound request to {request.url}")

    return httpx.MockTransport(handler)


class TestAudioUrlSsrf:
    @pytest.mark.parametrize(
        "url",
        [
            "http://169.254.169.254/latest/meta-data/",  # cloud metadata, http
            "https://169.254.169.254/latest/meta-data/",  # IP literal
            "https://127.0.0.1/audio.wav",
            "https://localhost/audio.wav",  # not allowlisted
            "https://api:8000/health",  # internal service name
            "https://user:pw@api.twilio.com/x.wav",  # embedded credentials
            "file:///etc/passwd",
        ],
    )
    async def test_internal_or_unlisted_urls_rejected(self, url):
        with pytest.raises(safe_fetch.UnsafeURLError):
            await safe_fetch.fetch_bytes(url, max_bytes=1024, transport=_transport_never_called())

    @pytest.mark.parametrize(
        "addr",
        ["10.0.0.5", "127.0.0.1", "169.254.169.254", "::1", "::ffff:192.168.1.1"],
    )
    async def test_allowlisted_host_resolving_to_private_ip_rejected(self, monkeypatch, addr):
        _fake_dns(monkeypatch, addr)
        with pytest.raises(safe_fetch.UnsafeURLError, match="non-public"):
            await safe_fetch.fetch_bytes(
                "https://api.twilio.com/Recordings/RE1.wav",
                max_bytes=1024,
                transport=_transport_never_called(),
            )

    async def test_redirect_to_internal_host_rejected(self, monkeypatch):
        _fake_dns(monkeypatch, "54.172.60.1")
        seen = []

        def handler(request):
            seen.append(str(request.url))
            return httpx.Response(302, headers={"location": "http://169.254.169.254/"})

        with pytest.raises(safe_fetch.UnsafeURLError):
            await safe_fetch.fetch_bytes(
                "https://api.twilio.com/Recordings/RE1.wav",
                max_bytes=1024,
                transport=httpx.MockTransport(handler),
            )
        assert seen == ["https://api.twilio.com/Recordings/RE1.wav"]

    async def test_oversized_body_rejected(self, monkeypatch):
        _fake_dns(monkeypatch, "54.172.60.1")
        transport = httpx.MockTransport(lambda r: httpx.Response(200, content=b"x" * 2048))
        with pytest.raises(safe_fetch.UnsafeURLError, match="size"):
            await safe_fetch.fetch_bytes(
                "https://api.twilio.com/Recordings/RE1.wav",
                max_bytes=1024,
                transport=transport,
            )

    async def test_allowlisted_public_url_with_cdn_redirect_is_fetched(self, monkeypatch):
        _fake_dns(monkeypatch, "54.172.60.1")

        def handler(request):
            if request.url.host == "api.twilio.com":
                return httpx.Response(
                    307, headers={"location": "https://media.twiliocdn.com/a.wav"}
                )
            return httpx.Response(200, content=b"RIFFdata")

        body = await safe_fetch.fetch_bytes(
            "https://api.twilio.com/Recordings/RE1.wav",
            max_bytes=1024,
            transport=httpx.MockTransport(handler),
        )
        assert body == b"RIFFdata"

    async def test_resolve_audio_returns_none_for_internal_url(self):
        assert await VoiceHandler.resolve_audio(audio_url="http://localhost:5432/") is None

    def test_host_allowlist_is_env_configurable(self, monkeypatch):
        monkeypatch.setenv("AUDIO_URL_ALLOWED_HOSTS", "*.example-cdn.com")
        patterns = safe_fetch.allowed_hosts()
        assert safe_fetch.host_is_allowed("media.example-cdn.com", patterns)
        assert not safe_fetch.host_is_allowed("example-cdn.com.evil.io", patterns)
        assert not safe_fetch.host_is_allowed("api.twilio.com", patterns)
