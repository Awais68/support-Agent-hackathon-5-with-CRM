"""Sentiment analyzer using VADER for customer message scoring.

VADER (Valence Aware Dictionary and sEntiment Reasoner) is a lexicon and
rule-based sentiment analysis tool specifically attuned to sentiments expressed
in social media / support contexts. It requires no API calls, no model hosting,
and has zero marginal cost — ideal for the <$1K/year budget constraint.

Enhancements beyond VADER:
- Emotion classification (anger, frustration, confusion, satisfaction, etc.)
- Urgency scoring via keyword/regex patterns
- Aspect-based sentiment (pricing, features, support, response_time, etc.)
- Sentiment trend / drop detection across conversation turns
"""

import re
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Sequence
import structlog
from exceptions import sanitize_error_message

logger = structlog.get_logger(__name__)

try:
    from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
    _analyzer = SentimentIntensityAnalyzer()
except ImportError:
    logger.warning("vaderSentiment not installed — sentiment defaults to 0.5")
    _analyzer = None


@dataclass
class SentimentDetail:
    sentiment_score: float = 0.5
    emotion: str = "neutral"
    emotion_scores: Dict[str, float] = field(default_factory=lambda: {
        "anger": 0.0, "frustration": 0.0, "confusion": 0.0,
        "satisfaction": 0.0, "gratitude": 0.0, "neutral": 1.0, "mixed": 0.0,
    })
    urgency_score: float = 0.0
    is_urgent: bool = False
    compound: float = 0.0
    detail: Optional[Dict[str, float]] = None
    aspect_scores: Dict[str, float] = field(default_factory=dict)
    aspect_relevance: Dict[str, float] = field(default_factory=dict)


# --- Emotion keyword lexicons ---

_ANGER_KEYWORDS = [
    r"(?i)\b(angry|furious|outraged|pissed|infuriat|enraged|livid)\b",
    r"(?i)\b(terrible|horrible|awful|disgusting|unacceptab|intolerab)\b",
    r"(?i)\b(worst|never.*again|ridiculous|absurd|outrageous)\b",
    r"(?i)\b(shut up|stop ignoring|listen to me|how dare)\b",
    r"(?i)\b(incompetent|useless|worthless|pathetic)\b",
]

_FRUSTRATION_KEYWORDS = [
    r"(?i)\b(frustrat|annoying|irritat|exasperat|aggravat)\b",
    r"(?i)\b(sick of|tired of|fed up|had enough|cannot believe)\b",
    r"(?i)\b(keeps failing|keeps breaking|not working|still broken)\b",
    r"(?i)\b(why can'?t you|how many times|already told you|again)\b",
    r"(?i)\b(waste.*time|wasting.*time|hours.*trying|days.*waiting)\b",
]

_CONFUSION_KEYWORDS = [
    r"(?i)\b(confused|don'?t understand|not clear|unclear|perplex)\b",
    r"(?i)\b(wait what|how does|what does|what is|how is)\b",
    r"(?i)\b(i'm lost|im lost|no idea|not sure|maybe\?)\b",
    r"(?i)\b(can you explain|clarify|what do you mean|need more info)\b",
    r"(?i)\b(doesn'?t make sense|don'?t get it|mislead|ambigu)\b",
]

_SATISFACTION_KEYWORDS = [
    r"(?i)\b(great|excellent|amazing|fantastic|wonderful|perfect)\b",
    r"(?i)\b(happy|pleased|delighted|impressed|satisfied)\b",
    r"(?i)\b(love it|works great|so good|very helpful|exactly what I needed)\b",
    r"(?i)\b(best|awesome|outstanding|superb|brilliant)\b",
]

_GRATITUDE_KEYWORDS = [
    r"(?i)\b(thank|thanks|appreciate|grateful|thankful)\b",
    r"(?i)\b(you're the best|you rock|much obliged|very helpful)\b",
]

_URGENCY_PATTERNS = [
    r"(?i)\b(urgent|asap|asap|immediately|right away|emergency)\b",
    r"(?i)\b(critical|blocker|blocking|stopped|down|crashed|broken)\b",
    r"(?i)\b(deadline|overdue|past due|due date|timely|time.sensitive)\b",
    r"(?i)\b(can'?t work|can'?t access|lost.*data|data.*loss|security)\b",
    r"(?i)\b(production|prodcution|prod.*down|site.*down|system.*down)\b",
    r"(?i)\b(help.*now|need.*now|fix.*now|must.*fix|resolve.*immediately)\b",
    r"(?i)\b(impact.*(business|revenue|customer|sla))\b",
    r"(?i)\b(fire|panic|disaster|catastroph|meltdown)\b",
    r"(?i)!!!\s*$",
]

