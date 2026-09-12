"""Action Gateway + Approval Manager.

Reversible actions (draft, label, archive, defer, delegate) execute directly.
Irreversible actions (send, delete, forward) MUST pass require_approval()
first -- there is no other code path that can reach the outbox or the
delete-log. This is the Part 6 / R3 defence: a hostile message can influence
a *draft*, but nothing it says can reach send/delete without a human (or
--dry-run, which only ever prints intent and writes zero bytes to outbox/).
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTBOX_DIR = ROOT / "outbox"
PENDING_PATH = ROOT / "pending_actions.json"
APPROVAL_LOG_PATH = ROOT / "approval_log.jsonl"

IRREVERSIBLE = {"send", "delete", "forward"}
REVERSIBLE = {"draft", "label", "archive", "defer", "delegate"}


@dataclass
class PendingAction:
    proposed_action: str
    message_id: str
    reason: str
    payload: dict
    decision_timestamp: str = ""
    human_response: str | None = None
    final_status: str = "pending"


class ApprovalGate:
    """Explicit gate object: require_approval() is the ONLY function that may
    authorize an irreversible action. dry_run short-circuits to "would-do"."""

    def __init__(self, dry_run: bool = False, auto_deny: bool = False):
        self.dry_run = dry_run
        self.auto_deny = auto_deny  # used by non-interactive test/demo runs
        self.pending: list[PendingAction] = []
        self._log_path = APPROVAL_LOG_PATH

    def require_approval(self, action: str, message_id: str, reason: str, payload: dict, prompt=None) -> PendingAction:
        assert action in IRREVERSIBLE, f"{action} is not gated (reversible actions run directly)"
        pa = PendingAction(
            proposed_action=action,
            message_id=message_id,
            reason=reason,
            payload=payload,
            decision_timestamp=datetime.now(timezone.utc).isoformat(),
        )

        if self.dry_run:
            pa.final_status = "dry_run_would_do"
            self._append_log(pa)
            self.pending.append(pa)
            return pa

        if self.auto_deny:
            pa.human_response = "n"
            pa.final_status = "denied"
            self._append_log(pa)
            self.pending.append(pa)
            return pa

        answer = (prompt or input)(f"Approve {action} for {message_id}? [{reason}] (y/n): ").strip().lower()
        pa.human_response = answer
        pa.final_status = "approved" if answer == "y" else "denied"
        if pa.final_status == "approved":
            self._execute(pa)
        self._append_log(pa)
        self.pending.append(pa)
        return pa

    def _execute(self, pa: PendingAction) -> None:
        try:
            OUTBOX_DIR.mkdir(exist_ok=True)
            out_file = OUTBOX_DIR / f"{pa.proposed_action}_{pa.message_id}.json"
            if out_file.exists():
                pa.final_status = "approved_already_written"
                return
            with open(out_file, "x", encoding="utf-8") as f:
                json.dump(pa.payload, f, indent=2)
            pa.final_status = "approved_and_written"
        except OSError as exc:
            pa.final_status = "failed_write"
            pa.reason = f"{pa.reason}; outbox write failed: {exc.__class__.__name__}"

    def _append_log(self, pa: PendingAction) -> None:
        with open(self._log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(pa)) + "\n")

    def write_pending_snapshot(self) -> None:
        with open(PENDING_PATH, "w", encoding="utf-8") as f:
            json.dump([asdict(p) for p in self.pending], f, indent=2)

    def outbox_write_count(self) -> int:
        if not OUTBOX_DIR.exists():
            return 0
        return len(list(OUTBOX_DIR.glob("*.json")))


def do_reversible(action: str, message_id: str, payload: dict) -> dict:
    assert action in REVERSIBLE
    return {"action": action, "message_id": message_id, "payload": payload, "status": "executed"}
