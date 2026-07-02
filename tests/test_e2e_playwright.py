"""Playwright end-to-end tests for web form and ticket flow."""

import pytest
import json
from playwright.async_api import async_playwright, Page
from datetime import datetime

# These tests require:
# - Running API server on http://localhost:8000
# - Database initialized
# - API_KEY environment variable set

BASE_URL = "http://localhost:8000"
API_KEY = "test-key-12345"


@pytest.fixture
async def browser():
    """Provide a browser instance."""
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        yield browser
        await browser.close()


@pytest.fixture
async def page(browser):
    """Provide a page instance."""
    page = await browser.new_page()
    yield page
    await page.close()


class TestWebFormFlow:
    """Test web form submission and ticket creation flow."""

    async def test_web_form_submission_creates_ticket(self, page: Page):
        """Test that web form submission creates a support ticket."""
        # Mock web form endpoint (would be in real HTML page)
        response = await page.request.post(
            f"{BASE_URL}/webhooks/webform",
            data=json.dumps({
                "name": "Jane Smith",
                "email": "jane.smith@example.com",
                "subject": "How to set up connectors?",
                "message": "I'm trying to set up my first data connector but need guidance.",
                "category": "onboarding",
                "priority": "medium",
            }),
            headers={"Content-Type": "application/json"},
        )

        assert response.status == 201
        data = await response.json()
        assert "ticket_number" in data
        assert "message" in data
        assert "tracking_url" in data
        assert "estimated_response" in data

        # Extract ticket number
        ticket_number = data["ticket_number"]
        assert ticket_number.startswith("TKT-")

    async def test_ticket_creation_via_api(self, page: Page):
        """Test ticket creation through REST API."""
        response = await page.request.post(
            f"{BASE_URL}/tickets",
            data=json.dumps({
                "name": "John Doe",
                "email": "john@example.com",
                "subject": "API integration issue",
                "category": "technical",
                "priority": "high",
                "message": "Getting 401 errors when calling the API",
            }),
            headers={
                "Content-Type": "application/json",
                "X-API-Key": API_KEY,
            },
        )

        assert response.status == 201
        data = await response.json()
        assert data["status"] == "open"
        assert "ticket_number" in data
        assert "ticket_id" in data

        return data["ticket_id"]

    async def test_ticket_lifecycle(self, page: Page):
        """Test full ticket lifecycle: create → add message → escalate → resolve."""
        # 1. Create ticket
        ticket_response = await page.request.post(
            f"{BASE_URL}/tickets",
            data=json.dumps({
                "name": "Test User",
                "email": "test@example.com",
                "subject": "Test ticket lifecycle",
                "category": "support",
                "priority": "low",
                "message": "Testing full lifecycle",
            }),
            headers={
                "Content-Type": "application/json",
                "X-API-Key": API_KEY,
            },
        )
        assert ticket_response.status == 201
        ticket = await ticket_response.json()
        ticket_id = ticket["ticket_id"]

        # 2. Get ticket details
        details_response = await page.request.get(
            f"{BASE_URL}/tickets/{ticket_id}",
            headers={"X-API-Key": API_KEY},
        )
        assert details_response.status == 200
        details = await details_response.json()
        assert details["status"] == "open"
        assert len(details["messages"]) > 0

        # 3. Add reply to ticket
        reply_response = await page.request.post(
            f"{BASE_URL}/tickets/{ticket_id}/reply",
            data=json.dumps({"message": "Thank you for your inquiry, we're looking into it."}),
            headers={
                "Content-Type": "application/json",
                "X-API-Key": API_KEY,
            },
        )
        assert reply_response.status == 201
        reply = await reply_response.json()
        assert "message_id" in reply

        # 4. Update ticket status
        status_response = await page.request.patch(
            f"{BASE_URL}/tickets/{ticket_id}/status",
            data=json.dumps({"status": "in_progress"}),
            headers={
                "Content-Type": "application/json",
                "X-API-Key": API_KEY,
            },
        )
        assert status_response.status == 200
        status_update = await status_response.json()
        assert status_update["status"] == "in_progress"

        # 5. Close ticket
        close_response = await page.request.patch(
            f"{BASE_URL}/tickets/{ticket_id}/status",
            data=json.dumps({"status": "resolved"}),
            headers={
                "Content-Type": "application/json",
                "X-API-Key": API_KEY,
            },
        )
        assert close_response.status == 200

    async def test_customer_history_retrieval(self, page: Page):
        """Test retrieving customer ticket history."""
        customer_email = "history@example.com"

        # Create multiple tickets for same customer
        for i in range(3):
            await page.request.post(
                f"{BASE_URL}/tickets",
                data=json.dumps({
                    "name": "History Test",
                    "email": customer_email,
                    "subject": f"Issue #{i+1}",
                    "category": "support",
                    "priority": "low",
                    "message": f"Support issue number {i+1}",
                }),
                headers={
                    "Content-Type": "application/json",
                    "X-API-Key": API_KEY,
                },
            )

        # Get history
        history_response = await page.request.get(
            f"{BASE_URL}/customers/{customer_email}/history",
            headers={"X-API-Key": API_KEY},
        )

        assert history_response.status == 200
        history = await history_response.json()
        assert history["email"] == customer_email
        assert history["count"] >= 3

    async def test_knowledge_base_search(self, page: Page):
        """Test knowledge base search functionality."""
        # First, ingest an article
        ingest_response = await page.request.post(
            f"{BASE_URL}/knowledge-base/ingest",
            data=json.dumps({
                "title": "Getting Started with Connectors",
                "content": "This guide explains how to set up and configure data connectors...",
                "category": "onboarding",
                "tags": ["connectors", "setup", "guide"],
                "embedding": [],
            }),
            headers={
                "Content-Type": "application/json",
                "X-API-Key": API_KEY,
            },
        )
        assert ingest_response.status == 201

        # Search for it
        search_response = await page.request.get(
            f"{BASE_URL}/knowledge-base?q=how+to+set+up+connectors&limit=5",
            headers={"X-API-Key": API_KEY},
        )
        assert search_response.status == 200
        results = await search_response.json()
        assert "query" in results
        assert "results" in results
        assert "count" in results

    async def test_dashboard_metrics(self, page: Page):
        """Test dashboard metrics endpoint."""
        metrics_response = await page.request.get(
            f"{BASE_URL}/metrics/dashboard",
            headers={"X-API-Key": API_KEY},
        )

        assert metrics_response.status == 200
        metrics = await metrics_response.json()
        assert "status_counts" in metrics
        assert "avg_resolution_hours" in metrics
        assert "escalation_rate_percent" in metrics
        assert "channel_counts" in metrics
        assert "total_tickets" in metrics


