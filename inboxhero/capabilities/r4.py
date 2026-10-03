"""R4 - Persistent preference, learned from the message that states it.

Run 1 reads the inbox, extracts standing preferences out of message *text*
(`memory.PreferenceLearner` -- no preference value, person or message id is
hardcoded here), writes them to memory/preferences.json and exits.

Run 2 is a fresh process. It loads what is on disk and applies it: the CC is
attached to real drafts produced by the DraftBuilder, so the preference changes
the artifact that R3 would send, not just a log line.
"""
from __future__ import annotations

from ..drafting import DraftBuilder
from ..memory import CC_KEY_PREFIX, EARLIEST_MEETING_KEY, PreferenceLearner


def run(ctx) -> dict:
    memory = ctx.memory
    learned_keys = sorted(memory.all())

    if not learned_keys:
        # RUN 1: learn from the inbox and persist. No message is answered yet.
        learner = PreferenceLearner(ctx.messages)
        preferences = learner.learn()
        for preference in preferences:
            memory.remember(preference)
            print(f"RUN 1: learned '{preference.key}' from {preference.source_message_id} "
                  f"-> \"{preference.stated}\"")
            ctx.trace.log(
                component="MemoryManager",
                message_id=preference.source_message_id,
                reasoning_summary=f"Extracted standing preference {preference.key} from message text.",
                evidence_messages=[preference.source_message_id],
                final_action="store_preference",
                cap="R4",
                extra={"value": preference.value, "stated": preference.stated},
            )
        print(f"RUN 1: wrote {len(preferences)} preference(s) to memory/preferences.json, then exiting.")
        return {
            "run": 1,
            "stored": [p.key for p in preferences],
            "sources": {p.key: p.source_message_id for p in preferences},
        }

    # RUN 2: fresh process, preferences already on disk -- apply them.
    print(f"RUN 2: loaded {len(learned_keys)} preference(s) from disk: {learned_keys}")
    builder = DraftBuilder(ctx.messages, ctx.decisions, memory)
    applied: list[dict] = []

    for record in memory.find(CC_KEY_PREFIX):
        value = record.get("value") or {}
        domain, address = value.get("domain", ""), value.get("cc", "")
        source_id = record.get("source_message_id", "")
        print(f"  rule from {source_id}: CC {address} on mail from {domain} "
              f"(scope \"{value.get('scope', '')}\")")
        for message in ctx.messages:
            if not domain or not message.domain.endswith(domain):
                continue
            draft = builder.build(message.id)
            if address not in draft.cc:
                continue
            applied.append({
                "message_id": message.id,
                "from": message.from_,
                "cc": draft.cc,
                "draft_kind": draft.kind,
                "cited": draft.cited,
            })
            print(f"    applied -> {message.id} ({message.from_}): draft cc={draft.cc}, "
                  f"cited={draft.cited}")
            ctx.trace.log(
                component="MemoryManager",
                message_id=message.id,
                reasoning_summary=(
                    f"Applied stored preference {record['key']} without being re-told: "
                    f"CC {address} added to the draft for {message.id}."
                ),
                evidence_messages=[source_id, message.id],
                final_action="apply_preference:add_cc",
                cap="R4",
                extra={"cc": draft.cc, "draft_kind": draft.kind},
            )

    scheduling = memory.get(EARLIEST_MEETING_KEY)
    if scheduling:
        source_id = scheduling.get("source_message_id", "")
        earliest = (scheduling.get("value") or {}).get("earliest", "")
        print(f"  rule from {source_id}: no meetings before {earliest} "
              f"(enforced on drafts by X5 / DraftBuilder)")

    print(f"\npreferences applied to {len(applied)} message(s) without restating them: "
          f"{[entry['message_id'] for entry in applied]}")

    return {"run": 2, "applied": applied, "preferences": learned_keys}
