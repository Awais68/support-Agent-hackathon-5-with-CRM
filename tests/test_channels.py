"""Tests for channel handlers."""

import pytest
from unittest.mock import MagicMock, AsyncMock

from channels.web_form_handler import WebFormHandler, WebFormSubmission


@pytest.mark.asyncio
async def test_web_form_validation():
    """Test web form submission validation."""
    # Valid submission
    valid_data = {
        "name": "John Doe",
        "email": "john@example.com",
        "subject": "Help with connector",
        "message": "I need help setting up a connector for my data warehouse.",
        "category": "technical",
        "priority": "medium",
    }

    submission = WebFormSubmission(**valid_data)
    assert submission.email == "john@example.com"
    assert submission.name == "John Doe"


def test_web_form_invalid_email():
    """Test validation of invalid email."""
    invalid_data = {
        "name": "John",
        "email": "invalid-email",
        "subject": "Help",
        "message": "This is a test message for the form.",
    }

    with pytest.raises(Exception):
        WebFormSubmission(**invalid_data)


def test_web_form_short_message():
    """Test validation of message too short."""
    invalid_data = {
        "name": "John",
        "email": "john@example.com",
        "subject": "Help",
        "message": "Short",  # Too short
    }

    with pytest.raises(Exception):
        WebFormSubmission(**invalid_data)


def test_web_form_long_message():
    """Test validation of message too long."""
    invalid_data = {
        "name": "John",
        "email": "john@example.com",
        "subject": "Help",
        "message": "x" * 1001,  # Too long
    }

    with pytest.raises(Exception):
        WebFormSubmission(**invalid_data)


@pytest.mark.asyncio
async def test_web_form_process_submission(mock_kafka_producer):
    """Test processing a web form submission."""
    handler = WebFormHandler(mock_kafka_producer)

    submission = WebFormSubmission(
        name="Jane Doe",
        email="jane@example.com",
        subject="API integration help",
        message="I need help integrating the TechFlow API into our application.",
    )

    result = await handler.process_submission(submission)

    assert result["success"] == True
    assert result["email"] == "jane@example.com"
    mock_kafka_producer.send_message.assert_called_once()


def test_whatsapp_char_limit():
    """Test WhatsApp character limit enforcement."""
    from agent.formatters import truncate_for_channel

    long_text = "x" * 600

    truncated = truncate_for_channel(long_text, channel="whatsapp", max_chars=500)

    assert len(truncated) <= 500
    assert truncated.endswith("...")
