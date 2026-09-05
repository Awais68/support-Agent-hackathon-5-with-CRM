"""Load test script for TechFlow CRM Digital FTE.

Simulates the grading scenario:
- 100+ web form submissions
- 50+ Gmail messages
- 50+ WhatsApp messages
- Pods killed every 2 hours during grading
- Target: 99.9% uptime, P95 <3s latency, <25% escalation rate

Usage:
    python scripts/load_test.py --base-url http://localhost:8000 --requests 200 --concurrency 10

Environment variables:
    API_KEY: API key for authentication (default: "test-api-key-2024")
"""

import argparse
import asyncio
import json
import random
import time
import os
from dataclasses import dataclass
from typing import Optional

import aiohttp
import structlog

logger = structlog.get_logger(__name__)

API_KEY = os.getenv("API_KEY", "test-api-key-2024")


# --- Test data generators ---

WEBFORM_SCENARIOS = [
    {"subject": "How do I connect my data source?", "body": "I'm trying to connect my PostgreSQL database to TechFlow but I can't find the connection settings. Can you help?", "category": "technical"},
    {"subject": "Billing question", "body": "I was charged twice this month. Can you help me get a refund for the duplicate charge?", "category": "billing"},
    {"subject": "Dashboard not loading", "body": "The analytics dashboard has been loading for over 5 minutes. It was working yesterday.", "category": "technical"},
    {"subject": "Need to cancel subscription", "body": "I need to cancel my subscription due to budget cuts. Please process the cancellation.", "category": "account"},
    {"subject": "New feature request", "body": "It would be great if you could add support for Snowflake as a data source.", "category": "feature_request"},
    {"subject": "User permissions issue", "body": "My team members can't access the reports I created. How do I set up proper permissions?", "category": "technical"},
    {"subject": "Data export problem", "body": "When I try to export my dashboard as PDF, the charts are blurry. Is there a fix?", "category": "technical"},
    {"subject": "Trial extension", "body": "We need more time to evaluate the platform. Can you extend our trial by 2 weeks?", "category": "account"},
    {"subject": "API rate limits", "body": "We're hitting API rate limits during our batch processing window. Can we get a temporary increase?", "category": "technical"},
    {"subject": "Integration with Slack", "body": "How do I set up Slack notifications for dashboard alerts?", "category": "technical"},
]

GMAIL_SCENARIOS = [
    {
        "subject": "Detailed inquiry about enterprise plan pricing and feature set",
        "body": "Dear TechFlow Support Team,\n\nI am writing to inquire about your enterprise plan. Our organization is currently evaluating analytics platforms and we are particularly interested in understanding your data source compatibility, user management capabilities, and compliance certifications.\n\nCould you please provide detailed information about:\n1. Number of supported data sources in the enterprise tier\n2. SAML/SSO integration options\n3. SOC 2 compliance status\n4. Custom dashboard branding options\n\nWe would also appreciate a demo tailored to our use case in the healthcare analytics space.\n\nThank you for your time and assistance.\n\nBest regards,\nSarah Johnson\nCTO, HealthData Inc.",
        "category": "sales",
    },
    {
        "subject": "Technical support request - Connector configuration failure",
        "body": "Hello,\n\nWe have been trying to configure the Google Analytics connector for the past 3 days but keep getting an authentication error. The error message says 'token_expired' even after regenerating the API key.\n\nSteps we have tried:\n- Regenerated OAuth credentials\n- Cleared cache and cookies\n- Tried from a different browser\n- Checked firewall settings\n\nOur setup:\n- TechFlow version: 2.4.1\n- Google Analytics: UA-123456789\n- Browser: Chrome 120\n\nPlease advise on next steps. This is blocking our weekly reporting.\n\nRegards,\nMike Chen\nData Engineering Lead",
        "category": "technical",
    },
    {
        "subject": "Legal notice - Data processing agreement",
        "body": "To Whom It May Concern,\n\nOur legal team has reviewed your standard Data Processing Agreement and we have some concerns regarding the data residency clause. As a financial institution, we are required to ensure all data remains within EU borders.\n\nPlease provide:\n1. Confirmation of EU-only data storage\n2. List of sub-processors\n3. Incident response SLA\n4. Data deletion certification process\n\nWe appreciate your prompt attention to this matter.\n\nSincerely,\nJames Wilson\nLegal Counsel, EuroBank Ltd.",
        "category": "legal",
    },
    {
        "subject": "Account downgrade request due to budget constraints",
        "body": "Hello Support,\n\nDue to recent budget cuts, we need to downgrade from our Professional plan to the Starter plan. We understand we will lose some features but our core requirement is basic dashboarding.\n\nPlease let us know:\n- The process for downgrading\n- What features we will lose\n- Whether we can keep our existing dashboards\n- Data retention policy after downgrade\n\nWe would like to complete this transition before the next billing cycle on March 1st.\n\nThank you,\nEmily Park\nOperations Manager",
        "category": "account",
    },
    {
        "subject": "Performance optimization for large datasets - seeking guidance",
        "body": "Hi TechFlow Team,\n\nWe are experiencing significant performance degradation when querying datasets larger than 50 million rows. Dashboard load times have increased from 2 seconds to over 45 seconds.\n\nOur current configuration:\n- Dataset size: ~80M rows\n- Number of concurrent users: 15\n- Refresh frequency: Every 15 minutes\n- Number of dashboard widgets: 8\n\nCould you suggest optimization strategies? We are considering:\n- Data aggregation tables\n- Query caching\n- Scheduled refresh vs real-time\n- Vertical vs horizontal scaling\n\nAny best practices documentation or direct guidance would be greatly appreciated.\n\nBest,\nDr. Alan Turing\nData Science Lead, QuantCo",
        "category": "technical",
    },
]