# --- Aspect-based sentiment lexicons ---

_ASPECT_PATTERNS: Dict[str, List[str]] = {
    "pricing": [
        r"(?i)\b(price|pricing|cost|subscription|fee|plan cost|how much)\b",
        r"(?i)\b(refund|money.back|reimburs|charge|cancel.*subscription)\b",
        r"(?i)\b(bill|billing|invoice|overcharge|double.charge|charged)\b",
        r"(?i)\b(discount|coupon|promo|free.trial|upgrade.*cost)\b",
        r"(?i)\b(too expensive|overpriced|costly|afford|cheap)\b",
    ],
    "features": [
        r"(?i)\b(feature|functionality|capability|ability to|option)\b",
        r"(?i)\b(missing.*feature|need.*feature|wish.*had|would be nice)\b",
        r"(?i)\b(dashboard|report|analytics|chart|graph|visualization)\b",
        r"(?i)\b(connector|integration|api|sdk|webhook|plugin)\b",
        r"(?i)\b(setup|configur|install|deploy|implement)\b",
    ],
    "support": [
        r"(?i)\b(support|help|assistance|service|customer.service)\b",
        r"(?i)\b(agent|representative|human|person|someone)\b",
        r"(?i)\b(response.*time|wait.*time|slow.*support|no.*response)\b",
        r"(?i)\b(ignoring|unresponsive|not.helping|useless.*support)\b",
        r"(?i)\b(ticket|escalat|follow.up|status.*update)\b",
    ],
    "response_time": [
        r"(?i)\b(how long|response.*time|waiting|wait.*for|still waiting)\b",
        r"(?i)\b(slow|delayed|delay|late|overdue|past due)\b",
        r"(?i)\b(hours.*ago|days.*ago|weeks.*ago|since.*(yesterday|monday))\b",
        r"(?i)\b(still.*no.*response|no.*reply|no.*update|any.*update)\b",
        r"(?i)\b(take.*(long|forever|ages)|too slow)\b",
    ],
    "onboarding": [
        r"(?i)\b(getting started|onboarding|setup|first.*time|new.*user)\b",
        r"(?i)\b(welcome|sign.up|register|create.*account|getting.*started)\b",
        r"(?i)\b(tutorial|guide|walkthrough|documentation|docs|manual)\b",
        r"(?i)\b(how.*(use|start|begin)|where.*(begin|start))\b",
    ],
    "reliability": [
        r"(?i)\b(down|crashed|outage|offline|unavailable|error)\b",
        r"(?i)\b(bug|glitch|broken|not working|failing|failed)\b",
        r"(?i)\b(data.*loss|data.*integrity|corrupt|missing.*data)\b",
        r"(?i)\b(performance|slow|lag|timeout|latency|loading)\b",
        r"(?i)\b(issue|problem|error.*message|exception|fail)\b",
    ],
    "account": [
        r"(?i)\b(login|log.in|sign.in|password|credential|access)\b",
        r"(?i)\b(account|profile|setting|preference|team.*member)\b",
        r"(?i)\b(permission|role|user.*management|invite)\b",
        r"(?i)\b(lock.*out|cannot.*login|forgot.*password|reset)\b",
    ],
    "security": [
        r"(?i)\b(security|breach|vulnerab|exploit|hack|unauthoriz)\b",
        r"(?i)\b(compliance|audit|sso|mfa|2fa|authentication)\b",
        r"(?i)\b(data.*privacy|gdpr|hipaa|encryption|secure)\b",
        r"(?i)\b(suspicious|unusual.*(activity|login|access))\b",
    ],
}


def _keyword_score(text: str, patterns: List[str]) -> float:
    score = 0.0
    for p in patterns:
        matches = re.findall(p, text)
        if matches:
            score += len(matches) * 0.2
    return min(1.0, score)


