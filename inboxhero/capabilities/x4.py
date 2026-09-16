"""X4 - Autonomous Assistant Planning (Tier C).

Produces a prioritized, human-readable action plan for the day from the
Router's decisions + extracted commitments. Purely informational: planning
never touches send/delete/forward, so it needs no approval gate itself --
any action it recommends still has to pass through R3 before it can happen."""
from __future__ import annotations

from ..commitments import extract_commitments

PRIORITY_ORDER = ["escalate", "reply", "defer", "delegate", "archive"]


def _day_sort_key(day_token: str | None) -> tuple:
    if not day_token:
        return (1, 99)
    weekday_rank = {
        "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
        "friday": 4, "saturday": 5, "sunday": 6,
    }
    if day_token in weekday_rank:
        return (0, weekday_rank[day_token])
    digits = "".join(ch for ch in day_token if ch.isdigit())
    return (0, int(digits)) if digits else (1, 99)


def run(ctx) -> dict:
    commitments = {c.message_id: c for c in extract_commitments(ctx.messages)}
    dec_by_id = {d.message_id: d for d in ctx.decisions}

    plan_items = []
    for d in ctx.decisions:
        if d.disposition not in ("escalate", "reply"):
            continue
        c = commitments.get(d.message_id)
        plan_items.append(
            {
                "message_id": d.message_id,
                "disposition": d.disposition,
                "day": c.day_token if c else None,
                "time": c.time_token if c else None,
                "reason": d.reason,
                "requires_approval": d.disposition == "escalate",
            }
        )

    plan_items.sort(
        key=lambda it: (PRIORITY_ORDER.index(it["disposition"]), _day_sort_key(it["day"]))
    )

    print("=== TODAY'S PLAN (informational only -- every send still gated by R3) ===")
    for i, item in enumerate(plan_items, 1):
        when = f"{item['day'] or '?'} {item['time'] or ''}".strip()
        gate = " [needs approval]" if item["requires_approval"] else ""
        print(f"{i:2}. [{item['disposition']:8}] {item['message_id']}  ({when}){gate} -- {item['reason'][:70]}")

    ctx.trace.log(
        component="PlannerAgent",
        message_id=None,
        reasoning_summary=f"Built a {len(plan_items)}-item plan ordered by disposition priority then commitment date.",
        evidence_messages=[i["message_id"] for i in plan_items[:8]],
        final_action="plan",
        cap="X4",
    )

    return {"plan": plan_items}
