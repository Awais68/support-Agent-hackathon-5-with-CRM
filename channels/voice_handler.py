"""Voice channel handler for TechFlow CRM Digital FTE.

Adds a full voice channel on top of the existing agent:

- **Voice messages**  — audio (base64 or URL) → speech-to-text → agent → reply
- **Voice calls**     — Twilio ``<Gather input="speech">`` → agent → spoken reply
- **NLP + translation** — the customer's language is detected, the request is
  translated to English for the agent, and the reply is translated back into
  the customer's language so the spoken answer is natural.
- **First-time accuracy** — uses the most accurate Whisper model
  (``whisper-large-v3-turbo``) and a confidence gate: if the transcript
  confidence is below ``STT_CONFIDENCE_THRESHOLD`` the agent never guesses;
  it asks the customer to repeat, echoing back what it heard.

Providers (all OpenAI-compatible, over HTTP — no extra SDKs required):

- STT:  Groq Whisper (default) → OpenAI Whisper → degraded mode
- TTS:  OpenAI TTS → gTTS (offline) → text-only (no audio)
- NLP:  the existing OpenRouter chat client (no extra cost)
"""

import base64
import io
import json
import os
import re
from dataclasses import asdict, dataclass, field
from typing import Any

import httpx
import structlog

from exceptions import sanitize_error_message
from kafka_client import KafkaProducerClient, create_inbound_voice_message
from utils.circuit_breaker import get_circuit_breaker

logger = structlog.get_logger(__name__)

GROQ_STT_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
OPENAI_STT_URL = "https://api.openai.com/v1/audio/transcriptions"
OPENAI_TTS_URL = "https://api.openai.com/v1/audio/speech"
GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"

STT_MODEL = os.getenv("STT_MODEL", "whisper-large-v3-turbo")
STT_FALLBACK_MODEL = os.getenv("STT_FALLBACK_MODEL", "whisper-1")
TTS_MODEL = os.getenv("TTS_MODEL", "tts-1")
TTS_VOICE = os.getenv("TTS_VOICE", "alloy")
GROQ_CHAT_MODEL = os.getenv("GROQ_CHAT_MODEL", "llama-3.3-70b-versatile")
CONFIDENCE_THRESHOLD = float(os.getenv("STT_CONFIDENCE_THRESHOLD", "0.5"))

_VOICE_SUBJECT_RE = re.compile(r"^[+\d\s\-().ext]+$")


@dataclass
class Transcription:
    """Result of speech-to-text with accuracy metadata."""

    text: str
    language: str = "en"
    confidence: float = 1.0
    needs_clarification: bool = False
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("raw", None)
        return d


@dataclass
class VoiceResult:
    """Full voice pipeline result (STT → agent → TTS)."""

    transcript: str
    language: str
    confidence: float
    needs_clarification: bool = False
    translated_to_english: str | None = None
    agent_response: str = ""
    response_language: str = ""
    audio_base64: str | None = None
    audio_format: str = "mp3"
    ticket_id: str | None = None
    ticket_number: str | None = None
    clarification_message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ISO 639-1 → Twilio <Say> locale map (extends as needed)
_TWILIO_LANGUAGE_MAP = {
    "en": "en-US",
    "hi": "hi-IN",
    "ur": "ur-PK",
    "es": "es-ES",
    "fr": "fr-FR",
    "de": "de-DE",
    "ar": "ar-AE",
    "pt": "pt-BR",
    "zh": "zh-CN",
    "ja": "ja-JP",
    "ko": "ko-KR",
    "it": "it-IT",
    "ru": "ru-RU",
    "bn": "bn-BD",
    "pa": "pa-IN",
    "ta": "ta-IN",
    "te": "te-IN",
}


def twilio_language(iso_code: str) -> str:
    """Map an ISO 639-1 language code to a Twilio <Say> locale."""
    if not iso_code:
        return "en-US"
    code = iso_code.lower().split("-")[0]
    return _TWILIO_LANGUAGE_MAP.get(code, "en-US")


