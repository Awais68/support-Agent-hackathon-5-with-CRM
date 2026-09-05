"""SpecifyPlus integration client (Python).

Drop-in client for the TechFlow CRM Digital FTE API. Only dependency: `requests`.

Usage:
    from specifyplus_client import SpecifyPlusClient

    client = SpecifyPlusClient(base_url="http://localhost:8000", api_key="test-key-12345")

    ticket = client.create_ticket(
        name="Ali",
        email="ali@example.com",
        subject="Cannot reset password",
        message="I need help resetting my account password.",
        category="onboarding",
        priority="high",
    )
    print(ticket["ticket_number"])

Webhook endpoints (/webhooks/*, /health) skip API-key auth. Everything else
requires the X-API-Key header.
"""

from __future__ import annotations

from typing import Any

import requests


class SpecifyPlusClient:
    def __init__(self, base_url: str, api_key: str, timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {"X-API-Key": api_key, "Content-Type": "application/json"}
        )

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        resp = self.session.request(
            method, self._url(path), timeout=self.timeout, **kwargs
        )
        resp.raise_for_status()
        return resp.json()

    # --- System ---

    def health(self) -> dict[str, str]:
        """GET /health — system status. No API key needed."""
        return self._request("GET", "/health")

    def dashboard_metrics(self) -> dict[str, Any]:
        """GET /metrics/dashboard — aggregated KPIs."""
        return self._request("GET", "/metrics/dashboard")

    # --- Tickets ---

    def create_ticket(
        self,
        name: str,
        email: str,
        subject: str,
        message: str,
        category: str = "general",
        priority: str = "medium",
    ) -> dict[str, Any]:
        """POST /tickets — create a new support ticket."""
        return self._request(
            "POST",
            "/tickets",
            json={
                "name": name,
                "email": email,
                "subject": subject,
                "message": message,
                "category": category,
                "priority": priority,
            },
        )

    def list_tickets(
        self, status: str | None = None, limit: int = 20, offset: int = 0
    ) -> dict[str, Any]:
        """GET /tickets — list tickets with optional status filter."""
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if status:
            params["status"] = status
        return self._request("GET", "/tickets", params=params)

    def get_ticket(self, ticket_id: str) -> dict[str, Any]:
        """GET /tickets/{id} — full ticket incl. messages + agent runs."""
        return self._request("GET", f"/tickets/{ticket_id}")

    def update_status(self, ticket_id: str, status: str) -> dict[str, Any]:
        """PATCH /tickets/{id}/status — open|in_progress|resolved|escalated|closed."""
        return self._request(
            "PATCH", f"/tickets/{ticket_id}/status", json={"status": status}
        )

    def reply_to_ticket(self, ticket_id: str, message: str) -> dict[str, Any]:
        """POST /tickets/{id}/reply — append a message to the conversation."""
        return self._request(
            "POST", f"/tickets/{ticket_id}/reply", json={"message": message}
        )

    def ticket_messages(self, ticket_id: str, limit: int = 50) -> dict[str, Any]:
        """GET /tickets/{id}/messages — conversation history."""
        return self._request(
            "GET", f"/tickets/{ticket_id}/messages", params={"limit": limit}
        )

    # --- Customers & Knowledge Base ---

    def customer_history(
        self,
        email: str,
        limit: int = 10,
        include_resolved: bool = True,
    ) -> dict[str, Any]:
        """GET /customers/{email}/history — past interactions for a customer."""
        return self._request(
            "GET",
            f"/customers/{email}/history",
            params={"limit": limit, "include_resolved": include_resolved},
        )

    def search_knowledge_base(
        self,
        query: str,
        category: str | None = None,
        limit: int = 5,
        customer_tier: str = "starter",
    ) -> dict[str, Any]:
        """GET /knowledge-base — pgvector similarity search."""
        params: dict[str, Any] = {
            "q": query,
            "limit": limit,
            "customer_tier": customer_tier,
        }
        if category:
            params["category"] = category
        return self._request("GET", "/knowledge-base", params=params)

    def submit_webform(
        self,
        name: str,
        email: str,
        subject: str,
        message: str,
        category: str = "general",
        priority: str = "medium",
    ) -> dict[str, Any]:
        """POST /webhooks/webform — public submission, no API key needed."""
        payload = {
            "name": name,
            "email": email,
            "subject": subject,
            "message": message,
            "category": category,
            "priority": priority,
        }
        return requests.post(self._url("/webhooks/webform"), json=payload, timeout=self.timeout).json()


if __name__ == "__main__":
    import os

    client = SpecifyPlusClient(
        base_url=os.getenv("SPECIFYPLUS_URL", "http://localhost:8000"),
        api_key=os.getenv("SPECIFYPLUS_API_KEY", "test-key-12345"),
    )

    print("health:", client.health())

    ticket = client.create_ticket(
        name="Ali",
        email="ali@example.com",
        subject="Cannot reset password",
        message="I need help resetting my account password.",
        category="onboarding",
        priority="high",
    )
    print("created:", ticket)

    tid = ticket["ticket_id"]
    print("status update:", client.update_status(tid, "in_progress"))
    print("reply:", client.reply_to_ticket(tid, "We've emailed you a reset link."))
    print("kb search:", client.search_knowledge_base("reset password"))