class TestMultiChannelFlow:
    """Test multi-channel message handling."""

    async def test_channel_diversity(self, page: Page):
        """Test that system handles multiple channels correctly."""
        channels_data = [
            {
                "channel": "email",
                "name": "Email User",
                "email": "email@example.com",
            },
            {
                "channel": "webform",
                "name": "Web Form User",
                "email": "webform@example.com",
            },
        ]

        for channel_info in channels_data:
            response = await page.request.post(
                f"{BASE_URL}/tickets",
                data=json.dumps({
                    "name": channel_info["name"],
                    "email": channel_info["email"],
                    "subject": f"Test from {channel_info['channel']}",
                    "category": "support",
                    "priority": "medium",
                    "message": f"Message from {channel_info['channel']} channel",
                }),
                headers={
                    "Content-Type": "application/json",
                    "X-API-Key": API_KEY,
                },
            )
            assert response.status == 201


class TestErrorHandling:
    """Test error handling and edge cases."""

    async def test_invalid_api_key(self, page: Page):
        """Test that invalid API key is rejected."""
        response = await page.request.post(
            f"{BASE_URL}/tickets",
            data=json.dumps({
                "name": "Test",
                "email": "test@example.com",
                "subject": "Test",
                "category": "support",
                "priority": "low",
                "message": "Test",
            }),
            headers={
                "Content-Type": "application/json",
                "X-API-Key": "invalid-key",
            },
        )
        assert response.status == 401

    async def test_missing_required_fields(self, page: Page):
        """Test that missing required fields are rejected."""
        response = await page.request.post(
            f"{BASE_URL}/tickets",
            data=json.dumps({
                "name": "Test",
                # Missing email
                "subject": "Test",
            }),
            headers={
                "Content-Type": "application/json",
                "X-API-Key": API_KEY,
            },
        )
        assert response.status in (400, 422)

    async def test_nonexistent_ticket(self, page: Page):
        """Test accessing a nonexistent ticket."""
        response = await page.request.get(
            f"{BASE_URL}/tickets/00000000-0000-0000-0000-000000000000",
            headers={"X-API-Key": API_KEY},
        )
        assert response.status == 404


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
