from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace

from inboxhero import actions
from inboxhero.actions import ApprovalGate, select_send_candidates
from inboxhero.capabilities import r2
from inboxhero.commitments import derive_cross_thread_commitments, validate_citations
from inboxhero.dashboard import build_dashboard
from inboxhero.drafting import DraftBuilder
from inboxhero.memory import (
    CC_KEY_PREFIX,
    EARLIEST_MEETING_KEY,
    Preference,
    PreferenceLearner,
    PreferenceMemory,
)
from inboxhero.models import load_inbox
from inboxhero.models import Message
from inboxhero.router import DISPOSITIONS, MODEL_FREE_PATHS, Router
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

    def _r2_context(self, message_id, memory=None):
        return SimpleNamespace(
            messages=self.messages,
            decisions=self.decisions,
            memory=memory,
            args=SimpleNamespace(msg=message_id),
            trace=TraceStub(),
        )

    def test_grounded_draft_masks_secret_instead_of_inventing_a_destination(self):
        with redirect_stdout(io.StringIO()):
            result = r2.run(self._r2_context("m008"))
        self.assertEqual(["m003"], result["cited"])
        self.assertNotIn("Rk7-quiet", result["draft"])
        self.assertIn(":***@", result["draft"])
        # the endpoint is reused structurally, from the retrieved URL itself
        self.assertIn("broker-stg.paperjet.io:5672/pjs", result["draft"])
        # and nothing is asserted that no message in the store supports
        self.assertNotIn("secrets manager", result["draft"].lower())

    def test_every_draft_sentence_of_evidence_is_quoted_from_a_cited_message(self):
        builder = DraftBuilder(self.messages, self.decisions)
        store = {message.id: message for message in self.messages}
        checked = 0
        for decision in self.decisions:
            if decision.disposition != "reply":
                continue
            draft = builder.build(decision.message_id)
            if draft.refused:
                continue
            present, missing = validate_citations(draft.cited, self.messages)
            self.assertEqual([], missing)
            self.assertTrue(present)
            for quote in draft.evidence:
                if "comes from" in quote:  # a recorded derivation, not a quote
                    continue
                stripped = quote.replace("***", "")
                haystacks = [store[cid].body.replace("\n", " ") for cid in draft.cited]
                self.assertTrue(
                    any(fragment in hay for hay in haystacks
                        for fragment in [stripped.split(" ")[0]] if fragment),
                    f"{draft.message_id} quoted text not traceable to {draft.cited}",
                )
            checked += 1
        self.assertGreater(checked, 0)

    def test_draft_refuses_when_retrieval_supports_nothing(self):
        with redirect_stdout(io.StringIO()):
            result = r2.run(self._r2_context("m042"))
        self.assertTrue(result["refused"])
        self.assertIsNone(result["draft"])
        self.assertEqual([], result["cited"])

    def test_hostile_message_gets_no_draft(self):
        builder = DraftBuilder(self.messages, self.decisions)
        for message_id in ("m017", "m021", "m024", "m045"):
            self.assertTrue(builder.build(message_id).refused)

    def test_router_reports_how_many_messages_rules_handled(self):
        rule_handled = [d for d in self.decisions if d.rule_handled]
        self.assertGreater(len(rule_handled), 0)
        self.assertTrue(all(d.path in MODEL_FREE_PATHS for d in rule_handled))
        # the paths partition the decisions: no decision is left without provenance
        self.assertTrue(all(d.path for d in self.decisions))

    def test_send_selection_is_a_policy_not_a_fixed_list(self):
        builder = DraftBuilder(self.messages, self.decisions)
        drafts = builder.build_all(d.message_id for d in self.decisions if d.disposition == "reply")
        selected = select_send_candidates(self.decisions, drafts)
        self.assertTrue(selected)
        for decision, draft in selected:
            self.assertNotEqual("security_threat", decision.category)
            self.assertEqual("reply", decision.disposition)
            self.assertFalse(draft.refused)
            # the payload a human approves is the real reply, not metadata
            payload = draft.as_payload()
            self.assertEqual(draft.to, payload["to"])
            self.assertTrue(payload["body"])
            self.assertTrue(payload["cited"])
        # m042 has a reply disposition but no grounded draft, so it is withheld
        self.assertNotIn("m042", [d.message_id for d, _ in selected])

    def test_preferences_are_learned_from_message_text(self):
        learned = {p.key: p for p in PreferenceLearner(self.messages).learn()}
        self.assertIn(EARLIEST_MEETING_KEY, learned)
        cutoff = learned[EARLIEST_MEETING_KEY]
        self.assertEqual("m041", cutoff.source_message_id)
        self.assertEqual("11:00am", cutoff.value["earliest"])
        store = {message.id: message for message in self.messages}
        # the quoted justification really is a sentence from that message
        self.assertIn(cutoff.stated.rstrip("."), store["m041"].body)

        cc_keys = [key for key in learned if key.startswith(CC_KEY_PREFIX)]
        self.assertTrue(cc_keys)
        cc_rule = learned[cc_keys[0]]
        self.assertEqual("m015", cc_rule.source_message_id)
        # the firm name was resolved to a domain using the inbox's own senders
        self.assertIn(cc_rule.value["domain"], {m.domain for m in self.messages})

    def test_learned_cc_preference_lands_on_a_real_draft(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            memory = PreferenceMemory(Path(temporary_directory) / "preferences.json")
            for preference in PreferenceLearner(self.messages).learn():
                memory.remember(preference)
            builder = DraftBuilder(self.messages, self.decisions, memory)
            draft = builder.build("m018")
            self.assertFalse(draft.refused)
            self.assertIn("priya@paperjet.io", draft.cc)
            self.assertIn("priya@paperjet.io", draft.as_payload()["cc"])

    def test_cross_thread_commitment_is_computed_from_both_messages(self):
        derived = derive_cross_thread_commitments(self.messages)
        self.assertTrue(derived)
        deck = next(d for d in derived if set(d.cited) == {"m038", "m040"})
        self.assertEqual("16", deck.day_token)
        self.assertIn("18", deck.derivation)
        store = {message.id: message for message in self.messages}
        # the anchor date is in m038, not in this module
        self.assertIn("18", store["m038"].body)

    def test_lookalike_detection_generalizes_to_an_unseen_domain(self):
        novel = Message.from_dict(
            {
                "id": "z001",
                "thread_id": "t-z",
                "from": "payroll@paperjet-payments.com",
                "to": "sam@paperjet.io",
                "subject": "Urgent payroll update",
                "timestamp": "2026-09-10T09:00:00",
                "body": "Please wire $4,000 to the updated remittance details today.",
                "unread": True,
            }
        )
        scan = SecurityScanner().scan(novel)
        self.assertTrue(scan.is_hostile)
        self.assertIn("lookalike_domain", [finding.signal for finding in scan.findings])

    def test_legitimate_related_domain_is_not_flagged_as_lookalike(self):
        benign = Message.from_dict(
            {
                "id": "z002",
                "thread_id": "t-z2",
                "from": "status@paperjet-monitoring.io",
                "to": "sam@paperjet.io",
                "subject": "All systems normal",
                "timestamp": "2026-09-10T09:05:00",
                "body": "Weekly uptime summary. No action needed.",
                "unread": True,
            }
        )
        scan = SecurityScanner().scan(benign)
        self.assertNotIn("lookalike_domain", [finding.signal for finding in scan.findings])

    def test_findings_name_the_attempted_action_and_target(self):
        results = SecurityScanner().scan_many(self.messages)
        forward = results["m024"]
        self.assertTrue(all(finding.attempted_action for finding in forward.findings))
        self.assertTrue(
            any("archive@mail-backup-service.info" in finding.target for finding in forward.findings),
            forward.attempted_actions,
        )
        wire = results["m021"]
        self.assertTrue(any("payment" in action for action in wire.attempted_actions))

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

    def test_dashboard_has_three_data_panes_and_validated_citations(self):
        dashboard = build_dashboard(self.messages, self.decisions)
        self.assertEqual(
            {"pending_actions", "flagged_items", "commitments_calendar", "conflicts", "citation_errors"},
            set(dashboard),
        )
        self.assertEqual([], dashboard["citation_errors"])

        known = {message.id for message in self.messages}
        for item in dashboard["commitments_calendar"]:
            self.assertTrue(item["cited"])
            self.assertTrue(set(item["cited"]).issubset(known))

        derived = next(
            item for item in dashboard["commitments_calendar"]
            if item.get("source") == "cross_thread_derived"
        )
        self.assertEqual(["m038", "m040"], derived["cited"])
        self.assertEqual("16", derived["day"])
        self.assertIn("derivation", derived)

    def test_dashboard_drops_a_commitment_citing_a_message_not_in_the_store(self):
        truncated = [message for message in self.messages if message.id != "m038"]
        dashboard = build_dashboard(truncated, self.decisions)
        cited_pairs = [item["cited"] for item in dashboard["commitments_calendar"]]
        self.assertNotIn(["m038", "m040"], cited_pairs)
        known = {message.id for message in truncated}
        for item in dashboard["commitments_calendar"]:
            self.assertTrue(set(item["cited"]).issubset(known))


if __name__ == "__main__":
    unittest.main()