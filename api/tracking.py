"""Per-ticket tracking tokens for the public, customer-facing ticket view.

A customer proves they may see a ticket with either the tracking token handed
out at submission time or the email the ticket was filed under. The token is
an HMAC of the ticket's internal UUID, so it can't be derived from the
(guessable) ticket number and needs no database column.
"""

import hashlib
import hmac
import os
import re
from uuid import UUID

# TKT-YYYYMMDD-<suffix>. New tickets use 6 hex chars; seeded demo tickets use
# shorter alphanumeric suffixes, hence the 4-12 range.
TICKET_NUMBER_RE = re.compile(r"^TKT-\d{8}-[A-Z0-9]{4,12}$")
TOKEN_RE = re.compile(r"^[0-9a-f]{32}$")
_DERIVATION_LABEL = b"ticket-tracking-v1"


def _secret() -> bytes | None:
    explicit = os.getenv("TRACKING_TOKEN_SECRET")
    if explicit:
        return explicit.encode()
    api_key = os.getenv("API_KEY") or os.getenv("API_KEY_SECRET")
    if api_key:
        # Derived, so the master key itself never leaves the server.
        return hmac.new(api_key.encode(), _DERIVATION_LABEL, hashlib.sha256).digest()
    return None


def tracking_token(ticket_id: UUID | str) -> str | None:
    """Token for one ticket, or None when no secret is configured."""
    secret = _secret()
    if secret is None:
        return None
    mac = hmac.new(secret, str(ticket_id).lower().encode(), hashlib.sha256)
    return mac.hexdigest()[:32]


def verify_tracking_token(ticket_id: UUID | str, token: str | None) -> bool:
    if not token or not TOKEN_RE.match(token):
        return False
    expected = tracking_token(ticket_id)
    return expected is not None and hmac.compare_digest(expected, token)


def normalize_email(email: str | None) -> str:
    return (email or "").strip().lower()


def email_matches(stored: str | None, supplied: str | None) -> bool:
    a, b = normalize_email(stored), normalize_email(supplied)
    return bool(a and b) and hmac.compare_digest(a.encode(), b.encode())
