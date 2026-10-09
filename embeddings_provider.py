"""Embedding provider wiring, kept separate from the chat provider.

Chat runs on DeepSeek, which serves no embedding models (and the optional
OpenRouter fallback account has no embedding credits: HTTP 402). Embeddings
therefore go to Gemini's OpenAI-compatible endpoint, so an outage or quota
change on one provider cannot take out the other.

Without ``GEMINI_API_KEY`` there is no embedding provider: startup logs an
error, ``/readyz`` reports ``embeddings: missing`` and every lexical fallback
is logged as an error and counted (``kb_search_lexical_fallback_total``,
alerted in monitoring/alerts.yml). Embedding through the chat client is only
used when ``EMBEDDING_MODEL`` is set explicitly and the chat provider is not
DeepSeek; with DeepSeek the setting is ignored and logged as an error.

Gemini's ``gemini-embedding-001`` returns 3072 dimensions by default, but
``knowledge_base.embedding`` is ``vector(1536)``. The ``dimensions`` override is
mandatory here, not an optimisation, so every embedding call goes through this
module rather than calling ``client.embeddings.create`` directly.
"""

import os
from dataclasses import dataclass

import structlog
from openai import AsyncOpenAI

from chat_provider import chat_provider_config
from database.queries import EMBEDDING_DIM
from metrics import embeddings_configured, kb_search_lexical_fallback

logger = structlog.get_logger(__name__)

GEMINI_BASE_URL_DEFAULT = "https://generativelanguage.googleapis.com/v1beta/openai/"
GEMINI_EMBEDDING_MODEL_DEFAULT = "gemini-embedding-001"

# Embeddings hit a different provider than chat, so they get their own breaker.
# Sharing the "openai" breaker would let an embedding outage reject chat calls.
EMBEDDING_CIRCUIT_BREAKER = "embeddings"


@dataclass
class EmbeddingProvider:
    """A client paired with the model name that client actually serves."""

    client: AsyncOpenAI
    model: str

    async def embed(self, text: str) -> list[float]:
        """Embed one string at the dimension the schema's vector column expects."""
        response = await self.client.embeddings.create(
            input=text,
            model=self.model,
            dimensions=EMBEDDING_DIM,
        )
        return response.data[0].embedding

    async def embed_many(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch in one request."""
        if not texts:
            return []
        response = await self.client.embeddings.create(
            input=texts,
            model=self.model,
            dimensions=EMBEDDING_DIM,
        )
        return [item.embedding for item in response.data]


def gemini_api_key() -> str:
    """Gemini key, treating an exported-but-empty var as unset."""
    return (os.getenv("GEMINI_API_KEY") or "").strip()


def chat_embedding_model() -> str:
    """Explicit opt-in to embed through the chat client (empty: not allowed).

    Ignored on DeepSeek, which serves no embedding models: honouring it
    reported embeddings as configured while every call failed.
    """
    model = (os.getenv("EMBEDDING_MODEL") or "").strip()
    if model and chat_provider_config().name == "deepseek":
        logger.error(
            "EMBEDDING_MODEL is ignored: the chat provider is DeepSeek, which has "
            "no embedding models; set GEMINI_API_KEY instead",
            model=model,
        )
        return ""
    return model


def build_embedding_provider(
    chat_client: AsyncOpenAI | None = None,
) -> EmbeddingProvider | None:
    """Build the embedding provider for this process.

    Gemini when ``GEMINI_API_KEY`` is set. The chat client only when
    ``EMBEDDING_MODEL`` names a model it serves. Otherwise None, logged as an
    error: KB search will run lexical-only.
    """
    key = gemini_api_key()
    if key:
        model = os.getenv("GEMINI_EMBEDDING_MODEL", GEMINI_EMBEDDING_MODEL_DEFAULT)
        logger.info("Embeddings provider: gemini", model=model)
        embeddings_configured.set(1)
        return EmbeddingProvider(
            client=AsyncOpenAI(
                api_key=key,
                base_url=os.getenv("GEMINI_BASE_URL", GEMINI_BASE_URL_DEFAULT),
            ),
            model=model,
        )

    model = chat_embedding_model()
    if model and chat_client is not None:
        logger.warning("Embeddings provider: chat client (EMBEDDING_MODEL set)", model=model)
        embeddings_configured.set(1)
        return EmbeddingProvider(client=chat_client, model=model)

    embeddings_configured.set(0)
    logger.error(
        "GEMINI_API_KEY is not set: no embedding provider, knowledge base search "
        "is lexical-only until it is configured"
    )
    return None


def resolve_embedding_provider(
    provider: EmbeddingProvider | None,
    chat_client: AsyncOpenAI | None,
) -> EmbeddingProvider | None:
    """Return ``provider`` if wired at startup.

    Falls back to the chat client only with an explicit ``EMBEDDING_MODEL``;
    DeepSeek has no embedding models, so an implicit fallback just failed
    every call and hid the missing key.
    """
    if provider is not None:
        return provider
    model = chat_embedding_model()
    if chat_client is None or not model:
        return None
    return EmbeddingProvider(client=chat_client, model=model)


def record_lexical_fallback(reason: str, surface: str, **fields: object) -> None:
    """Log and count a KB search that fell back to lexical (alerted on).

    reason: not_configured | circuit_open | embedding_failed | not_indexed.
    surface: agent_tool | tool_executor | api_search.
    """
    kb_search_lexical_fallback.labels(reason=reason, surface=surface).inc()
    logger.error("KB search fell back to lexical", reason=reason, surface=surface, **fields)
