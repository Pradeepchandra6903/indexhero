"""R3 - Gate the irreversible: never send/delete without approval or --dry-run."""
from __future__ import annotations

from ..actions import ApprovalGate

# Candidate irreversible actions this run proposes. Each ties back to a
# disposition produced by the Router (reply/escalate), never to a message the
# SecurityScanner flagged (R5 handles those separately -- refuse, don't gate).
CANDIDATE_SENDS = ["m008", "m010", "m013", "m016", "m018", "m043", "m061"]


def run(ctx) -> dict:
    dry_run = getattr(ctx.args, "dry_run", False)
    gate = ApprovalGate(dry_run=dry_run, auto_deny=getattr(ctx.args, "auto_deny", False))

    decisions_by_id = {d.message_id: d for d in ctx.decisions}
    proposed = []
    for mid in CANDIDATE_SENDS:
        d = decisions_by_id.get(mid)
        if not d or d.category == "security_threat":
            continue
        reason = f"send reply for {mid} ({d.disposition}): {d.reason[:80]}"
        pa = gate.require_approval(
            action="send",
            message_id=mid,
            reason=reason,
            payload={"message_id": mid, "disposition": d.disposition, "reason": d.reason},
        )
        proposed.append(pa)
        print(f"{'[DRY-RUN] would send' if dry_run else 'send'} -> {mid}: {reason} => {pa.final_status}")
        ctx.trace.log(
            component="ApprovalGate",
            message_id=mid,
            reasoning_summary=reason,
            evidence_messages=[mid],
            final_action=pa.final_status,
            cap="R3",
            extra={"gate": "require_approval", "proposed_action": "send"},
        )

    gate.write_pending_snapshot()
    writes = gate.outbox_write_count()
    print(f"\noutbox/ writes: {writes}")

    return {
        "dry_run": dry_run,
        "proposed": len(proposed),
        "outbox_writes": writes,
        "pending_actions_path": "pending_actions.json",
    }
