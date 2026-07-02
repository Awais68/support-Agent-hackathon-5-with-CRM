"""Locust load testing suite for TechFlow CRM Digital FTE.

Models all three webhook channels as weighted HttpUser tasks:
  - Web form submissions (weight=2 → targets 100+ in test run)
  - Gmail webhook payloads  (weight=1 → targets 50+)
  - WhatsApp webhook payloads (weight=1 → targets 50+)

Tracks P95 latency (<3s target) and escalation-vs-resolved ratio (<25% target).
"""

import json
import logging
import random
from dataclasses import dataclass, field
from typing import Any, Dict, List

from locust import HttpUser, events, task, between

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Event-level accumulators (reset per test)
# ---------------------------------------------------------------------------

@dataclass
class PerChannel:
    count: int = 0
    failures: int = 0
    slow_count: int = 0  # >3s
    latencies: List[float] = field(default_factory=list)
    escalated: int = 0
    resolved: int = 0

_channel_stats: Dict[str, PerChannel] = {}
_slow_requests: List[dict] = []


def _ensure(name: str) -> PerChannel:
    if name not in _channel_stats:
        _channel_stats[name] = PerChannel()
    return _channel_stats[name]


# ---------------------------------------------------------------------------
# Request hook – fires on every HTTP response
# ---------------------------------------------------------------------------

@events.request.add_listener
def _on_request(
    request_type: str,
    name: str,
    response_time: float,
    response_length: int,
    response: Any,
    context: Any,
    exception: Any = None,
    **kwargs,
):
    if exception:
        return

    channel = context.get("channel", "unknown") if context else "unknown"
    cs = _ensure(channel)
    cs.count += 1
    cs.latencies.append(response_time)

    if response_time > 3000:
        cs.slow_count += 1
        _slow_requests.append(
            {"method": request_type, "path": name, "latency_ms": f"{response_time:.0f}"}
        )

    try:
        body = response.json()
    except Exception:
        return

    # Track escalation / resolution from ticket status in response bodies
    status = body.get("status") if isinstance(body, dict) else None
    if status == "escalated":
        cs.escalated += 1
    elif status == "resolved":
        cs.resolved += 1

    # For webform responses that include ticket_number but not status,
    # we check the message content for escalation keywords as a proxy
    if channel == "webform" and isinstance(body, dict) and "ticket_number" in body:
        message = body.get("message", "")
        if any(kw in message.lower() for kw in ("escalat", "urgent", "critical")):
            cs.escalated += 1


# ---------------------------------------------------------------------------
# Test-data scenarios (mirroring payload shapes from api/main.py &
# channels/web_form_handler.py)
# ---------------------------------------------------------------------------

WEBFORM_SCENARIOS: List[Dict[str, str]] = [
    {"subject": "How do I connect my data source?",
     "message": "I'm trying to connect my PostgreSQL database to TechFlow but I can't find the connection settings. Can you help?",
     "category": "technical"},
    {"subject": "Billing question about duplicate charge",
     "message": "I was charged twice this month. Can you help me get a refund for the duplicate charge?",
     "category": "billing"},
    {"subject": "Dashboard not loading properly",
     "message": "The analytics dashboard has been loading for over 5 minutes. It was working fine yesterday.",
     "category": "technical"},
    {"subject": "Need to cancel subscription",
     "message": "I need to cancel my subscription due to budget cuts. Please process the cancellation.",
     "category": "billing"},
    {"subject": "New feature request for Snowflake",
     "message": "It would be great if you could add support for Snowflake as a data source connector.",
     "category": "feedback"},
    {"subject": "User permissions issue",
     "message": "My team members can't access the reports I created. How do I set up proper permissions?",
     "category": "technical"},
    {"subject": "Data export problem with PDF",
     "message": "When I try to export my dashboard as PDF, the charts are blurry. Is there a known fix?",
     "category": "bug"},
    {"subject": "Trial extension request",
     "message": "We need more time to evaluate the platform. Can you extend our trial by 2 weeks?",
     "category": "general"},
    {"subject": "API rate limits during batch processing",
     "message": "We're hitting API rate limits during our nightly batch processing window. Can we get a temporary increase?",
     "category": "technical"},
    {"subject": "Integration with Slack",
     "message": "How do I set up Slack notifications for dashboard alerts? I followed the docs but it failed.",
     "category": "technical"},
]

