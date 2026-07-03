"""Pytest configuration and fixtures."""

import os
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

from openai import AsyncOpenAI
from kafka_client import KafkaProducerClient

@pytest.fixture(scope="session")
def event_loop():
    """Create event loop for async tests."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def mock_openai_client():
    """Mock OpenAI/OpenRouter client (handles both chat + embeddings)."""
    mock = MagicMock(spec=AsyncOpenAI)
    mock.chat.completions.create = AsyncMock()
    mock.embeddings.create = AsyncMock()
    return mock


@pytest.fixture
def mock_kafka_producer():
    """Mock Kafka producer."""
    mock = MagicMock(spec=KafkaProducerClient)
    mock.send_message = AsyncMock(return_value="msg-123")
    mock.start = AsyncMock()
    mock.stop = AsyncMock()
    return mock


@pytest.fixture
def mock_db_pool():
    """Mock database pool that supports async context manager protocol."""
    mock = MagicMock()
    mock_conn = AsyncMock()
    mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_conn.__aexit__ = AsyncMock(return_value=None)
    mock.acquire = MagicMock(return_value=mock_conn)
    return mock


@pytest.fixture
def test_ticket():
    """Sample ticket for testing."""
    return {
        "id": "550e8400-e29b-41d4-a716-446655440000",
        "ticket_number": "TKT-20240312-0001",
        "customer_id": "550e8400-e29b-41d4-a716-446655440001",
        "subject": "How do I set up a connector?",
        "status": "open",
        "priority": "medium",
        "category": "onboarding",
        "channel": "webform",
        "created_at": "2024-03-12T10:00:00",
        "updated_at": "2024-03-12T10:00:00",
    }


@pytest.fixture
def test_customer():
    """Sample customer for testing."""
    return {
        "id": "550e8400-e29b-41d4-a716-446655440001",
        "email": "john@example.com",
        "name": "John Doe",
        "company": "Acme Corp",
        "tier": "starter",
        "created_at": "2024-03-12T10:00:00",
    }


def pytest_configure(config):
    """Configure pytest."""
    # Add custom markers
    config.addinivalue_line(
        "markers", "slow: marks tests as slow (deselect with '-m \"not slow\"')"
    )
    config.addinivalue_line(
        "markers", "integration: marks tests as integration tests"
    )
    config.addinivalue_line(
        "markers", "e2e: marks tests as end-to-end tests"
    )
