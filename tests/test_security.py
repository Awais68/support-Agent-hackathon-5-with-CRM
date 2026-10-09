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