GMAIL_SCENARIOS: List[Dict[str, str]] = [
    {"subject": "Enterprise plan pricing inquiry",
     "message": "Dear TechFlow Support,\n\nI am interested in your enterprise plan. Please provide details on data source compatibility, user management, and compliance certifications.\n\nBest regards,\nSarah Johnson\nCTO, HealthData Inc.",
     "category": "general"},
    {"subject": "Connector configuration failure",
     "message": "Hello,\n\nWe've been trying to configure the Google Analytics connector for 3 days but keep getting auth errors. The error says 'token_expired' even after regenerating the API key.\n\nRegards,\nMike Chen",
     "category": "technical"},
    {"subject": "Data processing agreement review",
     "message": "To Whom It May Concern,\n\nOur legal team has concerns about the data residency clause in your DPA. As a financial institution we require EU-only data storage. Please confirm compliance.\n\nSincerely,\nJames Wilson\nLegal Counsel",
     "category": "general"},
    {"subject": "Account downgrade request",
     "message": "Hello Support,\n\nDue to budget cuts we need to downgrade from Professional to Starter. Please advise on feature loss and data retention.\n\nThank you,\nEmily Park",
     "category": "general"},
    {"subject": "Performance optimization for 50M+ row datasets",
     "message": "Hi TechFlow Team,\n\nDashboard load times increased from 2s to 45s on datasets >50M rows. We have 15 concurrent users and 8 widgets. Please suggest optimization strategies.\n\nBest,\nDr. Alan Turing",
     "category": "technical"},
]

WHATSAPP_SCENARIOS: List[str] = [
    "Hey, my dashboard is broken. It shows no data since yesterday.",
    "Can I get a refund for last month? I didn't use the service at all.",
    "Your pricing is too high. Any discounts available?",
    "Need help resetting my password. The link is not working.",
    "How do I add more users to my account?",
    "Is there a mobile app for TechFlow?",
    "My chart colors look weird after the update.",
    "Can you extend my trial by a week?",
    "The export feature is not working on mobile.",
    "I'm getting a 503 error when I try to load reports.",
    "How do I delete my account?",
    "Your support team is amazing! Quick response.",
    "The new AI insights feature is really helpful.",
    "When will the new dashboard theme be available?",
    "I lost access to my account after changing email.",
    "Can I integrate TechFlow with Zapier?",
    "My team needs read-only access to reports.",
    "The CSV export truncates decimal values.",
    "How do I set up email alerts for anomalies?",
    "Please escalate this to your manager immediately.",
]

# ---------------------------------------------------------------------------
# Locust user
# ---------------------------------------------------------------------------

