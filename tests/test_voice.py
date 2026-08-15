"""Unit tests for the voice channel (STT, TTS, translation, orchestration).

These tests mock all external services (Groq/OpenAI/OpenRouter/gTTS) so they
run with zero infrastructure.
"""

import base64
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from channels.voice_handler import (
    Transcription,
    VoiceHandler,
    twilio_language,
)
from kafka_client import INBOUND_VOICE_TOPIC, create_inbound_voice_message


@pytest.fixture
def handler():
    kafka = MagicMock()
    kafka.send_message = AsyncMock(return_value="msg-1")
    openai = MagicMock()
    return VoiceHandler(kafka, openai_client=openai)


# ---------------------------------------------------------------------------
# STT / confidence gate ("pick it up first time")
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_transcribe_low_confidence_triggers_clarification(handler):
    with patch.object(handler, "transcribe", new=AsyncMock()) as mock_t:
        mock_t.return_value = Transcription(
            text="blah blah",
            language="en",
            confidence=0.2,  # below threshold
            needs_clarification=True,
        )
        result = await handler.handle_voice_message(
            audio_bytes=b"fake-audio",
            run_agent=AsyncMock(),
        )

    assert result.needs_clarification is True
    assert "Did you say" in result.clarification_message
    mock_t.assert_awaited_once()


@pytest.mark.asyncio
async def test_transcribe_high_confidence_runs_agent(handler):
    with (
        patch.object(handler, "transcribe", new=AsyncMock()) as mock_stt,
        patch.object(handler, "detect_and_translate", new=AsyncMock()) as mock_tr,
        patch.object(handler, "synthesize", new=AsyncMock()) as mock_tts,
    ):
        mock_stt.return_value = Transcription(
            text="I need help resetting my password",
            language="en",
            confidence=0.98,
            needs_clarification=False,
        )
        mock_tr.return_value = ("I need help resetting my password", "en", False)
        mock_tts.return_value = ("AUDIOBASE64", "mp3")

        agent = AsyncMock(return_value={
            "response": "I can help with that. Check your email for a reset link.",
            "ticket_id": "11111111-1111-1111-1111-111111111111",
            "ticket_number": "TKT-20260101-0001",
        })

        result = await handler.handle_voice_message(
            audio_bytes=b"fake-audio",
            name="Alice",
            email="alice@example.com",
            run_agent=agent,
        )

    assert result.needs_clarification is False
    assert result.transcript == "I need help resetting my password"
    assert result.translated_to_english == "I need help resetting my password"
    assert result.agent_response.startswith("I can help")
    assert result.audio_base64 == "AUDIOBASE64"
    agent.assert_awaited_once_with("I need help resetting my password")
    handler.kafka_producer.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_non_english_round_trip_translation(handler):
    """Customer speaks Urdu → agent sees English → reply spoken in Urdu."""
    with (
        patch.object(handler, "transcribe", new=AsyncMock()) as mock_stt,
        patch.object(handler, "detect_and_translate", new=AsyncMock()) as mock_tr,
        patch.object(handler, "translate_to_language", new=AsyncMock()) as mock_tr_back,
        patch.object(handler, "synthesize", new=AsyncMock()) as mock_tts,
    ):
        mock_stt.return_value = Transcription(
            text="میں اپنا پاس ورڈ ری سیٹ کرنا چاہتا ہوں",
            language="ur",
            confidence=0.96,
            needs_clarification=False,
        )
        mock_tr.return_value = ("I want to reset my password", "ur", True)
        mock_tr_back.return_value = "میں آپ کی مدد کروں گا۔"
        mock_tts.return_value = ("AUDIO", "mp3")

        agent = AsyncMock(return_value={
            "response": "I will help you reset it.",
            "ticket_id": "22222222-2222-2222-2222-222222222222",
            "ticket_number": "TKT-20260101-0002",
        })

        result = await handler.handle_voice_message(
            audio_bytes=b"fake-audio",
            phone="+923001234567",
            run_agent=agent,
        )

    # Agent received the English translation
    agent.assert_awaited_once_with("I want to reset my password")
    # Reply was translated back into the customer's language before TTS
    mock_tr_back.assert_awaited_once()
    assert mock_tr_back.call_args.args[0] == "I will help you reset it."
    assert mock_tr_back.call_args.args[1] == "ur"
    assert result.response_language == "ur"
    assert result.audio_base64 == "AUDIO"


@pytest.mark.asyncio
async def test_handle_voice_message_no_agent_returns_transcription(handler):
    with (
        patch.object(handler, "transcribe", new=AsyncMock()) as mock_stt,
        patch.object(handler, "detect_and_translate", new=AsyncMock()) as mock_tr,
    ):
        mock_stt.return_value = Transcription(
            text="Hello there",
            language="en",
            confidence=0.9,
            needs_clarification=False,
        )
        mock_tr.return_value = ("Hello there", "en", False)

        result = await handler.handle_voice_message(audio_bytes=b"x", run_agent=None)

    assert result.transcript == "Hello there"
    assert result.agent_response == ""
    assert result.audio_base64 is None


