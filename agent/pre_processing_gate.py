"""Pre-processing gate for hard business rule enforcement.

Must run BEFORE any agent processing. Checks:
1. Pricing/refund questions → escalate
2. "lawyer", "legal", "sue" (or clear paraphrases) → escalate
3. Angry or abusive wording, or sentiment score < 0.3 → escalate. The score
   ignores technical failure words ("error", "failed", "broken"), so a calm
   bug report is not mistaken for an upset customer.
4. Business-critical incidents (outage, data loss, security) → escalate
5. Customer asking about internal system details → deflect
6. Ticket must exist before response
"""

import re
from enum import Enum

import structlog

from agent.sentiment_analyzer import analyze_sentiment_detailed

logger = structlog.get_logger(__name__)


class GateAction(Enum):
    ALLOW = "allow"
    ESCALATE = "escalate"
    DEFLECT = "deflect"


class GateResult:
    def __init__(
        self,
        action: GateAction,
        reason: str = "",
        priority: str = "medium",
        sentiment_score: float | None = None,
        emotion: str = "neutral",
        urgency_score: float = 0.0,
        is_urgent: bool = False,
        aspect_scores: dict[str, float] | None = None,
        sentiment_drop_detected: bool = False,
        sentiment_drop_amount: float = 0.0,
    ):
        self.action = action
        self.reason = reason
        self.priority = priority
        self.sentiment_score = sentiment_score
        self.emotion = emotion
        self.urgency_score = urgency_score
        self.is_urgent = is_urgent
        self.aspect_scores = aspect_scores or {}
        self.sentiment_drop_detected = sentiment_drop_detected
        self.sentiment_drop_amount = sentiment_drop_amount


PRICING_REFUND_PATTERNS = [
    r"(?i)\b(price|pricing|cost|subscription fee|plan cost|how much)\b",
    r"(?i)\b(refund|money.back|reimburs|charge.back|cancel.*refund)\b",
    r"(?i)\b(bill|billing|invoice|charged|overcharge|double.charge)\b",
    r"(?i)\b(discount|coupon|promo|free.trial|can.i.get.*free)\b",
    r"(?i)\b(upgrade.*cost|downgrade.*refund|plan.*change.*fee)\b",
    r"(?i)\b(get my money|want my money|give me my money)\b",
]

LEGAL_PATTERNS = [
    r"(?i)\blawyer\b",
    r"(?i)\blegal\b",
    r"(?i)\bsue\b",
    r"(?i)\blawsuits?\b",
    r"(?i)\battorney\b",
    r"(?i)\blegal.action\b",
    r"(?i)\bcourt\b",
    r"(?i)\blitigat",
    r"(?i)\blegal.*(notice|threat|demand)\b",
    r"(?i)\b(cease|desist)\b",
    r"(?i)\b(seek\w*|claim\w*)\s+(damages|compensation)\b",
    r"(?i)\breport.*(you|techflow)\b.*\b(authorit|regulat|law)",
    r"(?i)\b(class.action)\b",
    r"(?i)\blegal.*(right|obligation|claim)\b",
]

# Hostile or abusive wording escalates on its own, whatever the overall score.
# Mild frustration ("annoying", "a bit frustrating") is deliberately absent:
# the agent can handle it.
HOSTILITY_PATTERNS = [
    r"(?i)\b(idiots?|stupid|morons?|dumb|crap|garbage|damn|shit\w*|bullshit|f+u+c+k\w*)\b",
    r"(?i)\b(pathetic|incompetent|useless|clowns?)\b",
    r"(?i)\b(angry|furious|outraged|pissed|livid|enraged|infuriat\w*)\b",
    r"(?i)\b(unacceptable|ridiculous|outrageous|absurd|disgusting)\b",
    r"(?i)\bworst\b",
    r"(?i)\b(is|are) a (joke|scam)\b",
    r"(?i)\b(sick of|fed up|had enough|how dare|shut up)\b",
    r"(?i)\bhow many times\b",
]

# Business-critical incidents escalate even when the customer is polite.
CRITICAL_INCIDENT_PATTERNS = [
    r"(?i)\b(production|prod|site|system|platform|service|everything|dashboards?)\b"
    r"[^.?!]{0,40}\b(is|are|has been|have been|went|gone)\s+down\b",
    r"(?i)\boutage\b",
    r"(?i)\b(lost|losing|loss of)\b[^.?!]{0,25}\bdata\b|\bdata\s+loss\b",
    r"(?i)\bdata\b[^.?!]{0,30}\b(disappeared|deleted|gone|vanished|wiped)\b",
    r"(?i)\b(breach(ed)?|hacked|compromised)\b",
    r"(?i)\b(unauthori[sz]ed|suspicious|unknown)\b[^.?!]{0,20}\b(access|logins?|activity|ip)\b",
    r"(?i)\bblock(s|ed|ing)\b[^.?!]{0,60}\b(close|payroll|launch|release|go-live|month-end)\b",
]

INTERNAL_DETAIL_PATTERNS = [
    r"(?i)\bwhat.*(model|llm|ai.*model|gpt|prompt|system.prompt)\b",
    r"(?i)\bhow.*(you.*work|decision|algorithm|process.*ticket|classif|classify|classification)\b",
    r"(?i)\bwhat.*(database|db.*schema|postgres|table.*structure)\b",
    r"(?i)\bwhat.*(tool|function|internal|backend|server)\b",
    r"(?i)\b(show|tell|reveal|display).*(prompt|instruction|how.*trained)\b",
    r"(?i)\bwhat.*(kafka|queue|pipeline|worker|processing)\b",
    r"(?i)\bwhat.*(escalat|threshold|sentiment|trigger)\b",
    r"(?i)\bwho.*(developer|built|created|programmed)\b",
    r"(?i)\byou.*(just|only).*(copy|paste|template|script|bot)\b",
    r"(?i)\bare.*you.*(real|human|person|ai|robot|automated)\b",
]


