"""Embedding provider wiring, kept separate from the chat provider.

Chat runs on OpenRouter, but that account has no embedding credits — every
``/embeddings`` call there returns HTTP 402 — which silently disabled knowledge
base search: the agent fell back to "escalate to human support" on every
lookup. Embeddings therefore go to Gemini's OpenAI-compatible endpoint while
chat stays on OpenRouter, so an outage or quota change on one provider cannot
take out the other.

Gemini's ``gemini-embedding-001`` returns 3072 dimensions by default, but
``knowledge_base.embedding`` is ``vector(1536)``. The ``dimensions`` override is
mandatory here, not an optimisation, so every embedding call goes through this
module rather than calling ``client.embeddings.create`` directly.
"""

import os
from dataclasses import dataclass
from typing import List, Optional

import structlog
from openai import AsyncOpenAI

from database.queries import EMBEDDING_DIM

logger = structlog.get_logger(__name__)

GEMINI_BASE_URL_DEFAULT = "https://generativelanguage.googleapis.com/v1beta/openai/"
GEMINI_EMBEDDING_MODEL_DEFAULT = "gemini-embedding-001"
CHAT_PROVIDER_EMBEDDING_MODEL_DEFAULT = "openai/text-embedding-3-small"

# Embeddings hit a different provider than chat, so they get their own breaker.
# Sharing the "openai" breaker would let an embedding outage reject chat calls.
EMBEDDING_CIRCUIT_BREAKER = "embeddings"


@dataclass
class EmbeddingProvider:
    """A client paired with the model name that client actually serves."""

    client: AsyncOpenAI
    model: str

    async def embed(self, text: str) -> List[float]:
        """Embed one string at the dimension the schema's vector column expects."""
        response = await self.client.embeddings.create(
            input=text,
            model=self.model,
            dimensions=EMBEDDING_DIM,
        )
        return response.data[0].embedding

    async def embed_many(self, texts: List[str]) -> List[List[float]]:
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


def build_embedding_provider(
    chat_client: Optional[AsyncOpenAI] = None,
) -> Optional[EmbeddingProvider]:
    """Build the embedding provider for this process.

    Prefers Gemini when ``GEMINI_API_KEY`` is set; otherwise falls back to the
    chat client so single-provider deployments keep working unchanged.
    """
    key = gemini_api_key()
    if key:
        model = os.getenv("GEMINI_EMBEDDING_MODEL", GEMINI_EMBEDDING_MODEL_DEFAULT)
        logger.info("Embeddings provider: gemini", model=model)
        return EmbeddingProvider(
            client=AsyncOpenAI(
                api_key=key,
                base_url=os.getenv("GEMINI_BASE_URL", GEMINI_BASE_URL_DEFAULT),
            ),
            model=model,
        )

    if chat_client is None:
        logger.warning("No embedding provider available: GEMINI_API_KEY unset and no chat client")
        return None

    model = os.getenv("EMBEDDING_MODEL", CHAT_PROVIDER_EMBEDDING_MODEL_DEFAULT)
    logger.info("Embeddings provider: chat provider (GEMINI_API_KEY unset)", model=model)
    return EmbeddingProvider(client=chat_client, model=model)


def resolve_embedding_provider(
    provider: Optional[EmbeddingProvider],
    chat_client: Optional[AsyncOpenAI],
) -> Optional[EmbeddingProvider]:
    """Return ``provider`` if wired at startup, else embed via the chat client.

    Call sites that were never updated to carry a provider keep working, and
    tests that inject a mock chat client keep exercising that mock instead of
    reaching a real Gemini endpoint.
    """
    if provider is not None:
        return provider
    if chat_client is None:
        return None
    return EmbeddingProvider(
        client=chat_client,
        model=os.getenv("EMBEDDING_MODEL", CHAT_PROVIDER_EMBEDDING_MODEL_DEFAULT),
    )
