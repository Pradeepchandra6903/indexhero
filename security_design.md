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
Each detector emits a `Finding(kind, signal, evidence, severity)` where
`evidence` is the exact offending substring, so every flag is explainable.

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
| `lookalike_domain` | Sender domain confusable with a trusted one | **m023** (paperjet.**co**), **m045** (paperjet-**helpdesk.com**) |

Legitimate external domains (`hartwellcho.com` legal counsel, `paperjet-board.org`
board) are whitelisted so real signature/board mail is not false-flagged.

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
