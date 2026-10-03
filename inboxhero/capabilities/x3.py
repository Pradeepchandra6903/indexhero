"""X3 - Relationship Intelligence (Tier B).

Builds a per-correspondent profile: relationship type, message volume, and who
has been waiting *longest* on Sam -- ranked by the age of the oldest unanswered
message (days), not by how many are outstanding, so one person waiting a week
outranks someone with three messages from this morning. Clock is the newest
timestamp in the mailbox, which keeps the output deterministic."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime

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


def _age_days(timestamp: str, now: datetime) -> float:
    try:
        return round((now - datetime.fromisoformat(timestamp)).total_seconds() / 86400, 1)
    except ValueError:
        return 0.0


def run(ctx) -> dict:
    dec_by_id = {d.message_id: d for d in ctx.decisions}
    now = max(
        (datetime.fromisoformat(m.timestamp) for m in ctx.messages),
        default=datetime.now(),
    )
    profiles: dict[str, dict] = defaultdict(
        lambda: {
            "count": 0,
            "last_seen": "",
            "relationship": "",
            "waiting": [],
            "oldest_waiting_id": None,
            "waiting_days": 0.0,
        }
    )

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
            age = _age_days(m.timestamp, now)
            if age > p["waiting_days"]:
                p["waiting_days"], p["oldest_waiting_id"] = age, m.id

    # Longest wait first; message count only breaks ties.
    ranked = sorted(
        profiles.items(),
        key=lambda kv: (-kv[1]["waiting_days"], -len(kv[1]["waiting"]), kv[0]),
    )

    waiting_profiles = [(s, p) for s, p in ranked if p["waiting"]]
    quiet = len(ranked) - len(waiting_profiles)

    print(f"clock = newest message in the mailbox ({now.isoformat()})")
    print(f"{'sender':36} {'relationship':14} {'msgs':5} {'waited':>7} {'oldest':7} waiting_on_sam")
    print("-" * 104)
    for sender, p in waiting_profiles:
        print(f"{sender:36} {p['relationship']:14} {p['count']:5} "
              f"{p['waiting_days']:6.1f}d {p['oldest_waiting_id']:7} {p['waiting']}")
    print(f"... and {quiet} correspondents with nothing outstanding "
          f"(archived or already answered)")

    longest = [
        {"sender": sender, "waiting_days": p["waiting_days"], "oldest_waiting_id": p["oldest_waiting_id"]}
        for sender, p in ranked
        if p["waiting"]
    ]
    print("\nlongest waits: " + ", ".join(
        f"{item['sender']} ({item['waiting_days']:.1f}d, {item['oldest_waiting_id']})"
        for item in longest[:3]
    ))

    ctx.trace.log(
        component="RelationshipIntelligence",
        message_id=None,
        reasoning_summary=(
            f"Profiled {len(profiles)} correspondents; {len(longest)} are waiting on Sam, "
            f"ranked by age of oldest unanswered message."
        ),
        evidence_messages=[item["oldest_waiting_id"] for item in longest[:8]],
        final_action="relationship_profile",
        cap="X3",
        extra={"longest_waits": longest[:5]},
    )

    return {"correspondents": len(profiles), "longest_waits": longest, "profiles": dict(profiles)}
