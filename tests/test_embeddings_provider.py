"""Tests for the embedding provider split and the knowledge base fallback.

These cover the failure that shipped silently: chat runs on OpenRouter, whose
account has no embedding credits, so every ``/embeddings`` call returned HTTP
402 and the agent answered "escalate to human support" on every knowledge base
lookup instead of degrading to lexical search.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from openai import APIError as OpenAIAPIError

from agent.tools import ToolContext, search_knowledge_base
from agent.tools_executor import ToolExecutor
from database.queries import EMBEDDING_DIM
from embeddings_provider import (
    EmbeddingProvider,
    build_embedding_provider,
    resolve_embedding_provider,
)


def _mock_embeddings_client(dim: int = EMBEDDING_DIM) -> AsyncMock:
    client = AsyncMock()
    client.embeddings.create.return_value = MagicMock(
        data=[MagicMock(embedding=[0.1] * dim)]
    )
    return client


def _api_error(message: str) -> OpenAIAPIError:
    return OpenAIAPIError(message, request=MagicMock(), body=None)


@pytest.fixture
def kb_row() -> dict:
    return {
        "id": "kb-001",
        "title": "Connector Setup",
        "content": "How to set up connectors...",
        "category": "technical",
        "tags": [],
        "source": "docs",
        "tier": "all",
        "similarity": 0.95,
    }


def test_gemini_key_selects_gemini_provider(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("GEMINI_EMBEDDING_MODEL", "gemini-embedding-001")
    chat_client = MagicMock()

    provider = build_embedding_provider(chat_client)

    assert provider is not None
    assert provider.model == "gemini-embedding-001"
    # Embeddings must not ride on the chat client, which has no credits.
    assert provider.client is not chat_client
    assert "generativelanguage.googleapis.com" in str(provider.client.base_url)


def test_empty_gemini_key_falls_back_to_chat_client(monkeypatch):
    # An exported-but-empty var is common in shell wrappers and must count as unset.
    monkeypatch.setenv("GEMINI_API_KEY", "   ")
    monkeypatch.setenv("EMBEDDING_MODEL", "openai/text-embedding-3-small")
    chat_client = MagicMock()

    provider = build_embedding_provider(chat_client)

    assert provider is not None
    assert provider.client is chat_client
    assert provider.model == "openai/text-embedding-3-small"


def test_no_key_and_no_chat_client_yields_no_provider(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert build_embedding_provider(None) is None
    assert resolve_embedding_provider(None, None) is None


@pytest.mark.asyncio
async def test_embed_requests_schema_dimension():
    """gemini-embedding-001 defaults to 3072; the vector(1536) column needs 1536."""
    client = _mock_embeddings_client()
    provider = EmbeddingProvider(client=client, model="gemini-embedding-001")

    embedding = await provider.embed("connector setup")

    assert len(embedding) == EMBEDDING_DIM
    kwargs = client.embeddings.create.call_args.kwargs
    assert kwargs["dimensions"] == EMBEDDING_DIM
    assert kwargs["model"] == "gemini-embedding-001"


@pytest.mark.asyncio
async def test_embed_many_batches_in_one_request():
    client = AsyncMock()
    client.embeddings.create.return_value = MagicMock(
        data=[MagicMock(embedding=[0.1] * EMBEDDING_DIM) for _ in range(3)]
    )
    provider = EmbeddingProvider(client=client, model="gemini-embedding-001")

    vectors = await provider.embed_many(["a", "b", "c"])

    assert len(vectors) == 3
    assert client.embeddings.create.await_count == 1


@pytest.mark.asyncio
async def test_embed_many_short_circuits_on_empty_input():
    client = AsyncMock()
    provider = EmbeddingProvider(client=client, model="gemini-embedding-001")

    assert await provider.embed_many([]) == []
    client.embeddings.create.assert_not_called()


@pytest.mark.asyncio
async def test_tool_uses_configured_provider_not_chat_client(kb_row):
    """The agent tool must embed through the provider, not the chat client."""
    chat_client = AsyncMock()
    embed_client = _mock_embeddings_client()
    context = ToolContext(
        db_pool=AsyncMock(),
        kafka_producer=AsyncMock(),
        openai_client=chat_client,
        embedding_provider=EmbeddingProvider(embed_client, "gemini-embedding-001"),
    )

    with patch("agent.tools.db.search_knowledge_base", new_callable=AsyncMock) as vector_search:
        vector_search.return_value = [kb_row]
        result = await search_knowledge_base({"query": "connector setup"}, context)

    assert result["found"] is True
    assert result["search_mode"] == "semantic"
    assert "degraded" not in result
    embed_client.embeddings.create.assert_awaited_once()
    chat_client.embeddings.create.assert_not_called()


@pytest.mark.asyncio
async def test_tool_falls_back_to_lexical_search_when_embedding_fails(kb_row):
    """An embedding 402/outage must degrade to lexical search, not escalate."""
    embed_client = AsyncMock()
    embed_client.embeddings.create.side_effect = _api_error("Insufficient credits")
    context = ToolContext(
        db_pool=AsyncMock(),
        kafka_producer=AsyncMock(),
        openai_client=AsyncMock(),
        embedding_provider=EmbeddingProvider(embed_client, "gemini-embedding-001"),
    )

    with patch("agent.tools.db.search_knowledge_base", new_callable=AsyncMock) as vector_search, \
         patch("agent.tools.db.search_knowledge_base_text", new_callable=AsyncMock) as text_search:
        text_search.return_value = [kb_row]
        result = await search_knowledge_base({"query": "connector setup"}, context)

    assert result["found"] is True
    assert result["search_mode"] == "text"
    assert result["degraded"] is True
    assert result["results"][0]["title"] == "Connector Setup"
    text_search.assert_awaited_once()
    vector_search.assert_not_called()


@pytest.mark.asyncio
async def test_executor_falls_back_to_lexical_search_when_embedding_fails(kb_row):
    embed_client = AsyncMock()
    embed_client.embeddings.create.side_effect = _api_error("Insufficient credits")
    executor = ToolExecutor(
        db_pool=AsyncMock(),
        kafka_producer=AsyncMock(),
        openai_client=AsyncMock(),
        embedding_provider=EmbeddingProvider(embed_client, "gemini-embedding-001"),
    )

    with patch("agent.tools_executor.db.search_knowledge_base_text", new_callable=AsyncMock) as text_search:
        text_search.return_value = [kb_row]
        result = await executor.search_knowledge_base("connector setup")

    assert result["count"] == 1
    assert result["search_mode"] == "text"
    assert result["degraded"] is True


@pytest.mark.asyncio
async def test_vector_search_is_scoped_to_the_active_embedding_model(kb_row):
    """Vectors from another model are not comparable, so they must be excluded.

    Without the model filter, a query embedded by OpenAI against a Gemini-indexed
    knowledge base returned articles ranked by noise (~0.04 cosine similarity
    instead of ~0.63) and reported them as a healthy ``vector`` result.
    """
    context = ToolContext(
        db_pool=AsyncMock(),
        kafka_producer=AsyncMock(),
        openai_client=AsyncMock(),
        embedding_provider=EmbeddingProvider(
            _mock_embeddings_client(), "gemini-embedding-001"
        ),
    )

    with patch("agent.tools.db.search_knowledge_base", new_callable=AsyncMock) as vector_search:
        vector_search.return_value = [kb_row]
        result = await search_knowledge_base({"query": "connector setup"}, context)

    assert result["search_mode"] == "semantic"
    assert vector_search.await_args.kwargs["embedding_model"] == "gemini-embedding-001"


@pytest.mark.asyncio
async def test_model_mismatch_degrades_to_lexical_search(kb_row):
    """No comparable vectors on disk must degrade, not answer from noise."""
    context = ToolContext(
        db_pool=AsyncMock(),
        kafka_producer=AsyncMock(),
        openai_client=AsyncMock(),
        embedding_provider=EmbeddingProvider(
            _mock_embeddings_client(), "openai/text-embedding-3-small"
        ),
    )

    with patch("agent.tools.db.search_knowledge_base", new_callable=AsyncMock) as vector_search, \
         patch("agent.tools.db.search_knowledge_base_text", new_callable=AsyncMock) as text_search:
        vector_search.return_value = []
        text_search.return_value = [kb_row]
        result = await search_knowledge_base({"query": "connector setup"}, context)

    assert result["search_mode"] == "text"
    assert result["degraded"] is True
    assert "embedding model" in result["error"]
    text_search.assert_awaited_once()


@pytest.mark.asyncio
async def test_model_mismatch_stays_degraded_when_lexical_is_empty():
    """An empty mismatch result must not masquerade as "no such article"."""
    context = ToolContext(
        db_pool=AsyncMock(),
        kafka_producer=AsyncMock(),
        openai_client=AsyncMock(),
        embedding_provider=EmbeddingProvider(
            _mock_embeddings_client(), "openai/text-embedding-3-small"
        ),
    )

    with patch("agent.tools.db.search_knowledge_base", new_callable=AsyncMock) as vector_search, \
         patch("agent.tools.db.search_knowledge_base_text", new_callable=AsyncMock) as text_search:
        vector_search.return_value = []
        text_search.return_value = []
        result = await search_knowledge_base({"query": "connector setup"}, context)

    assert result["found"] is False
    assert result["search_mode"] == "text"
    assert result["degraded"] is True


@pytest.mark.asyncio
async def test_executor_degrades_on_model_mismatch(kb_row):
    executor = ToolExecutor(
        db_pool=AsyncMock(),
        kafka_producer=AsyncMock(),
        openai_client=AsyncMock(),
        embedding_provider=EmbeddingProvider(
            _mock_embeddings_client(), "openai/text-embedding-3-small"
        ),
    )

    with patch("agent.tools_executor.db.search_knowledge_base", new_callable=AsyncMock) as vector_search, \
         patch("agent.tools_executor.db.search_knowledge_base_text", new_callable=AsyncMock) as text_search:
        vector_search.return_value = []
        text_search.return_value = [kb_row]
        result = await executor.search_knowledge_base("connector setup")

    assert result["search_mode"] == "text"
    assert result["degraded"] is True
    assert vector_search.await_args.kwargs["embedding_model"] == "openai/text-embedding-3-small"
