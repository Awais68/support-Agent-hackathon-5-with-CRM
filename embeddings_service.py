"""Embeddings service for semantic search in knowledge base."""

from typing import List
import structlog
from openai import AsyncOpenAI

logger = structlog.get_logger(__name__)


class EmbeddingsService:
    """Service for generating and managing embeddings."""

    def __init__(self, openai_client: AsyncOpenAI, model: str = "text-embedding-3-small"):
        self.client = openai_client
        self.model = model

    async def embed_text(self, text: str) -> List[float]:
        """Generate embedding for a single text."""
        try:
            response = await self.client.embeddings.create(
                input=text,
                model=self.model,
            )
            embedding = response.data[0].embedding
            logger.info("Text embedded", text_length=len(text), model=self.model)
            return embedding

        except Exception as e:
            logger.error("Embedding generation failed", error=str(e))
            raise

    async def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """Generate embeddings for multiple texts efficiently."""
        try:
            if not texts:
                return []

            response = await self.client.embeddings.create(
                input=texts,
                model=self.model,
            )

            embeddings = [item.embedding for item in response.data]
            logger.info(
                "Batch embedded",
                batch_size=len(texts),
                model=self.model,
            )
            return embeddings

        except Exception as e:
            logger.error("Batch embedding failed", error=str(e), batch_size=len(texts))
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

        except Exception as e:
            logger.error("KB article embedding failed", error=str(e), title=title)
            raise