@pytest.mark.asyncio
async def test_detect_and_translate_parses_llm_json(handler):
    mock_resp = MagicMock()
    mock_resp.choices[0].message.content = json.dumps(
        {"language": "hi", "translated": "I need billing help", "is_english": False}
    )
    handler.openai_client.chat.completions.create = AsyncMock(return_value=mock_resp)

    english, lang, was_translated = await handler.detect_and_translate(
        "मुझे बिलिंग में मदद चाहिए"
    )

    assert english == "I need billing help"
    assert lang == "hi"
    assert was_translated is True


@pytest.mark.asyncio
async def test_detect_and_translate_falls_back_without_client():
    h = VoiceHandler(kafka_producer=None, openai_client=None)
    english, lang, was_translated = await h.detect_and_translate("raw text")
    assert english == "raw text"
    assert lang == "en"
    assert was_translated is False


@pytest.mark.asyncio
async def test_translate_to_english_language_returns_unchanged(handler):
    result = await handler.translate_to_language("How can I help?", "en")
    assert result == "How can I help?"


# ---------------------------------------------------------------------------
# Audio helpers
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_resolve_audio_base64():
    payload = base64.b64encode(b"\x00\x01\x02").decode("ascii")
    result = await VoiceHandler.resolve_audio(audio_base64=payload)
    assert result == b"\x00\x01\x02"


@pytest.mark.asyncio
async def test_resolve_audio_invalid_base64():
    result = await VoiceHandler.resolve_audio(audio_base64="not-valid!!!")
    assert result is None


def test_twilio_language_mapping():
    assert twilio_language("ur") == "ur-PK"
    assert twilio_language("hi-IN") == "hi-IN"
    assert twilio_language("de") == "de-DE"
    assert twilio_language("unknown") == "en-US"
    assert twilio_language("") == "en-US"


# ---------------------------------------------------------------------------
# Kafka message builder
# ---------------------------------------------------------------------------
def test_create_inbound_voice_message():
    msg = create_inbound_voice_message(
        customer_phone="+923001234567",
        customer_name="Ali",
        message_body="I need help",
        language="en",
        confidence=0.95,
    )
    assert msg.topic == INBOUND_VOICE_TOPIC
    assert msg.headers == {"content_type": "voice"}
    assert msg.payload["language"] == "en"
    assert msg.payload["confidence"] == 0.95
    assert msg.payload["message_body"] == "I need help"


# ---------------------------------------------------------------------------
# API endpoint smoke tests (no real infra)
# ---------------------------------------------------------------------------
@pytest.fixture
def api_client():
    from api.main import app

    app.state.kafka_producer = MagicMock()
    app.state.kafka_producer.send_message = AsyncMock(return_value="msg")
    app.state.openai_client = MagicMock()
    return TestClient(app, raise_server_exceptions=False)


def test_voice_translate_endpoint(api_client):
    mock_resp = MagicMock()
    mock_resp.choices[0].message.content = json.dumps(
        {"language": "es", "translated": "I need help", "is_english": False}
    )
    api_client.app.state.openai_client.chat.completions.create = AsyncMock(
        return_value=mock_resp
    )

    resp = api_client.post(
        "/voice/translate",
        json={"text": "Necesito ayuda"},
        headers={"X-API-Key": "test-key-12345"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["translated"] == "I need help"
    assert data["language"] == "es"
    assert data["was_translated"] is True


def test_voice_translate_to_target_language(api_client):
    mock_resp = MagicMock()
    mock_resp.choices[0].message.content = "مرحبا بك"
    api_client.app.state.openai_client.chat.completions.create = AsyncMock(
        return_value=mock_resp
    )

    resp = api_client.post(
        "/voice/translate",
        json={"text": "Welcome", "target_language": "Arabic"},
        headers={"X-API-Key": "test-key-12345"},
    )
    assert resp.status_code == 200
    assert resp.json()["text"] == "مرحبا بك"


def test_voice_translate_requires_api_key(api_client):
    resp = api_client.post("/voice/translate", json={"text": "hello"})
    assert resp.status_code == 401


def test_voice_transcribe_endpoint_degraded(api_client):
    """Without STT keys the pipeline degrades gracefully (no 500)."""
    payload = base64.b64encode(b"fake-audio").decode("ascii")
    resp = api_client.post(
        "/voice/transcribe",
        json={"audio_base64": payload},
        headers={"X-API-Key": "test-key-12345"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["needs_clarification"] is True


def test_voice_transcribe_rejects_empty(api_client):
    resp = api_client.post(
        "/voice/transcribe",
        json={},
        headers={"X-API-Key": "test-key-12345"},
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "VALIDATION_ERROR"
