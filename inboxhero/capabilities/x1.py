"""X1 - Smart Daily Digest (Tier A).

One-screen digest: what needs Sam today, what can wait, and what was
auto-archived (aggregated by count, not spammed individually)."""
from __future__ import annotations

from collections import Counter


def run(ctx) -> dict:
    needs_you = [d for d in ctx.decisions if d.disposition in ("reply", "escalate", "delegate")]
    can_wait = [d for d in ctx.decisions if d.disposition == "defer"]
    archived = [d for d in ctx.decisions if d.disposition == "archive"]

    print("=== NEEDS YOU TODAY ===")
    for d in sorted(needs_you, key=lambda d: (d.disposition != "escalate", d.message_id)):
        tag = "COMMITMENT" if d.commitment else ""
        print(f"  [{d.disposition:8}] {d.message_id}  {tag:10} {d.reason[:80]}")

    print("\n=== CAN WAIT ===")
    for d in can_wait:
        print(f"  {d.message_id}  {d.reason[:80]}")

    print("\n=== AUTO-ARCHIVED (by category, not individually) ===")
    counts = Counter(d.category for d in archived)
    for cat, n in counts.most_common():
        print(f"  {cat}: {n}")

    ctx.trace.log(
        component="DigestGenerator",
        message_id=None,
        reasoning_summary=(
            f"Digest: {len(needs_you)} need Sam, {len(can_wait)} can wait, "
            f"{len(archived)} auto-archived across {len(counts)} categories."
        ),
        evidence_messages=[d.message_id for d in needs_you[:5]],
        final_action="digest",
        cap="X1",
    )

    return {
        "needs_you": [d.message_id for d in needs_you],
        "can_wait": [d.message_id for d in can_wait],
        "archived_by_category": dict(counts),
    }
