# CAPABILITIES.md — InboxHero

**Student:** Pradeep Chandra Tunga, evernorth-aai-1189022
**Repository:** https://github.com/Pradeepchandra6903/indexhero

Run everything through one entry point:

```
python demo.py --cap R1            # one capability
python demo.py --cap R2 --msg m008 # grounded reply to a specific message
python demo.py --cap R3 --dry-run  # gate demo without side effects
python demo.py --all               # every capability, in the order below
python gen_message_map.py          # regenerate MESSAGE_MAP.md
```

---

## The system, in one paragraph

A single deterministic Python pipeline, **no framework**. Messages are loaded and
sorted chronologically; **every** message is first scanned by the SecurityScanner
(zero-trust: email content is untrusted data); cheap automated mail (receipts,
newsletters, notifications) is dispatched by rule; the rest goes through a curated
classify → retrieve → draft → gate sequence. A final pass builds the dashboard.
State that must outlive a run (preferences, the action/trace logs) is kept in small
JSON/JSONL files on disk.

## Design choices you were asked to state

- **Framework: none.** The work is a linear pipeline with one branch (rule path vs
  human/model path), so a crew or graph would be overhead. See README Q4.
- **Model: none (deterministic).** The "model path" is a curated, fully-auditable
  decision table instead of an opaque LLM call — reproducible, no API key, every
  reason inspectable. The seams are exactly where an LLM would drop in.
- **Retrieval: thread-walk primary, keyword fallback.** The inbox already carries
  structure via `thread_id`; walking the thread is cheaper and more precise than
  embeddings. Keyword search handles cross-thread lookups (e.g. m040's deadline
  depends on m038).
- **Reversible vs irreversible.** `send/delete/forward` are irreversible and gated;
  `draft/label/archive/defer/delegate` are reversible and run without a prompt.
  Deleting is irreversible because the mock store has no trash.
- **Where the gate sits.** Only `ApprovalGate.require_approval()` can authorize an
  irreversible action, and it asserts the action is irreversible. Nothing else can
  reach the outbox — this is also the Part 6 defence: a hostile message can shape a
  *draft* but cannot reach a send.
- **Escalation line.** Escalate on external sends and anything touching money, legal,
  or press; auto-archive internal FYIs. Trade-off: a wrongly-archived internal note
  is possible, in exchange for not asking the user to approve dozens of trivial items.

## Capabilities

| id | name | tier | one-line claim |
|----|------|------|----------------|
| R1 | Zero the inbox | B | every one of 100 messages gets one disposition + reason, none left |
| R2 | Grounded reply | B | drafts cite earlier evidence (m008 -> m003) without replaying credentials |
| R3 | Gate the irreversible | C | no send/delete/forward without approval or --dry-run |
| R4 | Persistent preference | C | a stated preference (m015) survives a restart, applied to m018/m048/m055 |
| R5 | Refuse embedded instructions | C | detects, refuses, flags, reports all 7 injections/phish |
| R6 | Dashboard | C | three panes, commitments cited, two conflicts + cross-thread deck surfaced |
| X1 | Smart Daily Digest | A | what needs me / what can wait / what was auto-archived |
| X2 | Long Thread Resolution | B | long threads → participants + milestones + open items, cited |
| X3 | Relationship Intelligence | B | per-correspondent profile + who's waiting longest on Sam |
| X4 | Autonomous Assistant Planning | B | prioritized day plan; every send still gated by R3 |
| X5 | Preference-Aware Negotiation Agent | C | preference-aware counter-proposal and grounded conflict notices; drafts, never sends |

Tiers present: **A** (X1), **B** (R1, R2, X2, X3, X4), **C** (R3, R4, R5, R6, X5).

The exact command, observable outcome and evidence for each is in
`capabilities.json` (the machine-readable version a marking script reads). Keep the
two in step.

## Artifacts produced by a run
- `decisions.json` — R1 full disposition table
- `trace.jsonl` — one event per decision (Part 9)
- `memory/preferences.json` — persisted preferences (R4)
- `pending_actions.json`, `approval_log.jsonl`, `outbox/` — approval gate (R3/Part 7)
- `dashboard.html`, `dashboard.json` — three-pane dashboard (R6/Part 8)
- `MESSAGE_MAP.md` — per-message map + Phase-1 counts

## Final Report
See `README.md` for the four required answers (what was deliberately not automated;
where untrusted text enters; the accountability model; and the framework analysis).
