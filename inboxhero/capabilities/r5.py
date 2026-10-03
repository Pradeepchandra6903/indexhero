"""R5 - Refuse embedded instructions.

Reports *what each message tried to make the assistant do* (and the attacker's
target where the text names one), not just a threat category -- every finding
is listed, not only the first, and the same detail is what goes into
trace.jsonl so the refusal record names the attempted action.

`scan_many` is used rather than `scan` so an instruction split across two
messages in one thread is caught as well.
"""
from __future__ import annotations

from ..drafting import DraftBuilder
from ..security import SecurityScanner


def run(ctx) -> dict:
    scanner = SecurityScanner()
    results = scanner.scan_many(ctx.messages)
    builder = DraftBuilder(ctx.messages, ctx.decisions, ctx.memory)
    flagged = []

    for msg in ctx.messages:
        scan = results[msg.id]
        if not scan.is_hostile:
            continue

        print(f"FLAGGED {msg.id} (from {msg.from_}) -- refused, left in place, not forwarded, not deleted:")
        for finding in scan.findings:
            print(f"    attempted: {finding.describe()}")
            print(f"      [{finding.kind}:{finding.signal}, {finding.severity}] evidence: \"{finding.evidence}\"")

        draft = builder.build(msg.id)
        print(f"    reply drafted for it: {'none (refused)' if draft.refused else draft.kind}")

        flagged.append(
            {
                "message_id": msg.id,
                "thread_id": msg.thread_id,
                "from": msg.from_,
                "kinds": scan.kinds,
                "attempted_actions": scan.attempted_actions,
                "findings": [
                    {
                        "kind": f.kind,
                        "signal": f.signal,
                        "evidence": f.evidence,
                        "severity": f.severity,
                        "attempted_action": f.attempted_action,
                        "target": f.target,
                    }
                    for f in scan.findings
                ],
            }
        )
        ctx.trace.log(
            component="SecurityAgent",
            message_id=msg.id,
            reasoning_summary=(
                f"Refused {msg.id}: attempted " + "; ".join(scan.attempted_actions)
            ),
            evidence_messages=[msg.id],
            final_action="refusal",
            cap="R5",
            extra={
                "attempted_actions": scan.attempted_actions,
                "signals": [f.signal for f in scan.findings],
                "targets": [f.target for f in scan.findings if f.target],
                "kinds": scan.kinds,
            },
        )

    print(f"\ntotal flagged: {len(flagged)}")
    print("attempted actions across the inbox:")
    for action in sorted({a for item in flagged for a in item["attempted_actions"]}):
        print(f"  - {action}")
    print("No flagged message was replied to, forwarded, deleted, or hidden; "
          "each remains in the inbox with a disposition of escalate.")

    return {"flagged_count": len(flagged), "flagged": flagged}
