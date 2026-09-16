"""X2 - Long Thread Resolution (Tier B).

Collapses a long multi-participant thread into a structured resolution
summary (participants, milestones, open items) instead of making the user
re-read every message -- grounded with per-line message-id citations."""
from __future__ import annotations

from ..models import by_thread

MIN_THREAD_LEN = 4


def run(ctx) -> dict:
    threads = by_thread(ctx.messages)
    dec_by_id = {d.message_id: d for d in ctx.decisions}
    long_threads = {tid: msgs for tid, msgs in threads.items() if len(msgs) >= MIN_THREAD_LEN}

    results = {}
    for tid, msgs in long_threads.items():
        participants = sorted({m.from_ for m in msgs})
        milestones = [f"{m.id}: {m.body[:70].strip()}" for m in msgs]
        open_items = [
            f"{m.id} ({dec_by_id[m.id].disposition}): {dec_by_id[m.id].reason}"
            for m in msgs
            if m.id in dec_by_id and dec_by_id[m.id].disposition in ("reply", "escalate", "defer")
        ]
        results[tid] = {
            "message_count": len(msgs),
            "participants": participants,
            "milestones": milestones,
            "open_items": open_items,
            "cited": [m.id for m in msgs],
        }
        print(f"\n=== Thread {tid} ({len(msgs)} messages, {len(participants)} participants) ===")
        for line in milestones:
            print(f"  {line}")
        print("  -- open items --")
        for item in open_items or ["  (none -- fully resolved)"]:
            print(f"  {item}")

        ctx.trace.log(
            component="RetrievalEngine",
            message_id=None,
            reasoning_summary=f"Resolved long thread {tid}: {len(open_items)} open item(s) out of {len(msgs)} messages.",
            evidence_messages=[m.id for m in msgs],
            final_action="thread_resolution",
            cap="X2",
        )

    return {"threads_resolved": len(results), "detail": results}
