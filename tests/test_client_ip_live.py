"""Live: through the web form, rate limits follow the real browser address (S8).

The web form is a trusted proxy for the API (TRUSTED_PROXIES), so the API
keys its limits on the X-Forwarded-For the form sends. That is only safe
because client-ip.js overwrites the header with the TCP peer: a client that
rotates its own X-Forwarded-For must stay in one bucket, and two clients
with different addresses must not share one.

Needs the web form and API on this host with the API trusting the form's
address (CI: TRUSTED_PROXIES=127.0.0.1). Clients bind 127.0.0.2/127.0.0.3 so
the form sees two peers; behind Docker's port proxy every host client has the
gateway address, so the test only runs when E2E_CLIENT_IP_TEST=1.
"""

import os

import httpx
import pytest

pytestmark = pytest.mark.e2e

WEB_FORM_URL = os.getenv("WEB_FORM_URL", "http://localhost:3000")
STRICT_LIMIT = int(os.getenv("STRICT_RATE_LIMIT_PER_MINUTE", "10"))


@pytest.fixture(scope="module", autouse=True)
def _require_direct_stack():
    if os.getenv("E2E_CLIENT_IP_TEST") != "1":
        pytest.skip("set E2E_CLIENT_IP_TEST=1 with the form and API on this host")
    try:
        httpx.get(f"{WEB_FORM_URL}/", timeout=3)
    except httpx.HTTPError:
        pytest.skip(f"web form not reachable at {WEB_FORM_URL}")


def _client(local_address: str) -> httpx.Client:
    return httpx.Client(
        base_url=WEB_FORM_URL,
        transport=httpx.HTTPTransport(local_address=local_address),
        timeout=15,
    )


def _form(tag: str, i: int) -> dict[str, str]:
    return {
        "name": "Client IP Test",
        "email": f"client-ip-{tag}-{i}@example.com",
        "subject": "Rate limit key check",
        "message": "Checking which bucket this request is counted in.",
    }


def test_spoofed_header_does_not_change_the_bucket_but_another_client_does():
    with _client("127.0.0.2") as spoofer:
        codes = [
            spoofer.post(
                "/webhooks/webform",
                json=_form("a", i),
                headers={"X-Forwarded-For": f"192.0.2.{i}", "X-Real-IP": f"192.0.2.{i}"},
            ).status_code
            for i in range(STRICT_LIMIT + 1)
        ]
    # One bucket despite a new X-Forwarded-For on every request.
    assert codes[:STRICT_LIMIT] == [201] * STRICT_LIMIT, codes
    assert codes[-1] == 429, codes

    # A different browser is not caught by the first one's bucket.
    with _client("127.0.0.3") as other:
        resp = other.post("/webhooks/webform", json=_form("b", 0))
    assert resp.status_code == 201, resp.text[:200]
