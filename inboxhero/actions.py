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
from dataclasses import asdict, dataclass, field
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
    recipient: str = ""
    cc: list[str] = field(default_factory=list)
    cited: list[str] = field(default_factory=list)
    decision_timestamp: str = ""
    human_response: str | None = None
    final_status: str = "pending"


def select_send_candidates(decisions, drafts: dict) -> list[tuple]:
    """Policy (Part 4): which proposed actions are worth a human's attention.

    Not a list of message ids. A message is proposed as a send when all three
    hold, so the same policy applies to any inbox:

      1. it was not flagged as untrusted content by the SecurityScanner;
      2. the Router's disposition is `reply` -- an `escalate` needs a human to
         *write* the answer, not merely approve one, so those go to
         pending_actions instead of the send queue;
      3. a grounded draft exists for it (a refused draft has nothing to send).

    This is where the escalation line is drawn: a handful of real, answerable
    replies get a prompt each, instead of forty rubber-stamps.
    """
    selected = []
    for decision in decisions:
        if decision.category == "security_threat":
            continue
        if decision.disposition != "reply":
            continue
        draft = drafts.get(decision.message_id)
        if not draft or draft.refused:
            continue
        selected.append((decision, draft))
    return selected


def unsendable_replies(decisions, drafts: dict) -> list[tuple]:
    """Replies the policy deliberately withholds, with the reason why."""
    withheld = []
    for decision in decisions:
        if decision.category == "security_threat" or decision.disposition != "reply":
            continue
        draft = drafts.get(decision.message_id)
        if draft and draft.refused:
            withheld.append((decision, draft.refusal_reason))
    return withheld


class ApprovalGate:
    """Explicit gate object: require_approval() is the ONLY function that may
    authorize an irreversible action. dry_run short-circuits to "would-do"."""

    def __init__(self, dry_run: bool = False, auto_deny: bool = False):
        self.dry_run = dry_run
        self.auto_deny = auto_deny  # used by non-interactive test/demo runs
        self.pending: list[PendingAction] = []
        self._log_path = APPROVAL_LOG_PATH
        self._writes = 0  # bytes this gate itself put in outbox/, dry-run must stay 0

    def require_approval(self, action: str, message_id: str, reason: str, payload: dict, prompt=None) -> PendingAction:
        assert action in IRREVERSIBLE, f"{action} is not gated (reversible actions run directly)"
        pa = PendingAction(
            proposed_action=action,
            message_id=message_id,
            reason=reason,
            payload=payload,
            recipient=payload.get("to", ""),
            cc=list(payload.get("cc", []) or []),
            cited=list(payload.get("cited", []) or []),
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
            self._writes += 1
        except OSError as exc:
            pa.final_status = "failed_write"
            pa.reason = f"{pa.reason}; outbox write failed: {exc.__class__.__name__}"

    def _append_log(self, pa: PendingAction) -> None:
        with open(self._log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(pa)) + "\n")

    def write_pending_snapshot(self) -> None:
        with open(PENDING_PATH, "w", encoding="utf-8") as f:
            json.dump([asdict(p) for p in self.pending], f, indent=2)

    def writes_by_this_gate(self) -> int:
        """Files this run actually created. Always 0 under --dry-run."""
        return self._writes

    def outbox_write_count(self) -> int:
        """Files currently in outbox/, including ones earlier runs wrote."""
        if not OUTBOX_DIR.exists():
            return 0
        return len(list(OUTBOX_DIR.glob("*.json")))


def do_reversible(action: str, message_id: str, payload: dict) -> dict:
    assert action in REVERSIBLE
    return {"action": action, "message_id": message_id, "payload": payload, "status": "executed"}
