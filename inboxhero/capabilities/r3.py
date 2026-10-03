"""R3 - Gate the irreversible.

What gets proposed is decided by `actions.select_send_candidates`, a policy
over the Router's own output (non-hostile + disposition `reply` + a grounded
draft exists), not by a list of message ids. What gets approved is the real
reply: the payload written to `outbox/` carries recipient, CC, subject, body
and the cited evidence ids, so an approval is an approval of the actual text.
"""
from __future__ import annotations

from ..actions import ApprovalGate, select_send_candidates, unsendable_replies
from ..drafting import DraftBuilder


def run(ctx) -> dict:
    dry_run = getattr(ctx.args, "dry_run", False)
    gate = ApprovalGate(dry_run=dry_run, auto_deny=getattr(ctx.args, "auto_deny", False))

    builder = DraftBuilder(ctx.messages, ctx.decisions, ctx.memory)
    drafts = builder.build_all(d.message_id for d in ctx.decisions if d.disposition == "reply")
    candidates = select_send_candidates(ctx.decisions, drafts)

    print(f"send policy: non-hostile + disposition=reply + grounded draft exists "
          f"-> {len(candidates)} of {len(ctx.decisions)} messages")

    proposed = []
    for decision, draft in candidates:
        reason = f"send grounded {draft.kind} reply to {draft.to} for {decision.message_id}, cited {draft.cited}"
        pending = gate.require_approval(
            action="send",
            message_id=decision.message_id,
            reason=reason,
            payload=draft.as_payload(),
        )
        proposed.append(pending)
        prefix = "[DRY-RUN] would send" if dry_run else "send"
        cc = f" cc={draft.cc}" if draft.cc else ""
        print(f"{prefix} -> {decision.message_id} to {draft.to}{cc} "
              f"cited={draft.cited} => {pending.final_status}")
        ctx.trace.log(
            component="ApprovalGate",
            message_id=decision.message_id,
            reasoning_summary=reason,
            evidence_messages=draft.cited,
            final_action=pending.final_status,
            cap="R3",
            extra={
                "gate": "require_approval",
                "proposed_action": "send",
                "recipient": draft.to,
                "cc": draft.cc,
                "human_response": pending.human_response,
            },
        )

    withheld = unsendable_replies(ctx.decisions, drafts)
    for decision, why in withheld:
        print(f"withheld -> {decision.message_id}: {why}; left for a human to author")
        ctx.trace.log(
            component="ApprovalGate",
            message_id=decision.message_id,
            reasoning_summary=f"Not proposed as a send: {why}.",
            evidence_messages=[],
            final_action="withheld_ungrounded",
            cap="R3",
        )

    escalations = [d for d in ctx.decisions if d.disposition == "escalate" and d.category != "security_threat"]
    print(f"escalations needing a human author (not auto-proposed): "
          f"{[d.message_id for d in escalations]}")

    gate.write_pending_snapshot()
    print(f"\noutbox/ writes made by this run: {gate.writes_by_this_gate()}"
          f"{' (dry-run: nothing is written)' if dry_run else ''}")
    print(f"files now in outbox/ (including earlier approved runs): {gate.outbox_write_count()}")

    return {
        "dry_run": dry_run,
        "proposed": len(proposed),
        "withheld": [d.message_id for d, _ in withheld],
        "escalated_to_human": [d.message_id for d in escalations],
        "writes_this_run": gate.writes_by_this_gate(),
        "outbox_files": gate.outbox_write_count(),
        "pending_actions_path": "pending_actions.json",
    }