WHATSAPP_SCENARIOS = [
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
    "Are you hiring data scientists?",
    "Your support team is amazing! Quick response.",
    "The new AI insights feature is really helpful.",
    "When will the new dashboard theme be available?",
    "I lost access to my account after changing email.",
    "Can I integrate TechFlow with Zapier?",
    "My team needs read-only access to reports.",
    "The CSV export truncates decimal values.",
    "How do I set up email alerts for anomalies?",
    "I'm a lawyer and I need to review your terms of service.",
    "We're planning to sue if our data breach isn't addressed.",
    "Please escalate this to your manager immediately.",
]

LEGAL_KEYWORDS = ["lawyer", "legal", "sue", "attorney", "lawsuit", "litigation"]
PRICING_KEYWORDS = ["pricing", "refund", "discount", "cancel", "billing", "charge"]


@dataclass
class TestResult:
    channel: str
    status_code: int
    latency_ms: float
    success: bool


async def send_webform(session: aiohttp.ClientSession, base_url: str, scenario: dict, idx: int) -> TestResult:
    """Send a web form submission."""
    payload = {
        "name": f"Web User {idx}",
        "email": f"webuser{idx}@example.com",
        "subject": scenario["subject"],
        "message": scenario["body"],
        "category": scenario["category"],
        "priority": random.choice(["low", "medium", "high"]),
    }
    start = time.monotonic()
    try:
        async with session.post(f"{base_url}/webhooks/webform", json=payload) as resp:
            latency = (time.monotonic() - start) * 1000
            return TestResult(
                channel="webform",
                status_code=resp.status,
                latency_ms=latency,
                success=200 <= resp.status < 500,
            )
    except Exception:
        latency = (time.monotonic() - start) * 1000
        return TestResult(channel="webform", status_code=0, latency_ms=latency, success=False)


async def send_gmail(session: aiohttp.ClientSession, base_url: str, scenario: dict, idx: int) -> TestResult:
    """Simulate a Gmail message via the tickets API (Gmail is poll-based, not webhook)."""
    payload = {
        "name": f"Gmail User {idx}",
        "email": f"gmailuser{idx}@company.com",
        "subject": scenario["subject"],
        "message": scenario["body"],
        "category": scenario["category"],
        "priority": random.choice(["low", "medium", "high"]),
    }
    headers = {"X-API-Key": API_KEY}
    start = time.monotonic()
    try:
        async with session.post(f"{base_url}/tickets", json=payload, headers=headers) as resp:
            latency = (time.monotonic() - start) * 1000
            return TestResult(
                channel="gmail",
                status_code=resp.status,
                latency_ms=latency,
                success=200 <= resp.status < 500,
            )
    except Exception:
        latency = (time.monotonic() - start) * 1000
        return TestResult(channel="gmail", status_code=0, latency_ms=latency, success=False)


async def send_whatsapp(session: aiohttp.ClientSession, base_url: str, body: str, idx: int) -> TestResult:
    """Send a WhatsApp message via Twilio webhook."""
    payload = aiohttp.FormData()
    payload.add_field("From", f"whatsapp:+1555{1000 + idx:07d}")
    payload.add_field("Body", body)
    payload.add_field("MessageSid", f"SM{idx:015d}")
    start = time.monotonic()
    try:
        async with session.post(f"{base_url}/webhooks/whatsapp", data=payload) as resp:
            latency = (time.monotonic() - start) * 1000
            return TestResult(
                channel="whatsapp",
                status_code=resp.status,
                latency_ms=latency,
                success=200 <= resp.status < 500,
            )
    except Exception:
        latency = (time.monotonic() - start) * 1000
        return TestResult(channel="whatsapp", status_code=0, latency_ms=latency, success=False)


