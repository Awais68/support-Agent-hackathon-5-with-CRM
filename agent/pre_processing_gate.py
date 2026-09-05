"""Pre-processing gate for hard business rule enforcement.

Must run BEFORE any agent processing. Checks:
1. Pricing/refund questions → escalate
2. "lawyer", "legal", "sue" (or clear paraphrases) → escalate
3. Sentiment score < 0.3 → escalate
4. Customer asking about internal system details → deflect
5. Ticket must exist before response
"""

import re
from typing import Optional, Dict
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
        sentiment_score: Optional[float] = None,
        emotion: str = "neutral",
        urgency_score: float = 0.0,
        is_urgent: bool = False,
        aspect_scores: Optional[Dict[str, float]] = None,
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
    r"(?i)\blawsu\b",
    r"(?i)\battorney\b",
    r"(?i)\blegal.action\b",
    r"(?i)\bcourt\b",
    r"(?i)\blitigat\b",
    r"(?i)\blegal.*(notice|threat|demand)\b",
    r"(?i)\b(cease|desist)\b",
    r"(?i)\b(damages|compensat)\b.*\b(legal|sue)\b",
    r"(?i)\breport.*(you|techflow)\b.*\b(authorit|regulat|law)\b",
    r"(?i)\b(class.action)\b",
    r"(?i)\blegal.*(right|obligation|claim)\b",
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


def check_pricing_refund(message: str) -> Optional[str]:
    """Check if message is about pricing or refunds."""
    for pattern in PRICING_REFUND_PATTERNS:
        if re.search(pattern, message):
            logger.info("Pricing/refund keyword matched", pattern=pattern)
            return "Pricing or refund question detected — must escalate to human"
    return None


def check_legal(message: str) -> Optional[str]:
    """Check if message mentions legal topics."""
    for pattern in LEGAL_PATTERNS:
        if re.search(pattern, message):
            logger.info("Legal keyword matched", pattern=pattern)
            return "Legal/lawyer/sue keyword detected — must escalate to human"
    return None


def check_internal_details(message: str) -> Optional[str]:
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

    # 5. Check for high urgency even if sentiment is OK
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