class CRMChannelUser(HttpUser):
    """Simulates traffic across all three support channels.

    Task weights (2:1:1) deliver approximately:
      - 100+ web form submissions
      - 50+ Gmail messages
      - 50+ WhatsApp messages
    """

    wait_time = between(0.5, 2.5)

    def _pick_email(self, channel: str) -> str:
        idx = random.randint(1, 99999)
        return f"{channel}{idx}@example.com"

    # ---- Web form (POST /webhooks/webform) ----
    @task(2)
    def webform_submission(self):
        scenario = random.choice(WEBFORM_SCENARIOS)
        payload = {
            "name": f"Web User {random.randint(1, 99999)}",
            "email": self._pick_email("webuser"),
            "subject": scenario["subject"],
            "message": scenario["message"],
            "category": scenario["category"],
            "priority": random.choice(["low", "medium", "high", "urgent", "critical"]),
            "company": random.choice([None, "Acme Corp", "Globex Inc", "Initech"]),
        }
        with self.client.post(
            "/webhooks/webform",
            json=payload,
            catch_response=True,
            context={"channel": "webform"},
        ) as resp:
            if resp.elapsed.total_seconds() > 3:
                resp.failure(
                    f"Latency {resp.elapsed.total_seconds() * 1000:.0f}ms exceeds 3s P95 target"
                )
            elif resp.status_code == 201:
                resp.success()
            else:
                resp.failure(f"Unexpected status {resp.status_code}")

    # ---- Gmail (simulated via POST /tickets) ----
    @task(1)
    def gmail_webhook(self):
        scenario = random.choice(GMAIL_SCENARIOS)
        payload = {
            "name": f"Gmail User {random.randint(1, 99999)}",
            "email": self._pick_email("gmailuser"),
            "subject": scenario["subject"],
            "message": scenario["message"],
            "category": scenario["category"],
            "priority": random.choice(["low", "medium", "high"]),
        }
        with self.client.post(
            "/tickets",
            json=payload,
            catch_response=True,
            context={"channel": "gmail"},
        ) as resp:
            if resp.elapsed.total_seconds() > 3:
                resp.failure(
                    f"Latency {resp.elapsed.total_seconds() * 1000:.0f}ms exceeds 3s P95 target"
                )
            elif resp.status_code == 201:
                resp.success()
                body = resp.json()
                # Track escalation from ticket status returned in CreateTicketResponse
                if body.get("status") == "escalated":
                    _ensure("gmail").escalated += 1
            else:
                resp.failure(f"Unexpected status {resp.status_code}")

    # ---- WhatsApp (POST /webhooks/whatsapp, form-encoded) ----
    @task(1)
    def whatsapp_webhook(self):
        msg = random.choice(WHATSAPP_SCENARIOS)
        idx = random.randint(1, 99999)
        payload = {
            "From": f"whatsapp:+1555{1000 + idx:07d}",
            "Body": msg,
            "MessageSid": f"SM{idx:015d}",
        }
        with self.client.post(
            "/webhooks/whatsapp",
            data=payload,
            catch_response=True,
            context={"channel": "whatsapp"},
        ) as resp:
            if resp.elapsed.total_seconds() > 3:
                resp.failure(
                    f"Latency {resp.elapsed.total_seconds() * 1000:.0f}ms exceeds 3s P95 target"
                )
            elif resp.status_code == 200:
                resp.success()
                body = resp.json()
                # WhatsApp returns {"status": "received"} — track escalations
                # by checking the message body for escalation keywords
                if any(kw in msg.lower() for kw in ("escalat", "legal", "sue", "lawyer", "attorney")):
                    _ensure("whatsapp").escalated += 1
            else:
                resp.failure(f"Unexpected status {resp.status_code}")

    def on_stop(self):
        """Print summary statistics at end of test run."""
        print("\n" + "=" * 70)
        print("  LOAD TEST RESULTS — ESCALATION & P95 LATENCY REPORT")
        print("=" * 70)

        for channel in ("webform", "gmail", "whatsapp"):
            cs = _channel_stats.get(channel)
            if not cs or cs.count == 0:
                continue

            lat = sorted(cs.latencies)
            p50 = lat[len(lat) // 2] if lat else 0.0
            p95 = lat[int(len(lat) * 0.95)] if lat else 0.0
            p99 = lat[int(len(lat) * 0.99)] if lat else 0.0

            error_rate = cs.failures / cs.count * 100 if cs.count else 0.0
            total_tickets = cs.count - cs.escalated  # rough proxy
            escal_pct = (cs.escalated / max(total_tickets, 1)) * 100

            print(f"\n  [{channel.upper()}]")
            print(f"    Requests         : {cs.count}")
            print(f"    Failures         : {cs.failures} ({error_rate:.1f}%)")
            print(f"    >3s slow         : {cs.slow_count}")
            print(f"    P50 / P95 / P99  : {p50:.0f} / {p95:.0f} / {p99:.0f} ms")
            print(f"    Escalated        : {cs.escalated}")
            print(f"    Resolved         : {cs.resolved}")
            print(f"    Escalation rate  : {escal_pct:.1f}%  {'✅' if escal_pct < 25 else '❌'} target <25%")
            print(f"    P95 check        : {'✅' if p95 < 3000 else '❌'} target <3000ms")

        if _slow_requests:
            print(f"\n  SLOW REQUESTS (>3s): {len(_slow_requests)} total")
            for sr in _slow_requests[:20]:
                print(f"    {sr['method']:6s} {sr['path']:40s} {sr['latency_ms']:>8s} ms")
            if len(_slow_requests) > 20:
                print(f"    ... and {len(_slow_requests) - 20} more")

        print("=" * 70 + "\n")
