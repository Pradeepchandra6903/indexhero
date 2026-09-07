"""Trace Logger: one append-only JSONL event per decision, everywhere.

Every event carries: timestamp, message_id, component, reasoning_summary,
evidence_messages, final_action -- so any capability's behavior can be
reconstructed from trace.jsonl alone (Part 9 requirement).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parent.parent
TRACE_PATH = ROOT / "trace.jsonl"


class TraceLogger:
    def __init__(self, path: Path | None = None, truncate: bool = False):
        self.path = path or TRACE_PATH
        self.run_id = uuid4().hex
        if truncate and self.path.exists():
            self.path.unlink()

    def log(
        self,
        component: str,
        message_id: str | None,
        reasoning_summary: str,
        evidence_messages: list[str] | None = None,
        final_action: str = "",
        cap: str | None = None,
        extra: dict | None = None,
    ) -> dict:
        event = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "run_id": self.run_id,
            "message_id": message_id,
            "component": component,
            "reasoning_summary": reasoning_summary,
            "evidence_messages": evidence_messages or [],
            "final_action": final_action,
        }
        if cap:
            event["cap"] = cap
        if extra:
            event.update(extra)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event) + "\n")
        return event