class VoiceHandler:
    """Orchestrates the voice channel: STT → translate → agent → TTS."""

    def __init__(
        self,
        kafka_producer: KafkaProducerClient | None = None,
        openai_client: Any = None,
    ):
        self.kafka_producer = kafka_producer
        self.openai_client = openai_client
        self.groq_api_key = os.getenv("GROQ_API_KEY")
        self.openai_api_key = os.getenv("OPENAI_API_KEY")
        self.stt_provider = os.getenv("STT_PROVIDER", "auto").lower()
        self.tts_provider = os.getenv("TTS_PROVIDER", "auto").lower()
        self.confidence_threshold = CONFIDENCE_THRESHOLD

    # ------------------------------------------------------------------
    # Speech-to-text
    # ------------------------------------------------------------------
    async def transcribe(
        self,
        audio_bytes: bytes,
        filename: str = "voice.wav",
        content_type: str | None = None,
        language: str | None = None,
    ) -> Transcription:
        """Transcribe audio using Groq Whisper, falling back to OpenAI Whisper."""
        errors: list[str] = []
        if self.stt_provider in ("auto", "groq") and self.groq_api_key:
            try:
                return await self._transcribe_http(
                    GROQ_STT_URL,
                    self.groq_api_key,
                    STT_MODEL,
                    audio_bytes,
                    filename,
                    content_type,
                    language,
                )
            except Exception as e:
                errors.append(f"groq: {sanitize_error_message(str(e))}")

        if self.stt_provider in ("auto", "openai") and self.openai_api_key:
            try:
                return await self._transcribe_http(
                    OPENAI_STT_URL,
                    self.openai_api_key,
                    STT_FALLBACK_MODEL,
                    audio_bytes,
                    filename,
                    content_type,
                    language,
                )
            except Exception as e:
                errors.append(f"openai: {sanitize_error_message(str(e))}")

        logger.warning("No STT provider available, returning degraded transcription", errors=errors)
        return Transcription(
            text="",
            language=language or "en",
            confidence=0.0,
            needs_clarification=True,
        )

    async def _transcribe_http(
        self,
        url: str,
        api_key: str,
        model: str,
        audio_bytes: bytes,
        filename: str,
        content_type: str | None,
        language: str | None,
    ) -> Transcription:
        _cb = get_circuit_breaker("openai")
        async with _cb:
            async with httpx.AsyncClient(timeout=60.0) as client:
                files = {"file": (filename, audio_bytes, content_type or "audio/wav")}
                data: dict[str, Any] = {
                    "model": model,
                    "response_format": "verbose_json",
                }
                if language:
                    data["language"] = language
                resp = await client.post(
                    url,
                    headers={"Authorization": f"Bearer {api_key}"},
                    data=data,
                    files=files,
                )
                resp.raise_for_status()
                result = resp.json()

        text = (result.get("text") or "").strip()
        detected_lang = result.get("language") or language or "en"
        confidence = self._confidence_from_segments(result.get("segments"))

        if not text:
            return Transcription(
                text="",
                language=detected_lang,
                confidence=confidence,
                needs_clarification=True,
            )

        return Transcription(
            text=text,
            language=detected_lang,
            confidence=confidence,
            needs_clarification=confidence < self.confidence_threshold,
            raw=result,
        )

    @staticmethod
    def _confidence_from_segments(segments: list | None) -> float:
        """Derive a [0, 1] confidence score from Whisper segment log-probabilities."""
        if not segments:
            return 1.0
        probs = [s.get("avg_logprob", 0.0) for s in segments if "avg_logprob" in s]
        if not probs:
            return 1.0
        avg = sum(probs) / len(probs)
        # avg_logprob lives in roughly [-1.5, 0]; map to [0, 1] (higher = better).
        return max(0.0, min(1.0, 1.0 - abs(avg) / 1.5))

    # ------------------------------------------------------------------
    # Language detection + translation (NLP via OpenRouter → Groq fallback)
    # ------------------------------------------------------------------
    async def _llm_completion(
        self,
        system_prompt: str,
        user_text: str,
        max_tokens: int = 500,
        json_mode: bool = False,
    ) -> str | None:
        """Run an LLM completion through OpenRouter, falling back to Groq.

        Returns the raw content string, or ``None`` when no provider works so
        callers can degrade gracefully.
        """
        if self.openai_client is not None:
            try:
                kwargs: dict[str, Any] = {
                    "model": os.getenv("OPENAI_MODEL", "openai/gpt-4o"),
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_text},
                    ],
                    "temperature": 0.0,
                    "max_tokens": max_tokens,
                }
                if json_mode:
                    kwargs["response_format"] = {"type": "json_object"}
                _cb = get_circuit_breaker("openai")
                async with _cb:
                    response = await self.openai_client.chat.completions.create(**kwargs)
                content = response.choices[0].message.content
                return content.strip() if isinstance(content, str) else None
            except Exception as e:
                logger.warning(
                    "OpenRouter completion failed, trying Groq",
                    error=sanitize_error_message(str(e)),
                )

        if self.groq_api_key:
            try:
                payload: dict[str, Any] = {
                    "model": GROQ_CHAT_MODEL,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_text},
                    ],
                    "temperature": 0.0,
                    "max_tokens": max_tokens,
                }
                if json_mode:
                    payload["response_format"] = {"type": "json_object"}
                _cb = get_circuit_breaker("openai")
                async with _cb:
                    async with httpx.AsyncClient(timeout=60.0) as client:
                        resp = await client.post(
                            GROQ_CHAT_URL,
                            headers={"Authorization": f"Bearer {self.groq_api_key}"},
                            json=payload,
                        )
                        resp.raise_for_status()
                        data = resp.json()
                content = data["choices"][0]["message"]["content"]
                return content.strip() if isinstance(content, str) else None
            except Exception as e:
                logger.warning(
                    "Groq completion failed too",
                    error=sanitize_error_message(str(e)),
                )

        return None

    async def detect_and_translate(self, text: str) -> tuple[str, str, bool]:
        """Detect language and translate to English.

        Returns ``(english_text, detected_lang, was_translated)``. Falls back to
        the raw text when no LLM provider is available so voice never breaks.
        """
        text = (text or "").strip()
        if not text:
            return text, "en", False

        system = (
            "You are a precise multilingual translation engine. Detect the language "
            "of the user's text and translate it to English, keeping the meaning and "
            "tone. Respond ONLY with JSON: "
            '{"language": "<ISO 639-1 code>", "translated": "<English text>", '
            '"is_english": true|false}'
        )
        content = await self._llm_completion(system, text, max_tokens=500, json_mode=True)
        if not content:
            return text, "en", False

        try:
            data = json.loads(content)
            language = (data.get("language") or "en").lower()
            translated = (data.get("translated") or text).strip()
            is_english = bool(data.get("is_english", language == "en"))
            return translated, language, not is_english
        except (json.JSONDecodeError, KeyError, AttributeError) as e:
            logger.warning(
                "Translation response parse failed",
                error=sanitize_error_message(str(e)),
            )
            return text, "en", False

    async def translate_to_language(self, text: str, language: str) -> str:
        """Translate an agent reply into the customer's language (for TTS)."""
        text = (text or "").strip()
        if not text:
            return text
        if not language or language.lower() in ("en", "english", "en-us"):
            return text

        system = (
            "You are a precise translation engine. Translate the support reply into "
            f"{language}. Keep it natural, professional, and polite. "
            "Respond with ONLY the translated text."
        )
        translated = await self._llm_completion(system, text, max_tokens=1000, json_mode=False)
        return translated or text

    # ------------------------------------------------------------------
    # Text-to-speech
    # ------------------------------------------------------------------
    async def synthesize(
        self, text: str, language: str = "en"
    ) -> tuple[str | None, str]:
        """Synthesize speech. Returns ``(audio_base64, format)``.

        Provider order: OpenAI TTS → gTTS (offline) → ``(None, "none")`` (text only).
        """
        if not text:
            return None, "none"

        if self.tts_provider in ("auto", "openai") and self.openai_api_key:
            try:
                return await self._synthesize_openai(text, language)
            except Exception as e:
                logger.warning(
                    "OpenAI TTS failed, trying fallback",
                    error=sanitize_error_message(str(e)),
                )

        if self.tts_provider in ("auto", "gtts"):
            try:
                return await self._synthesize_gtts(text, language)
            except Exception as e:
                logger.warning(
                    "gTTS failed, returning text-only response",
                    error=sanitize_error_message(str(e)),
                )

        return None, "none"

    async def _synthesize_openai(self, text: str, language: str) -> tuple[str, str]:
        """OpenAI TTS (httpx — OpenAI-compatible API)."""
        _cb = get_circuit_breaker("openai")
        async with _cb:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(
                    OPENAI_TTS_URL,
                    headers={"Authorization": f"Bearer {self.openai_api_key}"},
                    json={
                        "model": TTS_MODEL,
                        "voice": TTS_VOICE,
                        "input": text,
                    },
                )
                resp.raise_for_status()
                audio_bytes = resp.content
        audio_b64 = base64.b64encode(audio_bytes).decode("ascii")
        logger.info("Speech synthesized", provider="openai", bytes=len(audio_bytes))
        return audio_b64, "mp3"

    async def _synthesize_gtts(self, text: str, language: str) -> tuple[str, str]:
        """Offline gTTS fallback (requires the optional ``gtts`` package)."""
        try:
            from gtts import gTTS  # type: ignore
        except ImportError as e:
            raise RuntimeError("gtts package not installed") from e

        lang = (language or "en").lower().split("-")[0]
        buf = io.BytesIO()
        tts = gTTS(text=text, lang=lang)
        tts.write_to_fp(buf)
        audio_bytes = buf.getvalue()
        audio_b64 = base64.b64encode(audio_bytes).decode("ascii")
        logger.info("Speech synthesized", provider="gtts", lang=lang, bytes=len(audio_bytes))
        return audio_b64, "mp3"

    # ------------------------------------------------------------------
    # Audio helpers
    # ------------------------------------------------------------------
    @staticmethod
    async def resolve_audio(
        audio_base64: str | None = None,
        audio_url: str | None = None,
        max_bytes: int = 25 * 1024 * 1024,
    ) -> bytes | None:
        """Decode base64 audio or download it from a URL."""
        if audio_base64:
            try:
                return base64.b64decode(audio_base64, validate=True)
            except Exception as e:
                logger.warning("Invalid base64 audio", error=sanitize_error_message(str(e)))
                return None
        if audio_url:
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    resp = await client.get(audio_url)
                    resp.raise_for_status()
                    if len(resp.content) > max_bytes:
                        logger.warning("Audio download too large", size=len(resp.content))
                        return None
                    return resp.content
            except Exception as e:
                logger.warning("Audio download failed", error=sanitize_error_message(str(e)))
                return None
        return None

    @staticmethod
    def is_likely_phone(identifier: str) -> bool:
        """Heuristic: is this identifier a phone number rather than an email?"""
        if not identifier:
            return False
        return bool(_VOICE_SUBJECT_RE.match(identifier.strip()))

    # ------------------------------------------------------------------
    # Orchestration
    # ------------------------------------------------------------------
    def clarification_message(self, transcription: Transcription) -> str:
        """Message shown/spoken when the transcript confidence is too low."""
        heard = transcription.text.strip()
        if heard:
            return (
                "I want to make sure I understood you correctly. "
                f"Did you say: '{heard}'? Please repeat your question a little more clearly."
            )
        return "I'm sorry, I couldn't hear you clearly. Could you please repeat that?"

    async def handle_voice_message(
        self,
        *,
        audio_bytes: bytes,
        filename: str = "voice.wav",
        content_type: str | None = None,
        language: str | None = None,
        name: str | None = None,
        email: str | None = None,
        phone: str | None = None,
        run_agent: Any | None = None,
    ) -> VoiceResult:
        """Full voice pipeline: STT → (confidence gate) → translate → agent → TTS."""
        transcription = await self.transcribe(audio_bytes, filename, content_type, language)

        result = VoiceResult(
            transcript=transcription.text,
            language=transcription.language,
            confidence=transcription.confidence,
            needs_clarification=transcription.needs_clarification,
        )

        if transcription.needs_clarification:
            result.clarification_message = self.clarification_message(transcription)
            return result

        # Route raw transcript to Kafka for analytics/replay
        customer_identifier = email or phone or "voice@customer.local"
        if self.kafka_producer is not None:
            try:
                kafka_message = create_inbound_voice_message(
                    customer_phone=phone or "",
                    customer_name=name or "",
                    message_body=transcription.text,
                    language=transcription.language,
                    confidence=transcription.confidence,
                )
                await self.kafka_producer.send_message(
                    kafka_message.topic,
                    kafka_message.payload,
                    key=customer_identifier,
                )
            except Exception as e:
                logger.warning(
                    "Failed to route voice message to Kafka",
                    error=sanitize_error_message(str(e)),
                )

        # Translate to English so the agent always understands
        english, detected_lang, was_translated = await self.detect_and_translate(transcription.text)
        result.translated_to_english = english
        result.language = detected_lang

        if not run_agent:
            return result

        agent_result = await run_agent(english)
        result.agent_response = agent_result.get("response", "")
        result.ticket_id = agent_result.get("ticket_id")
        result.ticket_number = agent_result.get("ticket_number")
        result.response_language = detected_lang

        # Translate the reply back into the customer's language
        spoken = result.agent_response
        if was_translated and detected_lang and detected_lang != "en":
            spoken = await self.translate_to_language(spoken, detected_lang)

        audio_b64, fmt = await self.synthesize(spoken, detected_lang)
        result.audio_base64 = audio_b64
        result.audio_format = fmt

        logger.info(
            "Voice message processed",
            language=detected_lang,
            confidence=result.confidence,
            translated=was_translated,
            audio=fmt,
        )
        return result
