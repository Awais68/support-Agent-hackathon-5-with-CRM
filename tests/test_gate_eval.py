"""Pre-processing gate against the labeled eval set (tests/fixtures/sentiment_gate_eval.json).

Before the fix: precision 0.54, recall 0.75 (13 normal or mildly frustrated
messages escalated because VADER reads "error"/"failed"/"broken" as anger).
"""

import pytest

from agent.gate_eval import evaluate, load_eval_set
from agent.pre_processing_gate import GateAction, run_gate


@pytest.mark.asyncio
async def test_gate_eval_set_precision_and_recall():
    report = await evaluate(load_eval_set())
    missed = [r["id"] for r in report["rows"] if r["should_escalate"] and not r["escalated"]]
    assert report["recall"] == 1.0, f"missed escalations: {missed}"
    assert report["precision"] >= 0.9


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message",
    [
        "My API calls keep failing with 429. What is the rate limit on the starter plan?",
        "The connector failed with error 401 and the sync is broken.",
        "Export failed: invalid date format error on row 12.",
    ],
)
async def test_technical_failure_words_alone_do_not_escalate(message):
    result = await run_gate(None, message)
    assert result.action == GateAction.ALLOW, result.reason


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message",
    [
        "Your support is useless and I'm fed up.",
        "This is the worst product I have ever used.",
        "We will file a lawsuit if this happens again.",
        "Expect to hear from our attorney.",
        "I'm reporting TechFlow to the regulator.",
        "Our production environment is down since 9am.",
        "Someone hacked our admin account.",
    ],
)
async def test_anger_threats_and_incidents_still_escalate(message):
    result = await run_gate(None, message)
    assert result.action == GateAction.ESCALATE
