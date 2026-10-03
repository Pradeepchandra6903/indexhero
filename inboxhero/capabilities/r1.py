"""R1 - Zero the inbox: every message gets exactly one disposition + reason.

Part 2 also asks how many messages were handled by rules and never needed a
model call, so this capability computes that from each decision's path and
reports it alongside the undecided count.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from ..router import DISPOSITIONS, MODEL_FREE_PATHS, PATH_RULES_OVERRIDE

ROOT = Path(__file__).resolve().parent.parent.parent
DECISIONS_PATH = ROOT / "decisions.json"


def run(ctx) -> dict:
    decisions = ctx.decisions
    undecided = [d for d in decisions if d.disposition not in DISPOSITIONS]

    print(f"{'id':6} {'thread':16} {'category':20} {'disposition':10} {'path':22} reason")
    print("-" * 130)
    for d in decisions:
        print(f"{d.message_id:6} {d.thread_id:16} {d.category:20} {d.disposition:10} {d.path:22} {d.reason[:55]}")
        ctx.trace.log(
            component="Router",
            message_id=d.message_id,
            reasoning_summary=d.reason,
            evidence_messages=d.cited,
            final_action=d.disposition,
            cap="R1",
            extra={"path": d.path, "rule_handled": d.rule_handled},
        )

    by_path = Counter(d.path for d in decisions)
    rule_handled = sum(1 for d in decisions if d.rule_handled)
    model_free = sum(1 for d in decisions if d.path in MODEL_FREE_PATHS)

    print(f"\nmessages processed: {len(decisions)}")
    print(f"undecided: {len(undecided)}")
    print(f"rule_handled (dispatched by RuleEngine, no model call): {rule_handled}")
    print(f"  of which given a curated nuance instead of blind archive: {by_path.get(PATH_RULES_OVERRIDE, 0)}")
    print(f"resolved without any model call (rules + security patterns): {model_free}")
    print("by path: " + ", ".join(f"{path}={count}" for path, count in sorted(by_path.items())))
    print("by disposition: " + ", ".join(
        f"{disposition}={count}" for disposition, count in sorted(Counter(d.disposition for d in decisions).items())
    ))

    out = [
        {
            "message_id": d.message_id,
            "thread_id": d.thread_id,
            "category": d.category,
            "disposition": d.disposition,
            "reason": d.reason,
            "flags": d.flags,
            "path": d.path,
            "rule_handled": d.rule_handled,
        }
        for d in decisions
    ]
    with open(DECISIONS_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)

    ctx.trace.log(
        component="Router",
        message_id=None,
        reasoning_summary=(
            f"{len(decisions)} messages dispositioned, {len(undecided)} undecided, "
            f"{rule_handled} handled by rules with no model call."
        ),
        evidence_messages=[],
        final_action="summary",
        cap="R1",
        extra={"rule_handled": rule_handled, "by_path": dict(by_path)},
    )

    return {
        "total": len(decisions),
        "undecided": len(undecided),
        "rule_handled": rule_handled,
        "model_free": model_free,
        "by_path": dict(by_path),
        "decisions_path": str(DECISIONS_PATH),
    }