async def run_load_test(base_url: str, total_requests: int, concurrency: int):
    """Run the full load test."""
    sem = asyncio.Semaphore(concurrency)
    results: list[TestResult] = []
    errors = 0
    lock = asyncio.Lock()

    async def worker(channel: str, idx: int):
        nonlocal errors
        async with sem:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
                try:
                    if channel == "webform":
                        scenario = random.choice(WEBFORM_SCENARIOS)
                        result = await send_webform(session, base_url, scenario, idx)
                    elif channel == "gmail":
                        scenario = random.choice(GMAIL_SCENARIOS)
                        result = await send_gmail(session, base_url, scenario, idx)
                    elif channel == "whatsapp":
                        body = random.choice(WHATSAPP_SCENARIOS)
                        result = await send_whatsapp(session, base_url, body, idx)
                    else:
                        return

                    async with lock:
                        results.append(result)
                        if not result.success:
                            errors += 1

                    status = "OK" if result.success else "FAIL"
                    logger.info(
                        "Request complete",
                        channel=channel,
                        status_code=result.status_code,
                        latency=f"{result.latency_ms:.0f}ms",
                        status=status,
                    )

                except Exception as e:
                    async with lock:
                        results.append(TestResult(channel=channel, status_code=0, latency_ms=0, success=False))
                        errors += 1
                    logger.error("Request error", channel=channel, error=str(e))

    # Build task list: 100+ web, 50+ gmail, 50+ whatsapp
    tasks = []
    web_count = max(100, total_requests // 2)
    gmail_count = max(50, total_requests // 4)
    whatsapp_count = max(50, total_requests // 4)

    for i in range(web_count):
        tasks.append(worker("webform", i))
    for i in range(gmail_count):
        tasks.append(worker("gmail", i))
    for i in range(whatsapp_count):
        tasks.append(worker("whatsapp", i))

    logger.info(
        "Starting load test",
        total=len(tasks),
        webform=web_count,
        gmail=gmail_count,
        whatsapp=whatsapp_count,
        concurrency=concurrency,
    )

    start_time = time.monotonic()
    await asyncio.gather(*tasks)
    elapsed = time.monotonic() - start_time

    # Compute stats
    latencies = [r.latency_ms for r in results if r.success]
    latencies.sort()
    p50 = latencies[len(latencies) // 2] if latencies else 0
    p95 = latencies[int(len(latencies) * 0.95)] if latencies else 0
    p99 = latencies[int(len(latencies) * 0.99)] if latencies else 0

    by_channel = {}
    for r in results:
        by_channel.setdefault(r.channel, []).append(r)

    logger.info("=" * 60)
    logger.info("LOAD TEST RESULTS")
    logger.info("=" * 60)
    logger.info("Summary", total=len(results), errors=errors, elapsed=f"{elapsed:.1f}s",
                throughput=f"{len(results)/elapsed:.1f} req/s")
    logger.info(f"  P50 latency: {p50:.0f}ms")
    logger.info(f"  P95 latency: {p95:.0f}ms")
    logger.info(f"  P99 latency: {p99:.0f}ms")
    logger.info(f"  Error rate: {errors/len(results)*100:.1f}%" if results else "  Error rate: 0%")

    for channel, channel_results in sorted(by_channel.items()):
        success = sum(1 for r in channel_results if r.success)
        ch_latencies = [r.latency_ms for r in channel_results if r.success]
        ch_latencies.sort()
        ch_p95 = ch_latencies[int(len(ch_latencies) * 0.95)] if ch_latencies else 0
        logger.info(f"  [{channel}] {success}/{len(channel_results)} OK, P95={ch_p95:.0f}ms")

    # Grade simulation
    escalation_count = sum(
        1 for r in results
        if r.success and "refund" in str(r) or "legal" in str(r) or "lawyer" in str(r) or "sue" in str(r)
    )
    logger.info(f"  Estimated escalation rate: {escalation_count/max(len(results),1)*100:.1f}%")

    # Return summary for programmatic use
    return {
        "total": len(results),
        "errors": errors,
        "elapsed_seconds": elapsed,
        "throughput": len(results) / elapsed if elapsed > 0 else 0,
        "p50_ms": p50,
        "p95_ms": p95,
        "p99_ms": p99,
        "error_rate": errors / len(results) * 100 if results else 0,
    }


def main():
    parser = argparse.ArgumentParser(description="Load test for TechFlow CRM Digital FTE")
    parser.add_argument("--base-url", default="http://localhost:8000", help="Base URL of the API")
    parser.add_argument("--requests", type=int, default=200, help="Total requests (auto-distributed)")
    parser.add_argument("--concurrency", type=int, default=10, help="Concurrent requests")
    args = parser.parse_args()

    asyncio.run(run_load_test(args.base_url, args.requests, args.concurrency))


if __name__ == "__main__":
    main()
