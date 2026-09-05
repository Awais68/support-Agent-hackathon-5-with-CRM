"""Embeddings service for semantic search in knowledge base."""

from typing import List, Optional
import structlog
from openai import AsyncOpenAI
from openai import APIError as OpenAIAPIError, APITimeoutError, APIConnectionError
from exceptions import sanitize_error_message
from database.queries import EMBEDDING_DIM
from utils.circuit_breaker import get_circuit_breaker, CircuitBreakerError
from embeddings_provider import (
    EMBEDDING_CIRCUIT_BREAKER,
    EmbeddingProvider,
    resolve_embedding_provider,
)

logger = structlog.get_logger(__name__)


class EmbeddingsService:
    """Service for generating and managing embeddings."""

    def __init__(
        self,
        openai_client: AsyncOpenAI,
        model: str = "",
        embedding_provider: Optional[EmbeddingProvider] = None,
    ):
        # Embeddings run on their own provider (see embeddings_provider); an
        # explicit ``model`` still wins so callers can override it.
        provider = resolve_embedding_provider(embedding_provider, openai_client)
        self.provider = provider
        self.client = provider.client if provider else openai_client
        self.model = model or (provider.model if provider else "")

    async def embed_text(self, text: str) -> List[float]:
        """Generate embedding for a single text."""
        _cb = get_circuit_breaker(EMBEDDING_CIRCUIT_BREAKER)
        try:
            async with _cb:
                response = await self.client.embeddings.create(
                    input=text,
                    model=self.model,
                    dimensions=EMBEDDING_DIM,
                )
            embedding = response.data[0].embedding
            logger.info("Text embedded", text_length=len(text), model=self.model)
            return embedding

        except (OpenAIAPIError, APITimeoutError, APIConnectionError, CircuitBreakerError, Exception) as e:
            logger.error("Embedding generation failed", error=sanitize_error_message(str(e)))
            raise

    async def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """Generate embeddings for multiple texts efficiently."""
        try:
            if not texts:
                return []

            _cb = get_circuit_breaker(EMBEDDING_CIRCUIT_BREAKER)
            async with _cb:
                response = await self.client.embeddings.create(
                    input=texts,
                    model=self.model,
                    dimensions=EMBEDDING_DIM,
                )

            embeddings = [item.embedding for item in response.data]
            logger.info(
                "Batch embedded",
                batch_size=len(texts),
                model=self.model,
            )
            return embeddings

        except (OpenAIAPIError, APITimeoutError, APIConnectionError, CircuitBreakerError, Exception) as e:
            logger.error("Batch embedding failed", error=sanitize_error_message(str(e)), batch_size=len(texts))
            raise

    async def embed_knowledge_base_article(
        self, title: str, content: str
    ) -> tuple[List[float], List[float], List[float]]:
        """Generate embeddings for KB article (title, content, combined)."""
        try:
            # Embed title
            title_embedding = await self.embed_text(title)

            # Embed content
            content_embedding = await self.embed_text(content)

            # Embed combined (title + content for hybrid search)
            combined_text = f"{title}\n\n{content}"
            combined_embedding = await self.embed_text(combined_text)

            logger.info(
                "KB article embedded",
                title=title,
                content_length=len(content),
            )

            return title_embedding, content_embedding, combined_embedding

        except (OpenAIAPIError, APITimeoutError, APIConnectionError, Exception) as e:
            logger.error("KB article embedding failed", error=sanitize_error_message(str(e)), title=title)
            raise
