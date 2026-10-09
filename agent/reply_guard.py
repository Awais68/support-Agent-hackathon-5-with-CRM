"""Last-line check that an outbound reply is written *to* the customer.

The agent's final chat message is an internal note ("I've responded to the
customer..."). Only the text passed to ``send_response`` is meant for the
customer. This guard catches the case where internal or empty text would be
delivered anyway, so it can be blocked and the ticket escalated instead.
"""

import re

# Third-person references to the recipient, or narration of the agent's own
# actions. "the customer success team" / "customer portal" style phrases are
# customer-facing, so they are excluded.
_OPERATOR_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "refers to the recipient in the third person",
        re.compile(
            r"\b(?:the|this) (?:customer|user|client)(?:'s)?\b"
            r"(?!\s+(?:success|support|service|portal|dashboard|account|team|care))",
            re.IGNORECASE,
        ),
    ),
    (
        "narrates the agent's own actions",
        re.compile(
            r"\bI(?:'ve| have)\s+(?:already\s+)?"
            r"(?:responded|replied|sent (?:a|the|my) (?:response|reply|message)"
            r"|provided (?:a|the) (?:response|reply))\b",
            re.IGNORECASE,
        ),
    ),
    (
        "narrates the agent's own actions",
        re.compile(
            r"\b(?:responded|replied|sent (?:a|the) (?:response|reply)) to (?:them|him|her)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "mentions internal tooling",
        re.compile(r"\b(?:send_response|escalate_to_human|search_knowledge_base)\b"),
    ),
]


def operator_voice_reason(text: str | None) -> str | None:
    """Return why ``text`` must not be sent to a customer, or None if it is fine."""
    if not text or not text.strip():
        return "empty reply"
    for reason, pattern in _OPERATOR_PATTERNS:
        if pattern.search(text):
            return reason
    return None
