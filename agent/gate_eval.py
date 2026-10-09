"""Measure the pre-processing gate against the labeled eval set.

Usage: uv run python -m agent.gate_eval [-v]

Prints precision/recall of ESCALATE against tests/fixtures/sentiment_gate_eval.json.
No network calls: the gate's sentiment step is local (VADER + regex).
"""

import asyncio
import json
import sys
from pathlib import Path

from agent.pre_processing_gate import GateAction, run_gate

FIXTURE = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "sentiment_gate_eval.json"


def load_eval_set() -> list[dict]:
    return json.loads(FIXTURE.read_text())["messages"]


async def evaluate(messages: list[dict]) -> dict:
    rows = []
    for m in messages:
        result = await run_gate(None, m["text"])
        rows.append(
            {**m, "escalated": result.action == GateAction.ESCALATE, "reason": result.reason}
        )
    tp = sum(r["escalated"] and r["should_escalate"] for r in rows)
    fp = sum(r["escalated"] and not r["should_escalate"] for r in rows)
    fn = sum(not r["escalated"] and r["should_escalate"] for r in rows)
    return {
        "rows": rows,
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": len(rows) - tp - fp - fn,
        "precision": tp / (tp + fp) if tp + fp else 1.0,
        "recall": tp / (tp + fn) if tp + fn else 1.0,
    }


def main() -> None:
    report = asyncio.run(evaluate(load_eval_set()))
    verbose = "-v" in sys.argv
    for r in report["rows"]:
        wrong = r["escalated"] != r["should_escalate"]
        if verbose or wrong:
            mark = "WRONG" if wrong else "ok   "
            print(f"{mark} {r['id']:<24} esc={r['escalated']!s:<5} {r['reason'][:70]}")
    print(
        f"TP={report['tp']} FP={report['fp']} FN={report['fn']} TN={report['tn']}  "
        f"precision={report['precision']:.2f} recall={report['recall']:.2f}"
    )


if __name__ == "__main__":
    main()
