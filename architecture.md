# architecture.md — InboxHero

InboxHero is a single, deterministic Python pipeline (no agent framework). Email
content is treated as **untrusted data** at every stage; instructions found
inside a message body are never promoted to system/assistant instructions.

```
inbox.json
   │  load + chronological sort (models.load_inbox)
   ▼
┌─────────────────────────────────────────────────────────────────────┐
│ Router (router.Router)                                              │
│   1. SecurityScanner.scan()   ← runs on EVERY message, first        │
│   2. RuleEngine.classify()    ← cheap path (newsletter/receipt/...) │
│   3. Curated human table      ← deterministic "model path"          │
│   4. conservative defer        ← R1 safety net (never silent)        │
└─────────────────────────────────────────────────────────────────────┘
   │ Decision (one disposition + reason + provenance path per message)
   ├────────────► Trace Logger (trace.jsonl)   — every decision
   ├────────────► Memory Manager (memory/preferences.json)
   ├────────────► Retrieval Engine (thread-walk + keyword)
   ├────────────► Commitments/Conflicts ─► cross-thread date arithmetic
   ├────────────► Draft Builder (the only producer of reply text)
   │                 └► Action Gateway ─► Approval Manager (pending/approval log, outbox/)
   └────────────► Dashboard Generator (dashboard.html/json)
```

Capabilities R1–R6 and X1–X5 are thin entry points over these components; see
`capabilities.json`.

---

## 1. Router Layer — `inboxhero/router.py`
- **Responsibilities:** Assign exactly one disposition (`reply/archive/defer/delegate/escalate`) + a one-line reason to every message. Guarantees R1 "inbox zero" (0 undecided).
- **Inputs:** a `Message`.
- **Outputs:** a `Decision(message_id, thread_id, category, disposition, reason, flags, cited, commitment, requires_retrieval, ambiguous, path)`.
- **Provenance:** `path` records which branch decided the message — `security`, `rules`, `rules+curated_override`, `curated_judgment` or `fallback_defer`. `Decision.rule_handled` is derived from it, which is how R1 can report "58 handled by the RuleEngine, 65 resolved with no model call" as a computed number rather than a constant.
- **Failure handling:** if no rule or curated entry matches, it defaults to a reversible `defer` with an explicit reason rather than dropping the message. Order guarantees the SecurityScanner runs before any rule, so a hostile "newsletter" (m024) can never be silently archived.

## 2. Rule Engine — `inboxhero/rules.py`
- **Responsibilities:** Cheaply dispatch obviously-automated mail (newsletters, receipts, notifications) with no model call.
- **Inputs:** a `Message` (never reached if the scanner flagged it).
- **Outputs:** a `RuleVerdict(category, reason)` or `category=None` when it should defer to the human path.
- **Failure handling:** an `ACTION_REQUIRED_OVERRIDE` guard prevents auto-archiving automated-looking mail that actually demands a human reply (e.g. "reply CONFIRM", "reply RELEASE"). Ambiguity → returns `None` and lets the router decide.

## 3. Retrieval Engine — `inboxhero/retrieval.py`
- **Responsibilities:** Ground replies in specific earlier messages. Thread-walk is primary; keyword search is the cross-thread fallback; CitationBuilder records exactly which ids were used.
- **Inputs:** a target `Message` + the corpus.
- **Outputs:** ordered earlier messages / keyword hits / a `Citation` list.
- **Failure handling:** if thread-walk finds no grounding, it degrades to keyword search; if that also fails, the draft says so explicitly rather than fabricating content (no hallucinated grounding).

