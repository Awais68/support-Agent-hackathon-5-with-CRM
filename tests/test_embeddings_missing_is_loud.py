"""A missing GEMINI_API_KEY must be loud, not a silent downgrade.

Without the key, embeddings quietly fell back to the chat client. On DeepSeek
(no embedding models) or this OpenRouter account (no embedding credits)
every call failed, KB search dropped to lexical, the API still reported
"healthy" and the only trace was a warning per query.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from prometheus_client import REGISTRY
from structlog.testing import capture_logs

from agent.tools import ToolContext, search_knowledge_base
from api.main import app
from embeddings_provider import (
    EmbeddingProvider,
    build_embedding_provider,
    resolve_embedding_provider,
)


def _fallbacks(reason: str, surface: str) -> float:
    value = REGISTRY.get_sample_value(
        "kb_search_lexical_fallback_total", {"reason": reason, "surface": surface}
    )
    return value or 0.0


@pytest.fixture
def no_gemini(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("EMBEDDING_MODEL", raising=False)


def test_missing_key_logs_an_error_and_does_not_ride_on_the_chat_client(no_gemini):
    with capture_logs() as logs:
        provider = build_embedding_provider(MagicMock())
    assert provider is None
    assert any(e["log_level"] == "error" and "GEMINI_API_KEY" in e["event"] for e in logs), logs
    assert REGISTRY.get_sample_value("embeddings_configured") == 0


def test_resolve_does_not_fall_back_to_the_chat_client_silently(no_gemini):
    assert resolve_embedding_provider(None, MagicMock()) is None


def test_embedding_model_is_ignored_on_deepseek(no_gemini, monkeypatch):
    # Before: EMBEDDING_MODEL routed embeddings to DeepSeek, which has none, so
    # readiness said "ok" while every embedding call failed.
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("EMBEDDING_MODEL", "openai/text-embedding-3-small")
    with capture_logs() as logs:
        assert build_embedding_provider(MagicMock()) is None
    assert any(e["log_level"] == "error" and "EMBEDDING_MODEL" in e["event"] for e in logs), logs
    assert REGISTRY.get_sample_value("embeddings_configured") == 0


def test_configured_key_sets_the_gauge(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    assert build_embedding_provider(None) is not None
    assert REGISTRY.get_sample_value("embeddings_configured") == 1


@pytest.fixture
def ready_client(monkeypatch):
    pool = MagicMock()
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=1)
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(app.state, "db_pool", pool, raising=False)
    monkeypatch.setattr(app.state, "kafka_enabled", True, raising=False)
    monkeypatch.setenv("ENABLE_KAFKA", "true")
    return TestClient(app, raise_server_exceptions=False)


def test_readiness_reports_degraded_without_embeddings(ready_client, monkeypatch):
    monkeypatch.setattr(app.state, "embedding_provider", None, raising=False)
    resp = ready_client.get("/readyz")
    # Still serving (lexical search works), so not 503, but visibly degraded.
    assert resp.status_code == 200
    assert resp.json()["status"] == "degraded"
    assert resp.json()["embeddings"] == "missing"


def test_readiness_is_healthy_with_embeddings(ready_client, monkeypatch):
    provider = EmbeddingProvider(MagicMock(), "gemini-embedding-001")
    monkeypatch.setattr(app.state, "embedding_provider", provider, raising=False)
    resp = ready_client.get("/readyz")
    assert resp.status_code == 200
    assert resp.json()["status"] == "healthy"
    assert resp.json()["embeddings"] == "ok"


@pytest.mark.asyncio
async def test_lexical_fallback_is_counted_and_logged_as_an_error(no_gemini, monkeypatch):
    text_search = AsyncMock(return_value=[])
    monkeypatch.setattr("agent.tools.db.search_knowledge_base_text", text_search)
    context = ToolContext(
        db_pool=AsyncMock(), kafka_producer=AsyncMock(), openai_client=AsyncMock()
    )
    before = _fallbacks("not_configured", "agent_tool")

    with capture_logs() as logs:
        result = await search_knowledge_base({"query": "reset password"}, context)

    assert result["degraded"] is True
    assert _fallbacks("not_configured", "agent_tool") == before + 1
    assert any(
        e["log_level"] == "error" and e["event"] == "KB search fell back to lexical" for e in logs
    ), logs
