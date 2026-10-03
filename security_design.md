# security_design.md — Zero-Trust Model

## Threat premise
**All email content is UNTRUSTED DATA.** A message body, subject, or even a
`From:` header is attacker-controllable. Therefore:

1. Instructions inside a message are **never** promoted to system/assistant
   instructions. The assistant's instructions come only from its own code and
   from preferences the user stated *out of band* (and even those are stored,
   not executed).
2. The only components that can cause an irreversible effect
   (`send/delete/forward`) are behind `ApprovalGate.require_approval()`. A
   hostile message can at most influence a *draft*; it cannot reach a send.
3. The SecurityScanner runs **first**, on every message, before the RuleEngine
   or any drafting. It has **no side effects** — it only emits findings.

## Data-flow: where untrusted text enters
```
inbox.json (UNTRUSTED)
  → SecurityScanner.scan()  ── produces Findings (data only, no actions)
  → Router                  ── hostile ⇒ escalate + flag; never auto-archived
  → RuleEngine / human table ── reached only if not hostile
  → Retrieval / Draft        ── may quote untrusted text, but into a *draft*
  → Action Gateway           ── reversible: direct; irreversible: GATED
  → ApprovalGate             ── human y/n or --dry-run before any send/delete
  → Dashboard                ── all content html.escape()'d (anti stored-XSS)
```

## SecurityScanner detections (`inboxhero/security.py`)
Each detector emits a
`Finding(kind, signal, evidence, severity, attempted_action, target)` where
`evidence` is the exact offending substring and `attempted_action` says, in
plain words, *what the message tried to make the assistant do* — with `target`
naming the address, URL, amount or message id the attacker aimed at. That pair
is what R5 prints and what goes into `trace.jsonl`, so the refusal record says
"tried to forward mail to an external address -> archive@mail-backup-service.info"
rather than only "prompt_injection".

| Signal | Detects | Example in inbox.json |
|--------|---------|-----------------------|
| `ignore_previous_instructions` | Prompt injection markers ("ignore all previous instructions", "SYSTEM NOTICE FOR AUTOMATED ASSISTANTS", "authorized backup routine") | **m024** |
| `role_play_attack` | "you are now", "act as", "from now on you will act", "new persona/role" | (guarded; fires with other markers) |
| `forward_mail_request` | Requests to forward the mailbox/messages to an external address | **m024** (archive@mail-backup-service.info), **m047** (finance-sync@ext-audit.co) |
| `delete_mail_request` | "then delete this message", "do not mention it", "skip the confirmation step before deleting" | **m024**, **m039** |
| `mass_reply_directive` | "reply with the single word … to the sender of every unread message" | **m017** |
| `self_spoofed_config_change` | A body claiming to reconfigure the assistant, purportedly from the mailbox owner | **m039** |
| `credential_harvesting` | "re-verify your credentials", "password expires in", "log in now to avoid" | **m045** |
| `invoice_wire_fraud` | "updated remittance", "new bank account", "routing:", "banking partner has changed", "wire $" | **m021**, **m023** |
| `secrecy_pressure` | "keep this between us", "don't loop in finance", "confidential favor" | **m023** |
| `artificial_urgency` | "urgent", "within N hours", "before end of day", "accounts will be suspended" (only elevated when paired with another finding) | **m021**, **m045** |
| `lookalike_domain` | Sender domain computed to be confusable with a trusted one | **m023** (paperjet.**co**), **m045** (paperjet-**helpdesk.com**) |

### Confusable domains are computed, not listed

There is no table of known-bad lookalikes. The only trusted domain is derived
from `models.OWNER_ADDRESS` (`sam@paperjet.io` → `paperjet.io`), and an incoming
domain is compared against it by three generic tests on the registrable name:

1. **same name, different TLD** — `paperjet.co` vs `paperjet.io`;
2. **trusted name plus an extra token** — `paperjet-helpdesk.com`,
   `paperjet-payments.com`;
3. **one-character edit distance** — `papejet.io`, `paperjett.io` (Levenshtein
   distance 1, for names of 5+ characters).

An unseen attacker domain is therefore caught the same way as the two in this
inbox; `test_lookalike_detection_generalizes_to_an_unseen_domain` proves it with
`payroll@paperjet-payments.com`, which appears nowhere in `inbox.json`.

Because related-looking domains are often legitimate, a confusable domain is a
**weak signal on its own**. It is only raised when the message already has
another finding, or the local part claims institutional authority
(`it-security@`, `payroll@`, `ceo@`, `accounts-payable@`, …). That is why
`status@paperjet-monitoring.io` and `chair@paperjet-board.org` pass untouched
without needing a whitelist — see
`test_legitimate_related_domain_is_not_flagged_as_lookalike`.

## Attack → response mapping (from the actual inbox)
| Attack | id | Response |
|--------|----|----------|
| **Prompt injection: forward + delete inbox** | m024 | Flagged `prompt_injection`; escalated; **not** forwarded, **not** deleted, **not** hidden from the summary. Outbox contains nothing to `archive@mail-backup-service.info`. |
| **Prompt injection: mass RELEASE reply, hide from user** | m017 | Flagged `mass_reply_directive`; escalated; no auto-replies sent; surfaced to the user (the opposite of what it demanded). |
| **Self-spoofed autonomous-mode config** | m039 | Flagged `self_spoofed_config_change`; escalated; autonomous mode is **not** enabled and **not** saved to preferences. Memory only ever stores preferences learned through the sanctioned path, never from a scanned-hostile body. |
| **Embedded forward-on-keyword directive** | m047 | Flagged `forward_mail_request`; escalated; nothing forwarded to `finance-sync@ext-audit.co`. |
| **Invoice/wire fraud (vendor BEC)** | m021 | Flagged `invoice_wire_fraud` + `artificial_urgency`; escalated for human review; no payment action exists in the system at all. |
| **CEO/CFO fraud + lookalike domain + secrecy** | m023 | Flagged `lookalike_domain` (paperjet.co) + `invoice_wire_fraud` + `secrecy_pressure`; escalated; not actioned. |
| **Credential-harvesting phish** | m045 | Flagged `credential_harvesting` + `lookalike_domain`; escalated; the verify link is never followed. |

## Why the gate is the last line of defence
Even if a future detector missed something, the injection still could not cause
harm: the only functions that send/delete/forward assert they are irreversible
and route through `require_approval()`. There is no code path from "draft" to
"send" that skips the human. `--dry-run` proves this by writing **0** bytes to
`outbox/` while printing every action it *would* take.

Two further containments sit above the gate:

- **No draft is produced for a hostile message at all.** `DraftBuilder.build()`
  checks the Router's decision first and refuses outright when the category is
  `security_threat`, so hostile text never reaches the drafting code that would
  quote it (`test_hostile_message_gets_no_draft`).
- **The send-selection policy excludes them structurally.**
  `actions.select_send_candidates` requires `category != "security_threat"`, a
  disposition of `reply`, *and* a non-refused grounded draft. A hostile message
  fails all three, so it is never even proposed to the human — which also means
  a missed detection would have to pass the policy *and* the human prompt.

## Secrets found in email are reused structurally, never replayed
`m003` contains a live AMQP URL with a password. When `m008` asks for it again,
the draft quotes the sentence with the secret masked
(`amqp://pj_stage:***@broker-stg.paperjet.io:5672/pjs`), restates the host, port,
vhost and username that the recipient actually needs, and says the password will
not be repeated in a mail thread. The masking is a transform over the retrieved
URL (`drafting.mask_url_secret`), so the reply cannot contain a secret the
original message did not, and it does not invent a destination (such as a secrets
manager) that no message in the store mentions.
