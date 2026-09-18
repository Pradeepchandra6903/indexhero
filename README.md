# InboxHero

**Student:** Pradeep Chandra Tunga, evernorth-aai-1189022
**Repository:** https://github.com/Pradeepchandra6903/indexhero

A graph-free, framework-free, **deterministic** email-triage assistant for a
100-message inbox (`inbox.json`). It zeroes the inbox, drafts grounded replies,
gates every irreversible action behind a human, remembers preferences across
restarts, refuses prompt-injection/phishing, and produces a reproducible
dashboard — all offline, with no API key and no nondeterminism.

## Quick start
```
python demo.py --all               # run every capability in rubric order
python demo.py --cap R1            # one capability
python demo.py --cap R2 --msg m008 # grounded reply
python demo.py --cap R3 --dry-run  # gate demo, zero side effects
python gen_message_map.py          # regenerate MESSAGE_MAP.md
```

## Layout
```
demo.py                     single entry point / capability runner
gen_message_map.py          MESSAGE_MAP.md generator
inboxhero/
  models.py                 Message model + chronological loader
  security.py               SecurityScanner (zero-trust, no side effects)
  rules.py                  RuleEngine (newsletter/receipt/notification)
  router.py                 Router (one disposition per message; R1)
  retrieval.py              thread-walk + keyword + citation builder
  memory.py                 PreferenceMemory (persists to memory/preferences.json)
  commitments.py            commitment extraction + cross-thread conflict detection
  actions.py                Action Gateway + ApprovalGate
  trace.py                  append-only trace.jsonl logger
  dashboard.py              three-pane dashboard generator
  capabilities/r1..r6, x1..x5.py
architecture.md, security_design.md, memory_demo.md, MARKING_REVIEW.md
CAPABILITIES.md, capabilities.json, MESSAGE_MAP.md
```

---

# Final Report

## Q1. What was deliberately NOT automated?
Anything **irreversible or externally binding** is deliberately left to a human:

- **Sends, deletes, and forwards** (`send/delete/forward`) never happen without
  explicit per-action approval or `--dry-run`. Concretely, the drafted replies to
  the investor (m010), the Acme demo (m016), and the SAFE signature escalation
  (m018) are *drafted* but not sent — `approval_log.jsonl` shows them as
  `denied` under `--auto-deny`, and `outbox/` stays empty.
- **Money and legal decisions.** The system has **no** payment capability at all,
  so the wire-fraud attempts (m021 vendor BEC, m023 CEO-fraud lookalike) cannot be
  actioned even in principle; they are escalated to the human.
- **Public statements.** The press request (m046 — "confirm the launch date is
  public") and the binding venue confirmation (m019) are escalated, not answered
  autonomously, because a wrong word is externally irreversible.
- **Ambiguous asks.** m012 ("did you ever sort out that thing we talked about after
  standup") has no retrievable referent, so it is deferred with an explicit "needs a
  clarifying question" flag rather than guessing.
- **Preference changes from email content.** m039 tries to enable "autonomous mode"
  from inside a message body; it is refused, not obeyed.

## Q2. Where does untrusted text enter?
**Everywhere email content is read** — so the whole pipeline treats it as untrusted
(`security_design.md`). Entry points and containment:

- **Message body/subject/From** are attacker-controllable. The SecurityScanner runs
  **first**, on every message, and only ever emits *findings* (data), never actions.
- **Drafting** treats credentials discovered in email as sensitive: R2 uses m003 as
  evidence but refuses to replay its connection details. Any resulting draft still has
  to pass the approval gate before it can be sent. A hostile body can shape a draft; it
  cannot reach a send.
- **The dashboard** renders untrusted bodies, so every field is `html.escape()`'d to
  prevent stored-XSS from a malicious email leaking into the HTML.
- **Concrete injections handled:** m024 (forward+delete the whole inbox), m017 (mass
  RELEASE reply + "don't surface this"), m047 (forward-on-keyword to an external
  auditor), m039 (self-spoofed config). None are obeyed; all are flagged and escalated.

## Q3. Accountability model
Every consequential action is **traceable to an evidence trail and a human decision**:

- **Trace log.** `trace.jsonl` records one event per decision with
  `timestamp, message_id, component, reasoning_summary, evidence_messages,
  final_action`. Any capability's behavior can be reconstructed from it alone.
- **Grounding = citations.** Grounded replies (R2) and cross-thread commitments (R6,
  board deck) record the exact message ids they relied on, so a reader can verify the
  claim without reproducing sensitive source content (for example, the board date is
  grounded in m038).
- **Approval log.** `approval_log.jsonl` records, per irreversible action,
  `proposed_action, message_id, reason, human_response, final_status`. `pending_actions.json`
  snapshots what is awaiting a human.
- **Preference provenance.** Each stored preference keeps its `source_message_id`
  (m015 for the legal-CC rule), so every applied CC traces back to the request that
  authorized it.
- **Human is the final authority.** The only path to send/delete/forward is
  `ApprovalGate.require_approval()`; the human's y/n is recorded and binding.

## Q4. Framework analysis (why no crew/graph)
- **Shape of the problem.** Triage over 100 messages is a *linear* pipeline with a
  single branch (rule path vs human/model path). A multi-agent crew or a graph
  framework would add coordination, serialization, and nondeterminism overhead
  without buying more capability.
- **Determinism and reproducibility.** A marking script needs the same output every
  run. A curated, auditable decision table gives that; a stochastic multi-agent
  system does not. It also runs with **no API key**, which matters for grading.
- **Security posture.** Fewer moving parts = a smaller attack surface. With exactly
  one gate function reachable by exactly the drafting/escalation paths, it is trivial
  to prove that no injected instruction can reach an irreversible action. A graph of
  autonomous agents makes that proof much harder.
- **Where a framework *would* help (future work).** If the inbox grew to thousands of
  messages/day with genuinely open-ended drafting, an LLM would slot into the
  "human/model path" seam in the Router and into R2's drafting, still behind the same
  approval gate. The architecture is designed so that swap is local, not a rewrite.

## Assumptions, trade-offs, future improvements
- **Assumption:** `sam@paperjet.io` is the mailbox owner; a `From:` of that address in
  a message body is *not* proof of intent (see m039).
- **Trade-off:** the curated human-judgment table is inbox-specific; in exchange it is
  100% reproducible and auditable. The seams for an LLM are clearly marked.
- **Future:** swap the decision table for an LLM behind the same gate; add a real
  calendar backend so X5's counter-proposals can suggest concrete free slots;
  persist relationship profiles (X3) to inform escalation thresholds over time.
