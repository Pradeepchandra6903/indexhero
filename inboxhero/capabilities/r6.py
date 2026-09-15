"""R6 - Dashboard: three panes, commitments cited to source messages, and
scheduling conflicts surfaced."""
from __future__ import annotations

from ..dashboard import build_dashboard, write_dashboard_html, write_dashboard_json


def run(ctx) -> dict:
    data = build_dashboard(ctx.messages, ctx.decisions)
    write_dashboard_json(data)
    write_dashboard_html(data)

    print(f"Pending actions: {len(data['pending_actions'])}")
    print(f"Flagged items:   {len(data['flagged_items'])}")
    print(f"Commitments:     {len(data['commitments_calendar'])}")
    for c in data["conflicts"]:
        print(f"CONFLICT: {c['day']} {c['time']} -- {c['message_ids']}")
    print("\nWrote dashboard.html and dashboard.json")

    ctx.trace.log(
        component="DashboardGenerator",
        message_id=None,
        reasoning_summary=(
            f"Built dashboard: {len(data['pending_actions'])} pending, "
            f"{len(data['flagged_items'])} flagged, {len(data['commitments_calendar'])} commitments, "
            f"{len(data['conflicts'])} conflicts."
        ),
        evidence_messages=[c["message_id"] for c in data["pending_actions"][:5]],
        final_action="render_dashboard",
        cap="R6",
    )

    return {
        "pending": len(data["pending_actions"]),
        "flagged": len(data["flagged_items"]),
        "commitments": len(data["commitments_calendar"]),
        "conflicts": data["conflicts"],
    }
