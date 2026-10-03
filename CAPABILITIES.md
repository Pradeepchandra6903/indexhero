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

There is exactly one place reply text is produced (`inboxhero/drafting.py`), so the
draft R2 prints, the payload R3 asks you to approve, the CC R4 applies and the
counter-proposal X5 negotiates with are all the same artifact rather than three
descriptions of one.

## Input data-format assumptions

Everything the code assumes about `inbox.json`, stated so a marker can swap the file
and know what will happen:

- **Shape.** A JSON array of objects with exactly the keys `id`, `thread_id`, `from`,
  `to`, `subject`, `body`, `timestamp`. A missing key, malformed JSON, a duplicate
  `id` or an unparseable timestamp exits with a one-line message, not a traceback
  (`models.load_inbox`).
- **`id`** is unique and is the only citation handle. Every cited id is validated
  against the loaded store before a draft or a dashboard pane is produced, so a
  citation can never point at a message that is not in the file.
- **`timestamp`** is naive ISO-8601 `YYYY-MM-DDTHH:MM:SS`, no timezone and no offset.
  Messages are sorted by `(timestamp, id)` at load, which makes ties deterministic.
- **"Today" is the newest timestamp in the file**, never the wall clock, so every run
  is reproducible and X3's waiting times are stable.
- **`thread_id`** groups a conversation and is the primary retrieval key. A message
  with no earlier sibling has no thread history, and retrieval refuses rather than
  guessing — that is why `m042` gets no draft.
- **`from` / `to`** are bare addresses (`priya@paperjet.io`), not `Name <addr>`.
  Relationship and role inference reads the domain and local part.
- **Owner.** The mailbox owner is `sam@paperjet.io` (`models.OWNER_ADDRESS`); every
  message is addressed to that account. Trusted domains are derived from it, so
  confusable-domain detection has no hardcoded list of lookalikes.
- **`body`** is plain text, is treated as untrusted data, and is scanned before any
  other component reads it. No HTML parsing, no attachments, no inline images.
- **Scale.** Single-process, in-memory, no index. Linear scans are fine at the
  assignment's 100 messages; past a few thousand the keyword fallback would need an
  inverted index.

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
| R1 | Zero the inbox | B | every one of 100 messages gets one disposition + reason + provenance path; 58 handled by rules, 65 with no model call at all |
| R2 | Grounded reply | B | drafts quote the sentence they rely on (m008 -> m003), mask the secret instead of replaying it, and refuse when retrieval supports nothing (m042) |
| R3 | Gate the irreversible | C | no send/delete/forward without approval or --dry-run; the 8 proposals come from a policy over Router output, and each payload carries recipient, body and citations |
| R4 | Persistent preference | C | preferences are extracted from message text (m015, m041), survive a restart, and the CC lands on the real drafts for m018/m048/m055 |
| R5 | Refuse embedded instructions | C | names the attempted action and target for every finding on all 7 hostile messages, refuses, leaves them in place |
| R6 | Dashboard | C | three panes, every commitment cites validated ids, two conflicts, and the cross-thread deadline computed (18 − 2 = 16) |
| X1 | Smart Daily Digest | A | what needs me / what can wait / what was auto-archived |
| X2 | Long Thread Resolution | B | long threads → participants + milestones + open items, cited |
| X3 | Relationship Intelligence | B | per-correspondent profile ranked by the age of each sender's oldest unanswered message, in days |
| X4 | Autonomous Assistant Planning | A | prioritized day plan that asks R3's own policy what it would do with each row; X4 performs no action |
| X5 | Preference-Aware Negotiation Agent | C | scans every commitment against the stored cutoff, drafts only where someone is waiting, hands all drafts to R3's gate |

Tiers present: **A** (X1, X4), **B** (R1, R2, X2, X3), **C** (R3, R4, R5, R6, X5).

X4 is claimed at tier A, not B: it is a read-only view over the Router's decisions and
the commitment dates. It calls R3's selection policy to label each row, but it takes no
action of its own, so it does not earn a higher tier.

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