## 4. Memory Manager — `inboxhero/memory.py`
- **Responsibilities:** Extract standing preferences from message text (`PreferenceLearner`) and persist them to `memory/preferences.json` across process restarts (R4).
- **Inputs:** the message corpus for learning; `Preference(key, value, source_message_id, stated, created_at)` for storage.
- **Outputs:** JSON on disk; `get/has/all/find(prefix)` lookups on later runs.
- **How learning works:** two sentence patterns (a CC request, a "no meetings before <time>" cutoff) produce keys `cc_rule:<domain>` and `earliest_meeting_time`. A scope named in prose ("our lawyers at Hartwell & Cho") is resolved to a domain by matching it against the inbox's *own* sender domains, so no firm→domain mapping is hardcoded. `created_at` is the source message's timestamp, which keeps the file byte-identical between runs.
- **Failure handling:** creates the directory if missing; a missing file simply means "no preferences yet"; a corrupt file raises rather than silently resetting. Human-readable JSON on purpose (no hidden vector state).

## 4b. Draft Builder — `inboxhero/drafting.py`
- **Responsibilities:** The single producer of reply text. Retrieve first, then assemble a draft whose every factual sentence is quoted from a cited message; attach preference-driven CCs; refuse when nothing supports an answer.
- **Inputs:** messages, the Router's decisions, the preference memory.
- **Outputs:** a `Draft(to, cc, subject, body, cited, evidence, kind, refused, refusal_reason)` and `as_payload()`, which is literally what the gate records and what a send writes.
- **Grounding rules:** cited ids are validated against the mail store before the draft is returned; a secret found in email is reused structurally with the secret masked (`mask_url_secret`) rather than replayed; a message the Router flagged as `security_threat` is refused before any of its text is read for drafting.
- **Failure handling:** six grounded variants are tried in order (credential request, derived deadline, conflict, preference violation, thread context, preference CC). If none applies, it returns a refusal with the reason instead of inventing content — which is why `m042` gets no draft and is left to a human.

## 5. Security Layer — `inboxhero/security.py`
- **Responsibilities:** Detect prompt injection, role-play, "ignore previous instructions", forward-mail / delete-mail directives, credential harvesting, wire fraud, social engineering, and lookalike-domain spoofing. See `security_design.md`.
- **Inputs:** a `Message` (or the corpus, via `scan_many`, to catch instructions split across a thread).
- **Outputs:** a `ScanResult` with `Finding(kind, signal, evidence, severity, attempted_action, target)` items. **No side effects** — it only produces data.
- **Attempted actions:** every signal maps to a plain-words description of what the message tried to achieve plus the target it named (external address, login URL, amount, message id), so a refusal can be reported as an attempt rather than a category.
- **Confusable domains are computed:** the single trusted domain comes from `models.OWNER_ADDRESS`, and candidate domains are tested against it by same-name/different-TLD, trusted-name-plus-token, and Levenshtein-distance-1 comparisons. No list of known lookalikes exists, so unseen ones are caught identically.
- **Failure handling:** conservative — a weak signal (urgency alone, or a confusable domain alone) is only raised when paired with another finding or an authority-claiming local part, which is what keeps `status@paperjet-monitoring.io` and `chair@paperjet-board.org` clean without a whitelist.

## 6. Action Gateway — `inboxhero/actions.py`
- **Responsibilities:** Separate reversible (`draft/label/archive/defer/delegate`) from irreversible (`send/delete/forward`) actions. Reversible actions run directly; irreversible ones MUST call `ApprovalGate.require_approval()`.
- **Inputs:** action name, message id, reason, payload.
- **Outputs:** for reversible, an execution record; for irreversible, a gated `PendingAction(proposed_action, message_id, reason, payload, recipient, cc, cited, decision_timestamp, human_response, final_status)`.
- **Selection policy:** `select_send_candidates(decisions, drafts)` decides *what is worth asking about* — not a list of ids, but the conjunction "not a security threat ∧ disposition is `reply` ∧ a non-refused grounded draft exists". `unsendable_replies` reports what the policy deliberately withholds and why. This is where the escalation line is drawn: 8 real prompts instead of 40 rubber-stamps, with escalations routed to a human author rather than a human approver.
- **Failure handling:** `assert action in IRREVERSIBLE` inside `require_approval` — no other code path can reach the outbox. Outbox writes are idempotent (`open(..., "x")`, so a re-approval reports `approved_already_written` rather than duplicating). `--dry-run` writes zero bytes, and `writes_by_this_gate()` reports per-run writes separately from files left by earlier runs.

