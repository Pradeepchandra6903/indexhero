"""R1 - Zero the inbox: every message gets exactly one disposition + reason."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
DECISIONS_PATH = ROOT / "decisions.json"


def run(ctx) -> dict:
    decisions = ctx.decisions
    undecided = [d for d in decisions if d.disposition not in {"reply", "archive", "defer", "delegate", "escalate"}]

    print(f"{'id':6} {'thread':16} {'category':20} {'disposition':10} reason")
    print("-" * 110)
    for d in decisions:
        print(f"{d.message_id:6} {d.thread_id:16} {d.category:20} {d.disposition:10} {d.reason[:70]}")
        ctx.trace.log(
            component="Router",
            message_id=d.message_id,
            reasoning_summary=d.reason,
            evidence_messages=d.cited,
            final_action=d.disposition,
            cap="R1",
        )
    print(f"\nundecided: {len(undecided)}")

    out = [
        {
            "message_id": d.message_id,
            "thread_id": d.thread_id,
            "category": d.category,
            "disposition": d.disposition,
            "reason": d.reason,
            "flags": d.flags,
        }
        for d in decisions
    ]
    with open(DECISIONS_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)

    return {"total": len(decisions), "undecided": len(undecided), "decisions_path": str(DECISIONS_PATH)}
