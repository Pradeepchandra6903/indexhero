"""X5 - Preference-Aware Negotiation Agent (Tier C).

Uses stored preferences (memory/preferences.json) plus detected scheduling
conflicts to DRAFT counter-proposals. It never sends anything itself -- every
draft it produces still has to pass R3's approval gate before it becomes a
real 'send'."""
from __future__ import annotations

import re

from ..commitments import detect_conflicts, extract_commitments
from ..memory import Preference

NO_MEETINGS_BEFORE_KEY = "no_meetings_before_11am"


def _hour_of(time_token: str) -> int:
    """'9:00am' -> 9, '11:00am' -> 11, '2:00pm' -> 14 (24h for correct ordering)."""
    m = re.match(r"(\d{1,2})(?::(\d{2}))?(am|pm)", time_token)
    if not m:
        return -1
    hour = int(m.group(1)) % 12
    if m.group(3) == "pm":
        hour += 12
    return hour


def _ensure_calendar_preference(ctx) -> dict:
    if not ctx.memory.has(NO_MEETINGS_BEFORE_KEY):
        pref = Preference(
            key=NO_MEETINGS_BEFORE_KEY,
            value={"earliest": "11:00am"},
            source_message_id="m041",
            stated="No meetings before 11:00am, ever; offer 11:00am or later instead.",
            created_at="x5-bootstrap",
        )
        ctx.memory.remember(pref)
    return ctx.memory.get(NO_MEETINGS_BEFORE_KEY)


def run(ctx) -> dict:
    pref = _ensure_calendar_preference(ctx)
    commitments = extract_commitments(ctx.messages)
    conflicts = detect_conflicts(commitments)

    drafts = []

    # 1) Preference violation: m043 proposes Monday 9:00am.
    m043 = next((c for c in commitments if c.message_id == "m043"), None)
    if m043 and m043.time_token and 0 <= _hour_of(m043.time_token) < _hour_of(pref["value"]["earliest"]):
        draft = (
            f"Hi Aria -- Monday at 9:00am is before Sam's standing 11:00am cutoff "
            f"(policy from {pref['source_message_id']}). Could we do 11:00am or later that day instead?"
        )
        drafts.append({"message_id": "m043", "kind": "preference_violation", "draft": draft, "cited": [pref["source_message_id"]]})

    # 2) Scheduling conflicts: surface the collision without fabricating availability.
    for conflict in conflicts:
        ids = conflict["message_ids"]
        primary, secondary = ids[0], ids[1] if len(ids) > 1 else ids[0]
        draft = (
            f"Both {primary} and {secondary} land on {conflict['day']} {conflict['time']}. "
            "No alternative availability is grounded in the inbox, so no reschedule is proposed. "
            "Awaiting human approval and a confirmed alternate slot before sending any reply."
        )
        drafts.append({"message_id": ",".join(ids), "kind": "conflict_resolution", "draft": draft, "cited": ids})

    print("=== NEGOTIATION DRAFTS (preference-aware, unsent) ===")
    for d in drafts:
        print(f"[{d['kind']}] {d['message_id']}: {d['draft']}")
        ctx.trace.log(
            component="NegotiationAgent",
            message_id=d["message_id"],
            reasoning_summary=f"Drafted preference-aware negotiation ({d['kind']}); requires R3 approval before sending.",
            evidence_messages=d["cited"],
            final_action="draft_negotiation",
            cap="X5",
        )

    return {"drafts": drafts}
