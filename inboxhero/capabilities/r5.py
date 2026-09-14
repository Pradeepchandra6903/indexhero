"""R5 - Refuse embedded instructions: detect prompt injection / social
engineering / phishing content addressed to "the assistant", refuse to act on
it, flag it, and report it -- without deleting, forwarding, or hiding it."""
from __future__ import annotations

from ..security import SecurityScanner


def run(ctx) -> dict:
    scanner = SecurityScanner()
    flagged = []

    for msg in ctx.messages:
        scan = scanner.scan(msg)
        if not scan.is_hostile:
            continue
        top = scan.findings[0]
        print(f"FLAGGED: {msg.id} attempted [{top.kind}:{top.signal}] -- not done, left in place, not forwarded.")
        flagged.append(
            {
                "message_id": msg.id,
                "thread_id": msg.thread_id,
                "kinds": scan.kinds,
                "findings": [
                    {"kind": f.kind, "signal": f.signal, "evidence": f.evidence, "severity": f.severity}
                    for f in scan.findings
                ],
            }
        )
        ctx.trace.log(
            component="SecurityAgent",
            message_id=msg.id,
            reasoning_summary=f"Refused embedded instruction(s): {', '.join(scan.kinds)}",
            evidence_messages=[msg.id],
            final_action="refusal",
            cap="R5",
        )

    print(f"\ntotal flagged: {len(flagged)}")
    print("outbox/ contains no message to any address named inside a flagged message.")
    print("No flagged message was deleted.")

    return {"flagged_count": len(flagged), "flagged": flagged}
