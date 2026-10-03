"""R2 - Grounded reply.

Every sentence of evidence in the draft is quoted from a message the retriever
returned, the cited ids are checked against the mail store before the draft is
printed, and when retrieval finds nothing that supports an answer the
capability refuses and drafts nothing (Part 3.4).

The draft text itself comes from `inboxhero.drafting.DraftBuilder`, which is
the same builder R3 sends and X5 negotiates with -- so what is shown here is
exactly what would be sent.
"""
from __future__ import annotations

from ..drafting import DraftBuilder

DEFAULT_TARGET = "m008"


def run(ctx) -> dict:
    target_id = getattr(ctx.args, "msg", None) or DEFAULT_TARGET
    builder = DraftBuilder(ctx.messages, ctx.decisions, ctx.memory)
    draft = builder.build(target_id)

    if target_id not in builder.by_id:
        print(f"unknown message id: {target_id}")
        return {"error": "unknown_message_id", "message_id": target_id}

    target = builder.by_id[target_id]
    print(f"Draft reply to {target_id} ({target.subject}):\n")

    if draft.refused:
        print(f"REFUSED: {draft.refusal_reason}.")
        print("\ncited: []")
        ctx.trace.log(
            component="RetrievalEngine+ActionAgent",
            message_id=target_id,
            reasoning_summary=f"Refused to draft for {target_id}: {draft.refusal_reason}.",
            evidence_messages=[],
            final_action="refuse_ungrounded",
            cap="R2",
        )
        return {"message_id": target_id, "draft": None, "cited": [], "refused": True}

    print(f"to: {draft.to}")
    if draft.cc:
        print(f"cc: {', '.join(draft.cc)}")
    print(f"subject: {draft.subject}\n")
    print(draft.body)
    print(f"\ncited: {draft.cited}   (grounding kind: {draft.kind})")
    for citation, quoted in zip(draft.cited, draft.evidence):
        print(f"  evidence from {citation}: \"{quoted}\"")

    ctx.trace.log(
        component="RetrievalEngine+ActionAgent",
        message_id=target_id,
        reasoning_summary=f"Grounded reply drafted for {target_id} using {draft.cited} ({draft.kind}).",
        evidence_messages=draft.cited,
        final_action="draft",
        cap="R2",
        extra={"draft": draft.body, "evidence_quotes": draft.evidence, "cc": draft.cc},
    )

    citations = builder.citations_for(draft)
    return {
        "message_id": target_id,
        "draft": draft.body,
        "cited": [citation.message_id for citation in citations],
        "kind": draft.kind,
        "cc": draft.cc,
    }
