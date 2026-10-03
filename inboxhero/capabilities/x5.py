"""X5 - Preference-Aware Negotiation Agent (Tier C).

Finds *every* message that proposes a slot earlier than the standing cutoff --
the cutoff and the hour come from the learned preference and from commitments
parsed out of the inbox, so no message id or time is written into this module
-- then drafts a counter-proposal that quotes the rule it is enforcing.

It never sends anything itself: each draft is handed to R3's ApprovalGate in
dry-run mode, so the gate decides, and nothing reaches outbox/ from here.
"""
from __future__ import annotations

from ..actions import ApprovalGate
from ..commitments import detect_conflicts, extract_commitments
from ..drafting import DraftBuilder
from ..memory import EARLIEST_MEETING_KEY, PreferenceLearner


def _ensure_calendar_preference(ctx):
    """Use the stored cutoff; if memory is empty, learn it from the inbox."""
    if not ctx.memory.has(EARLIEST_MEETING_KEY):
        for preference in PreferenceLearner(ctx.messages).learn():
            ctx.memory.remember(preference)
    return ctx.memory.get(EARLIEST_MEETING_KEY)


def run(ctx) -> dict:
    preference = _ensure_calendar_preference(ctx)
    builder = DraftBuilder(ctx.messages, ctx.decisions, ctx.memory)
    gate = ApprovalGate(dry_run=True, auto_deny=getattr(ctx.args, "auto_deny", False))

    if not preference:
        print("no scheduling preference is stored or derivable from the inbox; "
              "nothing to negotiate")
        return {"drafts": [], "preference": None}

    earliest = (preference.get("value") or {}).get("earliest", "")
    print(f"standing rule in effect: no meetings before {earliest} "
          f"(learned from {preference.get('source_message_id')}: "
          f"\"{preference.get('stated', '')}\")\n")

    drafts = []

    violations = builder.preference_violations()
    negotiable = [v for v in violations if v["negotiable"]]
    advisory = [v for v in violations if not v["negotiable"]]

    print(f"=== PREFERENCE VIOLATIONS ({len(violations)} found by scanning every "
          f"parsed commitment against the stored cutoff) ===")
    for violation in negotiable:
        draft = builder.build_preference_violation(violation["message_id"])
        if draft.refused:
            print(f"[skipped] {violation['message_id']}: {draft.refusal_reason}")
            continue
        print(f"[preference_violation] {draft.message_id} proposes "
              f"{violation['proposed']} (< {earliest}) -> counter-proposal to {draft.to}")
        print(f"  {draft.body}")
        drafts.append(
            {
                "message_id": draft.message_id,
                "kind": draft.kind,
                "draft": draft.body,
                "cited": draft.cited,
                "proposed": violation["proposed"],
                "payload": draft.as_payload(),
            }
        )

    for violation in advisory:
        print(f"[advisory, no draft] {violation['message_id']} has "
              f"{violation['proposed']} (< {earliest}) but is {violation['category']}/"
              f"{violation['disposition']} -- nobody is waiting on an answer, so "
              f"counter-proposing would be unsolicited mail")

    # Scheduling collisions: surface them without fabricating availability.
    commitments = extract_commitments(ctx.messages)
    print("\n=== SCHEDULING CONFLICTS ===")
    for conflict in detect_conflicts(commitments):
        ids = conflict["message_ids"]
        body = (
            f"Both {ids[0]} and {ids[1] if len(ids) > 1 else ids[0]} land on "
            f"{conflict['day']} {conflict['time']}. No alternative availability is "
            "grounded in the inbox, so no reschedule is proposed. Awaiting human "
            "approval and a confirmed alternate slot before sending any reply."
        )
        print(f"[conflict_resolution] {','.join(ids)}: {body}")
        drafts.append(
            {
                "message_id": ",".join(ids),
                "kind": "conflict_resolution",
                "draft": body,
                "cited": ids,
                "payload": {"message_id": ",".join(ids), "to": "", "body": body, "cited": ids},
            }
        )

    # Hand every draft to the gate rather than acting on it.
    print("\n=== HANDED TO R3's APPROVAL GATE (dry-run; nothing is sent) ===")
    gated = []
    for item in drafts:
        pending = gate.require_approval(
            action="send",
            message_id=item["message_id"],
            reason=f"X5 {item['kind']} counter-proposal, cited {item['cited']}",
            payload=item["payload"],
        )
        gated.append({"message_id": item["message_id"], "status": pending.final_status})
        print(f"  {item['message_id']} [{item['kind']}] -> gate says: {pending.final_status}")
        ctx.trace.log(
            component="NegotiationAgent",
            message_id=item["message_id"],
            reasoning_summary=(
                f"Drafted preference-aware negotiation ({item['kind']}); "
                f"routed through ApprovalGate, which returned {pending.final_status}."
            ),
            evidence_messages=item["cited"],
            final_action=f"draft_negotiation:{pending.final_status}",
            cap="X5",
            extra={"gate": "dry_run", "draft": item["draft"]},
        )

    print(f"\noutbox/ writes from X5: {gate.writes_by_this_gate()} (dry-run by design)")

    return {
        "preference": {"earliest": earliest, "source": preference.get("source_message_id")},
        "violations": [v["message_id"] for v in violations],
        "negotiated": [v["message_id"] for v in negotiable],
        "advisory_only": [v["message_id"] for v in advisory],
        "drafts": [{k: v for k, v in d.items() if k != "payload"} for d in drafts],
        "gated": gated,
    }
