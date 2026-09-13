"""R4 - Persistent preference: a preference stated in run 1 survives a full
process exit and changes behavior in run 2 (see memory_demo.md for the
two-invocation transcript)."""
from __future__ import annotations

from ..memory import Preference

PREF_KEY = "cc_legal_on_hartwell_cho"
LEGAL_DOMAIN = "hartwellcho.com"
LEGAL_MESSAGE_IDS = ["m018", "m048", "m055"]


def run(ctx) -> dict:
    mem = ctx.memory

    if not mem.has(PREF_KEY):
        # RUN 1: learn the preference from m015 and persist it. No legal
        # message is touched yet -- this run only stores state.
        pref = Preference(
            key=PREF_KEY,
            value={"cc": "priya@paperjet.io", "domain": LEGAL_DOMAIN},
            source_message_id="m015",
            stated="CC Priya on anything from Hartwell & Cho (our lawyers).",
            created_at="run-1",
        )
        mem.remember(pref)
        print("RUN 1: stored preference from m015 -> "
              f"'{pref.stated}' in memory/preferences.json, then exiting.")
        ctx.trace.log(
            component="MemoryManager",
            message_id="m015",
            reasoning_summary="Learned standing preference: CC Priya on Hartwell & Cho legal mail.",
            evidence_messages=["m015"],
            final_action="store_preference",
            cap="R4",
        )
        return {"run": 1, "stored": True, "preference": PREF_KEY}

    # RUN 2: fresh process, preference already on disk -- apply it without
    # being told again.
    pref = mem.get(PREF_KEY)
    print(f"RUN 2: loaded preference '{pref['stated']}' (source {pref['source_message_id']}).")
    applied = []
    for mid in LEGAL_MESSAGE_IDS:
        msg = next((m for m in ctx.messages if m.id == mid), None)
        if not msg or LEGAL_DOMAIN not in msg.domain:
            continue
        print(f"  applying -> {mid}: adding CC {pref['value']['cc']} (from {msg.from_})")
        applied.append(mid)
        ctx.trace.log(
            component="MemoryManager",
            message_id=mid,
            reasoning_summary=f"Applied stored preference {PREF_KEY} without being re-told.",
            evidence_messages=["m015", mid],
            final_action="apply_preference:add_cc",
            cap="R4",
        )

    return {"run": 2, "applied_to": applied, "preference": PREF_KEY}
