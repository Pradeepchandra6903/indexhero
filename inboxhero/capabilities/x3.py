"""X3 - Relationship Intelligence (Tier B).

Builds a per-correspondent profile: relationship type, message volume, and
who has been waiting longest on Sam -- surfaced from sender domain/local-part
patterns already used by the RuleEngine/SecurityScanner (no new data source)."""
from __future__ import annotations

from collections import defaultdict

RELATIONSHIP_RULES = [
    ("paperjet.io", "colleague"),
    ("paperjet-board.org", "board"),
    ("hartwellcho.com", "legal_counsel"),
    (".vc", "investor"),
    ("acme-corp.com", "prospect"),
    ("oldfriends.net", "personal"),
    ("techbrief.news", "press"),
    ("thegrandvenue.com", "vendor"),
    ("zenboard.io", "vendor"),
    ("gmail.com", "candidate"),
]


def _relationship(domain: str) -> str:
    for hint, label in RELATIONSHIP_RULES:
        if hint in domain:
            return label
    return "unclassified"


def run(ctx) -> dict:
    dec_by_id = {d.message_id: d for d in ctx.decisions}
    profiles: dict[str, dict] = defaultdict(lambda: {"count": 0, "last_seen": "", "relationship": "", "waiting": []})

    for m in ctx.messages:
        if m.is_self_sent:
            continue
        p = profiles[m.from_]
        p["count"] += 1
        p["relationship"] = _relationship(m.domain)
        p["last_seen"] = max(p["last_seen"], m.timestamp)
        dec = dec_by_id.get(m.id)
        if dec and dec.disposition in ("reply", "escalate", "delegate"):
            p["waiting"].append(m.id)

    print(f"{'sender':32} {'relationship':14} {'msgs':5} waiting_on_sam")
    print("-" * 80)
    ranked = sorted(profiles.items(), key=lambda kv: (-len(kv[1]["waiting"]), kv[0]))
    for sender, p in ranked:
        print(f"{sender:32} {p['relationship']:14} {p['count']:5} {p['waiting']}")

    ctx.trace.log(
        component="RelationshipIntelligence",
        message_id=None,
        reasoning_summary=f"Profiled {len(profiles)} correspondents; "
                           f"{sum(1 for p in profiles.values() if p['waiting'])} have messages awaiting Sam's reply.",
        evidence_messages=[mid for p in profiles.values() for mid in p["waiting"]][:8],
        final_action="relationship_profile",
        cap="X3",
    )

    return {"correspondents": len(profiles), "profiles": dict(profiles)}
