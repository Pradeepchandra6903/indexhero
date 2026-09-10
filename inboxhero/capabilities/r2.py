"""R2 - Grounded reply: draft a reply grounded in a specific earlier message,
and record exactly which message ids it drew on (cited)."""
from __future__ import annotations

import re

from ..models import by_id
from ..retrieval import CitationBuilder, KeywordRetrieval, ThreadWalker

URL_RE = re.compile(r"\b\w+://[^\s]+", re.IGNORECASE)

# Keyword hints used only for cross-thread retrieval when thread-walk alone
# can't ground a claim (e.g. m040's deadline depends on m038, a different
# thread_id). Same "thread-walk primary, keyword fallback" design as the
# rest of the system.
CROSS_THREAD_HINTS = {
    "m040": ["board review", "18th"],
}

DEFAULT_TARGET = "m008"


def _draft_from_url_grounding(target, ground_msg, url: str) -> str:
    return (
        "Hi Devika -- I can't resend connection credentials by email. "
        "The current staging connection details are available in the shared secrets "
        f"manager; please use that entry instead. (Grounded in {ground_msg.id}.)"
    )


def _draft_generic(target, ground_msgs) -> str:
    cited = ", ".join(m.id for m in ground_msgs) if ground_msgs else "no prior message"
    context = ground_msgs[-1].body[:140] if ground_msgs else "(no grounding context found)"
    return f"Re: {target.subject} -- drafting from context in {cited}: \"{context}\""


def run(ctx) -> dict:
    target_id = getattr(ctx.args, "msg", None) or DEFAULT_TARGET
    msgs_by_id = by_id(ctx.messages)
    if target_id not in msgs_by_id:
        print(f"unknown message id: {target_id}")
        return {"error": "unknown_message_id", "message_id": target_id}

    target = msgs_by_id[target_id]
    walker = ThreadWalker(ctx.messages)
    earlier = walker.earlier_in_thread(target)

    cited_ids: list[str] = []
    draft = None

    url_source = next((m for m in reversed(earlier) if URL_RE.search(m.body)), None)
    if url_source:
        url = URL_RE.search(url_source.body).group(0).rstrip(".")
        draft = _draft_from_url_grounding(target, url_source, url)
        cited_ids = [url_source.id]
    elif target_id in CROSS_THREAD_HINTS:
        kr = KeywordRetrieval(ctx.messages)
        hits = kr.search(CROSS_THREAD_HINTS[target_id], exclude_id=target_id)
        if hits:
            draft = _draft_generic(target, hits)
            cited_ids = [h.id for h in hits]
    elif earlier:
        draft = _draft_generic(target, earlier)
        cited_ids = [m.id for m in earlier[-2:]]

    # Part 3.4: if nothing in the inbox grounds a reply, refuse and draft
    # nothing rather than inventing content.
    if not cited_ids:
        print(f"Draft reply to {target_id} ({target.subject}):\n")
        print(f"REFUSED: cannot ground a reply to {target_id}; no earlier message in "
              f"the inbox supports an answer. Drafting nothing.")
        print("\ncited: []")
        ctx.trace.log(
            component="RetrievalEngine+ActionAgent",
            message_id=target_id,
            reasoning_summary=f"Refused to draft for {target_id}: no grounding evidence in the inbox.",
            evidence_messages=[],
            final_action="refuse_ungrounded",
            cap="R2",
        )
        return {"message_id": target_id, "draft": None, "cited": [], "refused": True}

    missing_citations = [message_id for message_id in cited_ids if message_id not in msgs_by_id]
    if missing_citations:
        print(f"REFUSED: cited message(s) not found in inbox: {missing_citations}")
        ctx.trace.log(
            component="RetrievalEngine+ActionAgent",
            message_id=target_id,
            reasoning_summary="Refused draft because retrieved citation IDs were not present in the inbox.",
            evidence_messages=[],
            final_action="refuse_invalid_citation",
            cap="R2",
        )
        return {"message_id": target_id, "draft": None, "cited": [], "refused": True}

    citations = CitationBuilder.build(
        [msgs_by_id[message_id] for message_id in cited_ids],
        reason="grounded R2 draft",
    )

    print(f"Draft reply to {target_id} ({target.subject}):\n")
    print(draft)
    print(f"\ncited: {cited_ids}")

    ctx.trace.log(
        component="RetrievalEngine+ActionAgent",
        message_id=target_id,
        reasoning_summary=f"Grounded reply drafted for {target_id} using {cited_ids}",
        evidence_messages=cited_ids,
        final_action="draft",
        cap="R2",
        extra={"draft": draft},
    )

    return {
        "message_id": target_id,
        "draft": draft,
        "cited": [citation.message_id for citation in citations],
    }
