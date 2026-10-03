"""X4 - Autonomous Assistant Planning (Tier A).

Produces a prioritized, human-readable action plan for the day from the
Router's decisions + extracted commitments. Planning is informational -- it
never touches send/delete/forward itself -- so each row also states what R3
would do with it: whether R3's send policy would select the item (grounded
draft exists) or whether a human has to author the reply. The plan is a view,
not an actor."""
from __future__ import annotations

from ..actions import select_send_candidates
from ..commitments import extract_commitments
from ..drafting import DraftBuilder

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

    # Ask R3's own selection policy what it would pick, so the plan's
    # "next step" column reflects the real gate rather than a guess.
    builder = DraftBuilder(ctx.messages, ctx.decisions, ctx.memory)
    drafts = builder.build_all(d.message_id for d in ctx.decisions if d.disposition == "reply")
    gateable = {d.message_id for d, _ in select_send_candidates(ctx.decisions, drafts)}

    plan_items = []
    for d in ctx.decisions:
        if d.disposition not in ("escalate", "reply"):
            continue
        c = commitments.get(d.message_id)
        if d.message_id in gateable:
            next_step = "R3 proposes send (needs approval)"
        elif d.disposition == "escalate":
            next_step = "human decision required"
        else:
            next_step = "human must author the reply (nothing grounded to send)"
        plan_items.append(
            {
                "message_id": d.message_id,
                "disposition": d.disposition,
                "day": c.day_token if c else None,
                "time": c.time_token if c else None,
                "reason": d.reason,
                "requires_approval": d.message_id in gateable or d.disposition == "escalate",
                "next_step": next_step,
            }
        )

    plan_items.sort(
        key=lambda it: (PRIORITY_ORDER.index(it["disposition"]), _day_sort_key(it["day"]))
    )

    print("=== TODAY'S PLAN (a view over Router + commitments; X4 performs no action) ===")
    for i, item in enumerate(plan_items, 1):
        when = f"{item['day'] or '?'} {item['time'] or ''}".strip()
        print(f"{i:2}. [{item['disposition']:8}] {item['message_id']}  ({when})")
        print(f"      next step: {item['next_step']}")
        print(f"      why: {item['reason'][:88]}")

    print(f"\n{len(gateable)} of {len(plan_items)} plan items would be selected by R3's "
          f"send policy; the rest need a human to write the reply or make the call.")

    ctx.trace.log(
        component="PlannerAgent",
        message_id=None,
        reasoning_summary=(
            f"Built a {len(plan_items)}-item plan ordered by disposition priority then "
            f"commitment date; {len(gateable)} items match R3's send-selection policy."
        ),
        evidence_messages=[i["message_id"] for i in plan_items[:8]],
        final_action="plan",
        cap="X4",
        extra={"gateable": sorted(gateable)},
    )

    return {"plan": plan_items, "r3_would_propose": sorted(gateable)}