def _classify_emotion(
    compound: float, neg: float, pos: float, neu: float, text: str
) -> Dict[str, float]:
    scores = {
        "anger": 0.0, "frustration": 0.0, "confusion": 0.0,
        "satisfaction": 0.0, "gratitude": 0.0, "neutral": 0.0, "mixed": 0.0,
    }

    anger_kw = _keyword_score(text, _ANGER_KEYWORDS)
    frustration_kw = _keyword_score(text, _FRUSTRATION_KEYWORDS)
    confusion_kw = _keyword_score(text, _CONFUSION_KEYWORDS)
    satisfaction_kw = _keyword_score(text, _SATISFACTION_KEYWORDS)
    gratitude_kw = _keyword_score(text, _GRATITUDE_KEYWORDS)

    sentiment_bias = compound
    question_count = text.count("?")
    exclamation_count = text.count("!")

    anger_score = anger_kw * 0.8 + max(0, -sentiment_bias) * 0.2
    anger_score += 0.2 if (neg > 0.4 and compound < -0.3) else 0.0
    scores["anger"] = min(1.0, anger_score)

    frustration_score = frustration_kw * 0.7 + max(0, -sentiment_bias) * 0.2
    frustration_score += 0.1 if neg > 0.2 else 0.0
    frustration_score += min(0.3, exclamation_count * 0.05)
    scores["frustration"] = min(1.0, frustration_score)

    confusion_score = confusion_kw * 0.6
    confusion_score += min(0.4, question_count * 0.1)
    confusion_score += 0.15 if (abs(compound) < 0.2 and neu > 0.8) else 0.0
    scores["confusion"] = min(1.0, confusion_score)

    satisfaction_score = satisfaction_kw * 0.7 + max(0, sentiment_bias) * 0.3
    scores["satisfaction"] = min(1.0, satisfaction_score)

    gratitude_score = gratitude_kw * 0.8 + max(0, sentiment_bias) * 0.1
    scores["gratitude"] = min(1.0, gratitude_score)

    neutral_score = max(0, 1.0 - (
        scores["anger"] + scores["frustration"] + scores["confusion"] +
        scores["satisfaction"] + scores["gratitude"]
    ))
    neutral_score = neutral_score * 0.7 + (0.3 if abs(compound) < 0.1 and neu > 0.85 else 0.0)
    scores["neutral"] = min(1.0, neutral_score)

    top_emotions = sorted(scores.items(), key=lambda x: -x[1])
    if len(top_emotions) >= 2 and top_emotions[0][1] < 0.35 and top_emotions[1][1] < 0.35:
        scores["mixed"] = min(1.0, top_emotions[0][1] + top_emotions[1][1])

    return scores


def _pick_primary_emotion(emotion_scores: Dict[str, float]) -> str:
    return max(emotion_scores, key=emotion_scores.get)


def _compute_urgency(text: str, compound: float) -> float:
    kw_score = _keyword_score(text, _URGENCY_PATTERNS)
    exclamation_count = text.count("!")
    caps_ratio = sum(1 for c in text if c.isupper()) / max(1, len(text))
    caps_bonus = min(0.25, caps_ratio * 0.5)
    exclamation_bonus = min(0.2, exclamation_count * 0.05)
    short_len = max(0, 0.1 * (1.0 - min(1.0, len(text) / 200)))
    negativity_bonus = max(0, -compound * 0.1)
    score = kw_score + caps_bonus + exclamation_bonus + short_len + negativity_bonus
    return min(1.0, score)


# --- Aspect-based sentiment ---

def _analyze_aspects(text: str, compound: float) -> tuple[Dict[str, float], Dict[str, float]]:
    """Score sentiment per aspect and return (aspect_scores, aspect_relevance)."""
    aspect_scores: Dict[str, float] = {}
    aspect_relevance: Dict[str, float] = {}

    for aspect, patterns in _ASPECT_PATTERNS.items():
        kw_score = _keyword_score(text, patterns)
        if kw_score > 0.0:
            aspect_relevance[aspect] = kw_score
            base_sentiment = max(0.0, min(1.0, (compound + 1.0) / 2.0))
            # Adjust if keywords themselves are negative/positive
            neg_kw = sum(1 for p in patterns if re.search(p, text) and any(
                w in text.lower() for w in ["not", "no", "never", "can't", "won't", "broken", "bad", "wrong", "issue", "problem", "fail", "slow", "expensive", "missing", "lack"]
            ))
            if neg_kw:
                base_sentiment = max(0.0, base_sentiment - 0.2 * neg_kw)
            aspect_scores[aspect] = max(0.0, min(1.0, base_sentiment))
        else:
            aspect_scores[aspect] = 0.5
            aspect_relevance[aspect] = 0.0

    return aspect_scores, aspect_relevance


# --- Sentiment trend detection ---

