"""Dashboard Generator: three panes (Pending Actions, Flagged Items,
Commitments Calendar), each message cited by id, conflicts highlighted,
cross-thread commitments supported (board deck / board review demo)."""
from __future__ import annotations

import html
import json
from pathlib import Path

from .commitments import (
    Commitment,
    derive_cross_thread_commitments,
    detect_conflicts,
    extract_commitments,
    validate_citations,
)
from .models import Message, by_id
from .router import Decision

ROOT = Path(__file__).resolve().parent.parent
DASHBOARD_JSON = ROOT / "dashboard.json"
DASHBOARD_HTML = ROOT / "dashboard.html"


def build_dashboard(messages: list[Message], decisions: list[Decision]) -> dict:
    msg_by_id = by_id(messages)
    dec_by_id = {d.message_id: d for d in decisions}

    pending_actions = [
        {
            "message_id": d.message_id,
            "thread_id": d.thread_id,
            "disposition": d.disposition,
            "reason": d.reason,
            "subject": msg_by_id[d.message_id].subject if d.message_id in msg_by_id else "",
        }
        for d in decisions
        if d.disposition in ("reply", "escalate", "defer", "delegate") and d.category != "security_threat"
    ]

    flagged_items = [
        {
            "message_id": d.message_id,
            "thread_id": d.thread_id,
            "reason": d.reason,
            "flags": d.flags,
        }
        for d in decisions
        if d.category == "security_threat"
    ]

    commitments = extract_commitments(messages)
    conflicts = detect_conflicts(commitments)
    conflict_ids = {mid for c in conflicts for mid in c["message_ids"]}

    # Every commitment carries the ids it came from, and those ids are checked
    # against the mail store before the pane is built (Part 7, same rule as
    # Part 3). Anything that cites a message this inbox does not contain is
    # dropped and reported rather than rendered.
    commitments_calendar = []
    citation_errors = []

    for c in commitments:
        present, missing = validate_citations([c.message_id], messages)
        if missing:
            citation_errors.append({"text": c.text, "missing": missing})
            continue
        commitments_calendar.append(
            {
                "message_id": c.message_id,
                "thread_id": c.thread_id,
                "text": c.text,
                "day": c.day_token,
                "time": c.time_token,
                "conflict": c.message_id in conflict_ids,
                "cited": present,
                "source": "single_message",
            }
        )

    # Cross-thread commitments are computed: the offset and subject come from
    # the referring message, the absolute date from the message that schedules
    # the event, and the due date is arithmetic over the two.
    for derived in derive_cross_thread_commitments(messages):
        present, missing = validate_citations(derived.cited, messages)
        if missing:
            citation_errors.append({"text": derived.text, "missing": missing})
            continue
        commitments_calendar.append(
            {
                "message_id": "+".join(present),
                "thread_id": "derived:" + "+".join(
                    sorted({msg_by_id[cid].thread_id for cid in present})
                ),
                "text": derived.text,
                "day": derived.day_token,
                "time": derived.time_token,
                "conflict": False,
                "cited": present,
                "source": "cross_thread_derived",
                "derivation": derived.derivation,
            }
        )

    return {
        "pending_actions": pending_actions,
        "flagged_items": flagged_items,
        "commitments_calendar": commitments_calendar,
        "conflicts": conflicts,
        "citation_errors": citation_errors,
    }


def write_dashboard_json(data: dict) -> None:
    with open(DASHBOARD_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def _esc(s) -> str:
    return html.escape(str(s), quote=True)


def render_html(data: dict) -> str:
    def pending_rows():
        rows = []
        for p in data["pending_actions"]:
            rows.append(
                f"<tr><td>{_esc(p['message_id'])}</td><td>{_esc(p['disposition'])}</td>"
                f"<td>{_esc(p['subject'])}</td><td>{_esc(p['reason'])}</td></tr>"
            )
        return "\n".join(rows)

    def flagged_rows():
        rows = []
        for fitem in data["flagged_items"]:
            flags = ", ".join(fitem["flags"])
            rows.append(
                f"<tr><td>{_esc(fitem['message_id'])}</td><td class='threat'>{_esc(flags)}</td>"
                f"<td>{_esc(fitem['reason'])}</td></tr>"
            )
        return "\n".join(rows)

    def commitment_rows():
        rows = []
        for c in data["commitments_calendar"]:
            cls = " class='conflict'" if c.get("conflict") else ""
            when = f"{c.get('day') or ''} {c.get('time') or ''}".strip()
            cited = ", ".join(c.get("cited", [])) if c.get("cited") else c["message_id"]
            marker = "\u26a0 CONFLICT " if c.get("conflict") else ""
            text = _esc(c["text"])
            if c.get("derivation"):
                text += f"<br><span class='derivation'>derived: {_esc(c['derivation'])}</span>"
            rows.append(
                f"<tr{cls}><td>{_esc(cited)}</td><td>{_esc(when)}</td>"
                f"<td>{marker}{text}</td></tr>"
            )
        return "\n".join(rows)

    def conflict_banner():
        if not data["conflicts"]:
            return ""
        items = "".join(
            f"<li>CONFLICT: {_esc(c['day'])} {_esc(c['time'])} \u2014 {', '.join(_esc(m) for m in c['message_ids'])}</li>"
            for c in data["conflicts"]
        )
        return f"<div class='banner'><strong>Scheduling conflicts detected:</strong><ul>{items}</ul></div>"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>InboxHero Dashboard</title>
<style>
  body {{ font-family: -apple-system, Segoe UI, Arial, sans-serif; margin: 2rem; background: #f7f7f9; color: #1a1a1a; }}
  h1 {{ margin-bottom: 0.2rem; }}
  section {{ background: #fff; border: 1px solid #ddd; border-radius: 8px; padding: 1rem 1.25rem; margin-bottom: 1.5rem; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 0.9rem; }}
  th, td {{ text-align: left; padding: 0.4rem 0.6rem; border-bottom: 1px solid #eee; vertical-align: top; }}
  th {{ background: #fafafa; }}
  tr.conflict {{ background: #fff2f2; }}
  td.threat {{ color: #a30000; font-weight: 600; }}
  .banner {{ background: #fff2f2; border: 1px solid #e0a0a0; border-radius: 6px; padding: 0.75rem 1rem; margin-bottom: 1rem; }}
  .pill {{ display:inline-block; background:#eef; border-radius: 999px; padding: 0.1rem 0.6rem; font-size: 0.8rem; }}
  .derivation {{ color: #555; font-size: 0.8rem; }}
</style>
</head>
<body>
<h1>InboxHero Dashboard</h1>
<p class="pill">Generated by capability R6 &middot; deterministic, reproducible from trace.jsonl</p>

<section>
<h2>Pending Actions</h2>
<table>
<tr><th>Message ID</th><th>Disposition</th><th>Subject</th><th>Reason</th></tr>
{pending_rows()}
</table>
</section>

<section>
<h2>Flagged Items</h2>
<table>
<tr><th>Message ID</th><th>Threat</th><th>Reason</th></tr>
{flagged_rows()}
</table>
</section>

<section>
<h2>Commitments Calendar</h2>
{conflict_banner()}
<table>
<tr><th>Source message id(s)</th><th>When</th><th>Commitment</th></tr>
{commitment_rows()}
</table>
</section>

</body>
</html>
"""


def write_dashboard_html(data: dict) -> None:
    with open(DASHBOARD_HTML, "w", encoding="utf-8") as f:
        f.write(render_html(data))