def check_pricing_refund(message: str) -> str | None:
    """Check if message is about pricing or refunds."""
    for pattern in PRICING_REFUND_PATTERNS:
        if re.search(pattern, message):
            logger.info("Pricing/refund keyword matched", pattern=pattern)
            return "Pricing or refund question detected — must escalate to human"
    return None


def check_legal(message: str) -> str | None:
    """Check if message mentions legal topics."""
    for pattern in LEGAL_PATTERNS:
        if re.search(pattern, message):
            logger.info("Legal keyword matched", pattern=pattern)
            return "Legal/lawyer/sue keyword detected — must escalate to human"
    return None


def check_hostility(message: str) -> str | None:
    """Check for angry or abusive wording."""
    for pattern in HOSTILITY_PATTERNS:
        if re.search(pattern, message):
            logger.info("Hostile wording matched", pattern=pattern)
            return "Angry or abusive wording detected — must escalate to human"
    return None


def check_critical_incident(message: str) -> str | None:
    """Check for an outage, data loss or security incident."""
    for pattern in CRITICAL_INCIDENT_PATTERNS:
        if re.search(pattern, message):
            logger.info("Critical incident matched", pattern=pattern)
            return "Business-critical incident (outage, data loss or security) — must escalate"
    return None


def check_internal_details(message: str) -> str | None:
    """Check if customer is asking about internal system details."""
    for pattern in INTERNAL_DETAIL_PATTERNS:
        if re.search(pattern, message):
            logger.info("Internal detail query detected", pattern=pattern)
            return "Customer asked about internal system details — must deflect"
    return None


async def run_gate(
    openai_client,
    message: str,
    customer_name: str = "Customer",
) -> GateResult:
    """Run the pre-processing gate. Returns action, reason, and metadata."""
    logger.info("Running pre-processing gate", message_preview=message[:100])

    # 1. Check pricing/refund (highest priority — mandatory escalate)
    pricing_reason = check_pricing_refund(message)
    if pricing_reason:
        return GateResult(
            action=GateAction.ESCALATE,
            reason=pricing_reason,
            priority="high",
        )

    # 2. Check legal keywords (mandatory escalate)
    legal_reason = check_legal(message)
    if legal_reason:
        return GateResult(
            action=GateAction.ESCALATE,
            reason=legal_reason,
            priority="critical",
        )

    # 3. Check for internal details probing (deflect, don't answer)
    internal_reason = check_internal_details(message)
    if internal_reason:
        return GateResult(
            action=GateAction.DEFLECT,
            reason=internal_reason,
            priority="low",
        )

    # 4. Run sentiment analysis with emotion, urgency, and aspect detection
    sentiment = await analyze_sentiment_detailed(openai_client, message)

    hostility_reason = check_hostility(message)
    if hostility_reason:
        return GateResult(
            action=GateAction.ESCALATE,
            reason=hostility_reason,
            priority="high",
            sentiment_score=sentiment.sentiment_score,
            emotion="anger",
            urgency_score=sentiment.urgency_score,
            is_urgent=sentiment.is_urgent,
            aspect_scores=sentiment.aspect_scores,
        )

    incident_reason = check_critical_incident(message)
    if incident_reason:
        return GateResult(
            action=GateAction.ESCALATE,
            reason=incident_reason,
            priority="critical",
            sentiment_score=sentiment.sentiment_score,
            emotion=sentiment.emotion,
            urgency_score=sentiment.urgency_score,
            is_urgent=True,
            aspect_scores=sentiment.aspect_scores,
        )

    if sentiment.sentiment_score < 0.3:
        logger.info(
            "Sentiment below threshold, escalating",
            sentiment_score=sentiment.sentiment_score,
            emotion=sentiment.emotion,
            urgency=sentiment.urgency_score,
        )
        return GateResult(
            action=GateAction.ESCALATE,
            reason=f"Sentiment score {sentiment.sentiment_score:.2f} below 0.3 threshold — customer may be upset",
            priority="high",
            sentiment_score=sentiment.sentiment_score,
            emotion=sentiment.emotion,
            urgency_score=sentiment.urgency_score,
            is_urgent=sentiment.is_urgent,
            aspect_scores=sentiment.aspect_scores,
        )

    # Check for high urgency even if sentiment is OK
    if sentiment.is_urgent:
        logger.info(
            "High urgency detected, escalating",
            urgency_score=sentiment.urgency_score,
            emotion=sentiment.emotion,
        )
        return GateResult(
            action=GateAction.ESCALATE,
            reason=f"Urgency score {sentiment.urgency_score:.2f} above threshold — time-sensitive issue",
            priority="high",
            sentiment_score=sentiment.sentiment_score,
            emotion=sentiment.emotion,
            urgency_score=sentiment.urgency_score,
            is_urgent=True,
            aspect_scores=sentiment.aspect_scores,
        )

    # All gates passed — allow normal processing
    return GateResult(
        action=GateAction.ALLOW,
        reason="All gates passed",
        sentiment_score=sentiment.sentiment_score,
        emotion=sentiment.emotion,
        urgency_score=sentiment.urgency_score,
        is_urgent=sentiment.is_urgent,
        aspect_scores=sentiment.aspect_scores,
    )
