from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace

from inboxhero import actions
from inboxhero.actions import ApprovalGate
from inboxhero.capabilities import r2
from inboxhero.dashboard import build_dashboard
from inboxhero.memory import Preference, PreferenceMemory
from inboxhero.models import load_inbox
from inboxhero.models import Message
from inboxhero.router import DISPOSITIONS, Router
from inboxhero.security import SecurityScanner


ROOT = Path(__file__).resolve().parents[1]


class TraceStub:
    def __init__(self):
        self.events = []

    def log(self, **event):
        self.events.append(event)


class InboxHeroTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.messages = load_inbox(ROOT / "inbox.json")
        cls.decisions = [Router().route(message) for message in cls.messages]

    def test_inbox_schema_and_unique_ids(self):
        self.assertEqual(100, len(self.messages))
        self.assertEqual(100, len({message.id for message in self.messages}))
        self.assertTrue(all(message.body and isinstance(message.unread, bool) for message in self.messages))

    def test_loader_accepts_empty_body(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "empty-body.json"
            path.write_text(json.dumps([{"id": "e001", "thread_id": "t", "from": "a@example.com", "to": "sam@paperjet.io", "subject": "Empty", "timestamp": "2026-09-10T10:00:00", "body": "", "unread": True}]), encoding="utf-8")
            self.assertEqual("", load_inbox(path)[0].body)

    def test_loader_rejects_duplicate_and_invalid_records(self):
        cases = [
            [{"id": "x"}],
            [{"id": "x", "thread_id": "t", "from": "a@example.com", "to": "sam@paperjet.io", "subject": "s", "timestamp": "bad", "body": "b", "unread": True}],
            [{"id": "x", "thread_id": "t", "from": "a@example.com", "to": "sam@paperjet.io", "subject": "s", "timestamp": "2026-09-10T10:00:00", "body": "b", "unread": True}, {"id": "x", "thread_id": "t", "from": "b@example.com", "to": "sam@paperjet.io", "subject": "s", "timestamp": "2026-09-10T10:01:00", "body": "b", "unread": True}],
        ]
        with tempfile.TemporaryDirectory() as temporary_directory:
            for index, data in enumerate(cases):
                path = Path(temporary_directory) / f"bad-{index}.json"
                path.write_text(json.dumps(data), encoding="utf-8")
                with self.assertRaises(SystemExit):
                    load_inbox(path)

    def test_loader_accepts_empty_inbox(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "empty.json"
            path.write_text("[]", encoding="utf-8")
            self.assertEqual([], load_inbox(path))

    def test_every_message_has_one_defined_disposition_and_reason(self):
        self.assertEqual(len(self.messages), len(self.decisions))
        self.assertEqual(len(self.decisions), len({decision.message_id for decision in self.decisions}))
        self.assertTrue(all(decision.disposition in DISPOSITIONS and decision.reason for decision in self.decisions))

    def test_hostile_messages_are_detected(self):
        hostile = {"m017", "m021", "m023", "m024", "m039", "m045", "m047"}
        scanner = SecurityScanner()
        found = {message.id for message in self.messages if scanner.scan(message).is_hostile}
        self.assertTrue(hostile.issubset(found))

    def test_adversarial_security_fixtures_are_detected(self):
        data = json.loads((ROOT / "tests" / "fixtures" / "security" / "adversarial_messages.json").read_text(encoding="utf-8"))
        messages = [Message.from_dict(record) for record in data]
        results = SecurityScanner().scan_many(messages)
        self.assertTrue(all(result.is_hostile for result in results.values()))
        self.assertIn("split_instruction_attack", results["s004a"].kinds + [finding.signal for finding in results["s004a"].findings])
        self.assertIn("encoded_forward_request", [finding.signal for finding in results["s006"].findings])

    def test_grounded_draft_does_not_replay_secret(self):
        ctx = SimpleNamespace(
            messages=self.messages,
            args=SimpleNamespace(msg="m008"),
            trace=TraceStub(),
        )
        with redirect_stdout(io.StringIO()):
            result = r2.run(ctx)
        self.assertEqual(["m003"], result["cited"])
        self.assertNotIn("Rk7-quiet", result["draft"])
        self.assertIn("can't resend connection credentials", result["draft"])

    def test_dry_run_does_not_write_outbox(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            old_outbox, old_pending, old_log = actions.OUTBOX_DIR, actions.PENDING_PATH, actions.APPROVAL_LOG_PATH
            directory = Path(temporary_directory)
            actions.OUTBOX_DIR = directory / "outbox"
            actions.PENDING_PATH = directory / "pending.json"
            actions.APPROVAL_LOG_PATH = directory / "approval.jsonl"
            try:
                gate = ApprovalGate(dry_run=True)
                result = gate.require_approval("send", "m008", "test", {"message_id": "m008"})
                self.assertEqual("dry_run_would_do", result.final_status)
                self.assertFalse(actions.OUTBOX_DIR.exists())
            finally:
                actions.OUTBOX_DIR, actions.PENDING_PATH, actions.APPROVAL_LOG_PATH = old_outbox, old_pending, old_log

    def test_approved_action_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            old_outbox, old_pending, old_log = actions.OUTBOX_DIR, actions.PENDING_PATH, actions.APPROVAL_LOG_PATH
            directory = Path(temporary_directory)
            actions.OUTBOX_DIR = directory / "outbox"
            actions.PENDING_PATH = directory / "pending.json"
            actions.APPROVAL_LOG_PATH = directory / "approval.jsonl"
            try:
                first = ApprovalGate().require_approval("send", "m008", "test", {}, prompt=lambda _: "y")
                second = ApprovalGate().require_approval("send", "m008", "test", {}, prompt=lambda _: "y")
                self.assertEqual("approved_and_written", first.final_status)
                self.assertEqual("approved_already_written", second.final_status)
                self.assertEqual(1, len(list(actions.OUTBOX_DIR.glob("*.json"))))
            finally:
                actions.OUTBOX_DIR, actions.PENDING_PATH, actions.APPROVAL_LOG_PATH = old_outbox, old_pending, old_log

    def test_preference_survives_new_memory_instance(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "preferences.json"
            first_run = PreferenceMemory(path)
            first_run.remember(Preference("test_preference", {"enabled": True}, "m015", "test", "now"))
            second_run = PreferenceMemory(path)
            self.assertEqual("m015", second_run.get("test_preference")["source_message_id"])

    def test_corrupt_preference_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "preferences.json"
            path.write_text("{not-json", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "not valid JSON"):
                PreferenceMemory(path)

    def test_dashboard_has_exactly_three_data_panes_and_valid_citations(self):
        dashboard = build_dashboard(self.messages, self.decisions)
        self.assertEqual({"pending_actions", "flagged_items", "commitments_calendar", "conflicts"}, set(dashboard))
        derived = next(item for item in dashboard["commitments_calendar"] if item.get("cited") == ["m038", "m040"])
        self.assertEqual(["m038", "m040"], derived["cited"])


if __name__ == "__main__":
    unittest.main()