## 7. Dashboard Generator — `inboxhero/dashboard.py`
- **Responsibilities:** Render three panes (Pending Actions, Flagged Items, Commitments Calendar) to `dashboard.html`/`dashboard.json`; highlight conflicts; show source message ids; compute cross-thread commitments.
- **Inputs:** messages + decisions.
- **Outputs:** HTML + JSON. Each commitment carries `cited` and `source` (`single_message` or `cross_thread_derived`); derived entries also carry `derivation`, the arithmetic in words.
- **Cross-thread derivation:** `commitments.derive_cross_thread_commitments` matches a relative deadline ("two days before the board review") in one message, finds the message that dates that event, and subtracts — recording "the 18 comes from m038; 18 − 2 = 16". Nothing about the board deck is written into the dashboard.
- **Citation validation:** every cited id is checked against the loaded store (`commitments.validate_citations`). A commitment citing a message the inbox does not contain is dropped into `citation_errors` rather than rendered; `test_dashboard_drops_a_commitment_citing_a_message_not_in_the_store` removes `m038` and asserts the derived entry disappears.
- **Failure handling:** all message content is HTML-escaped (`html.escape`) to prevent stored-XSS from hostile email bodies leaking into the dashboard.

## 8. Approval Manager — `inboxhero/actions.py` (`ApprovalGate`)
- **Responsibilities:** Prompt the human (y/n) per irreversible action, record `proposed_action, message_id, reason, human_response, final_status` to `approval_log.jsonl`, and snapshot `pending_actions.json`.
- **Inputs:** proposed irreversible actions.
- **Outputs:** append-only audit log + pending snapshot; on approval, a file in `outbox/`.
- **Failure handling:** non-interactive runs use `--auto-deny` (defaults to `denied`), so batch/CI runs never accidentally send.

## 9. Capability Runner — `inboxhero/capabilities/` + `demo.py`
- **Responsibilities:** Register and dispatch each capability (R1–R6, X1–X5) over a shared context. `--all` runs them in rubric order.
- **Inputs:** CLI args + shared `ctx`.
- **Outputs:** per-capability stdout + artifacts + a result dict.
- **Failure handling:** unknown `--msg` returns a typed error; each capability is independently executable.

## 10. Trace Logger — `inboxhero/trace.py`
- **Responsibilities:** Append one JSONL event per decision with `timestamp, message_id, component, reasoning_summary, evidence_messages, final_action` (+ optional `cap`).
- **Inputs:** structured event fields.
- **Outputs:** `trace.jsonl` (append-only).
- **Failure handling:** `--fresh-trace` truncates for a clean reproducible run; otherwise appends so multi-run history (e.g. R4 run 1 + run 2) is preserved.

---

## Design decisions & trade-offs
- **No framework.** The workload is a linear pipeline with one branch (rule path vs human/model path). A crew/graph would add overhead without buying anything; see README Q4.
- **Deterministic "model path".** Instead of a nondeterministic LLM call, the human-judgment layer is a curated, fully-auditable decision table. Trade-off: it is inbox-specific, but it is 100% reproducible, needs no API key, and every reason is inspectable by a marker. The seams (Router → Retrieval → Action → Gate) are exactly where an LLM would drop in.
- **Reversible vs irreversible.** `send/delete/forward` are irreversible and gated; everything else is reversible and automatic. Deleting is treated as irreversible because the mock store has no trash.
- **Escalation policy.** Escalate on external sends and anything touching money/legal/press; auto-archive internal FYIs. Trade-off: a wrongly-archived internal note is possible, in exchange for not asking the user to approve dozens of trivial items.
