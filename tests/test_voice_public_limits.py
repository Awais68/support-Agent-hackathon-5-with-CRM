"""S7: the voice endpoint is public but capped; the web form holds no key.

The /api/voice proxy attached the master API key to any browser request and
forwarded bodies of any size, so anyone could loop large audio through STT
and the LLM on the operator's key (and the key sat in the web-form
container). The backend endpoint is now reachable without a key, rejects
bodies over the audio cap before parsing them, and only accepts audio_url
(a server-side fetch) from key holders.
"""

import base64
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from api.main import app

KEY = {"X-API-Key": "test-key-12345"}
URL = "/webhooks/voice/message"


@pytest.fixture
def client(monkeypatch):
    for attr in ("db_pool", "kafka_producer", "openai_client"):
        monkeypatch.setattr(app.state, attr, MagicMock(), raising=False)
    handler = MagicMock()
    handler.resolve_audio = AsyncMock(return_value=b"audio")
    result = MagicMock()
    result.to_dict.return_value = {"transcription": "hi"}
    handler.handle_voice_message = AsyncMock(return_value=result)
    monkeypatch.setattr("api.main.VoiceHandler", MagicMock(return_value=handler))
    monkeypatch.setenv("VOICE_MAX_AUDIO_BYTES", "3000")
    c = TestClient(app, raise_server_exceptions=False)
    c.handler = handler
    return c


def _audio(n: int) -> str:
    return base64.b64encode(b"\0" * n).decode("ascii")


def test_oversized_body_is_rejected_before_processing(client):
    resp = client.post(URL, json={"audio_base64": _audio(50_000)}, headers=KEY)
    assert resp.status_code == 413
    client.handler.resolve_audio.assert_not_awaited()


def test_oversized_body_without_content_length_is_rejected(client):
    body = ('{"audio_base64": "' + _audio(50_000) + '"}').encode()

    def chunks():
        yield body

    resp = client.post(URL, content=chunks(), headers={"Content-Type": "application/json", **KEY})
    assert resp.status_code in (411, 413)
    client.handler.resolve_audio.assert_not_awaited()


def test_small_audio_is_accepted_without_a_key(client):
    resp = client.post(URL, json={"audio_base64": _audio(1000)})
    assert resp.status_code == 200, resp.text


def test_audio_url_needs_the_api_key(client):
    resp = client.post(URL, json={"audio_url": "https://example.com/a.wav"})
    assert resp.status_code in (400, 401, 403)
    client.handler.resolve_audio.assert_not_awaited()


def test_audio_url_still_works_with_the_key(client):
    resp = client.post(URL, json={"audio_url": "https://example.com/a.wav"}, headers=KEY)
    assert resp.status_code == 200, resp.text