def detect_sentiment_drop(
    history: Sequence[Dict],
    lookback: int = 3,
    drop_threshold: float = 0.15,
) -> tuple[bool, float, str]:
    """Detect if sentiment is dropping across recent conversation turns.

    Args:
        history: List of dicts with 'sentiment_score' keys, ordered newest-first.
        lookback: Number of recent turns to examine.
        drop_threshold: Minimum cumulative drop to trigger detection.

    Returns:
        (is_dropping, total_drop, description) tuple.
    """
    recent = [h for h in history[:lookback] if h.get("sentiment_score") is not None]
    if len(recent) < 2:
        return False, 0.0, "Not enough history for trend detection"

    scores = [r["sentiment_score"] for r in recent]

    # history is newest-first: scores[0] = most recent, scores[-1] = oldest
    # If sentiment is dropping, scores[0] (newest) < scores[-1] (oldest)
    first_score = scores[0]      # most recent turn
    last_score = scores[-1]      # oldest turn in the window
    total_drop = last_score - first_score   # positive = dropping

    if total_drop >= drop_threshold:
        return (
            True,
            total_drop,
            f"Sentiment dropped {total_drop:.2f} over last {len(scores)} messages "
            f"(from {last_score:.2f} to {first_score:.2f})",
        )

    # A steady decline over 3+ turns matters even when the total drop is below
    # the single-step threshold, so it gets a relaxed (half) threshold. Using the
    # full threshold here would be dead code: that case already returned above.
    if len(scores) >= 3:
        monotonic_drop = all(scores[i] < scores[i + 1] for i in range(len(scores) - 1))
        if monotonic_drop and total_drop >= drop_threshold / 2:
            return (
                True,
                total_drop,
                f"Monotonic sentiment drop of {total_drop:.2f} over {len(scores)} turns",
            )

    return False, max(0.0, total_drop), "No significant drop detected"


# --- Public API ---

async def analyze_sentiment(
    openai_client: Optional[object] = None,
    message: str = "",
) -> float:
    """Analyze sentiment of a customer message. Returns 0.0–1.0 (backward compatible).

    Uses VADER's compound score normalised from [-1, 1] → [0, 1].
    Falls back to 0.5 if the library is unavailable or parsing fails.

    The ``openai_client`` parameter is accepted for backward compatibility
    with callers that previously used an OpenAI-based scorer; it is ignored.
    """
    try:
        if _analyzer is None:
            return 0.5
        vs = _analyzer.polarity_scores(message)
        compound = vs.get("compound", 0.0)
        score = max(0.0, min(1.0, (compound + 1.0) / 2.0))
        return score
    except Exception as e:
        logger.error("Sentiment analysis failed", error=sanitize_error_message(str(e)), message_preview=message[:80])
        return 0.5


async def analyze_sentiment_detailed(
    openai_client: Optional[object] = None,
    message: str = "",
) -> "SentimentDetail":
    """Analyze sentiment with emotion classification, urgency, and aspect scoring.

    Returns a SentimentDetail dataclass containing:
    - sentiment_score, emotion, emotion_scores
    - urgency_score, is_urgent
    - aspect_scores (per-aspect sentiment) and aspect_relevance
    """
    try:
        if _analyzer is None:
            return SentimentDetail()

        vs = _analyzer.polarity_scores(message)
        compound = vs.get("compound", 0.0)
        neg = vs.get("neg", 0.0)
        neu = vs.get("neu", 1.0)
        pos = vs.get("pos", 0.0)

        sentiment_score = max(0.0, min(1.0, (compound + 1.0) / 2.0))
        emotion_scores = _classify_emotion(compound, neg, pos, neu, message)
        primary_emotion = _pick_primary_emotion(emotion_scores)
        urgency_score = _compute_urgency(message, compound)
        aspect_scores, aspect_relevance = _analyze_aspects(message, compound)

        logger.info(
            "Sentiment detail analyzed",
            sentiment_score=sentiment_score,
            emotion=primary_emotion,
            urgency=urgency_score,
            aspects=[a for a, r in aspect_relevance.items() if r > 0],
            message_preview=message[:80],
        )

        return SentimentDetail(
            sentiment_score=sentiment_score,
            emotion=primary_emotion,
            emotion_scores=emotion_scores,
            urgency_score=urgency_score,
            is_urgent=urgency_score >= 0.5,
            compound=compound,
            detail=vs,
            aspect_scores=aspect_scores,
            aspect_relevance=aspect_relevance,
        )

    except Exception as e:
        logger.error("Detailed sentiment analysis failed", error=sanitize_error_message(str(e)), message_preview=message[:80])
        return SentimentDetail()
