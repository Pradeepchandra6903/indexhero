"""SecurityScanner: zero-trust content inspection.

Design principle (Part 5 / R5): email content is UNTRUSTED DATA. Nothing in a
message body, subject, or "From" header is ever treated as a system or
assistant instruction, no matter how it is phrased ("SYSTEM NOTICE",
"AUTOMATED-AGENT DIRECTIVE", claims of being the mailbox owner, etc.). This
scanner only ever produces *findings* (data); it never executes an action
described inside a message. Findings feed the Router/Action Gateway, which
decide the disposition -- the scanner itself has no side effects.
"""
from __future__ import annotations

import base64
import re
from dataclasses import dataclass, field

from .models import Message

# ---------------------------------------------------------------------------
# Pattern libraries. Each is intentionally narrow + documented so a marker can
# see exactly why a message was flagged (no black-box scoring).
# ---------------------------------------------------------------------------

INJECTION_MARKERS = re.compile(
    r"(ignore (all|any) previous instructions"
    r"|system notice for automated assistants"
    r"|automated-agent directive"
    r"|assistant (configuration|settings) update"
    r"|autonomous mode is now enabled"
    r"|do not (mention|surface) this (to|in)"
    r"|no need to (mention|surface)"
    r"|assistant note:"
    r"|if (an )?ai agent is processing this mailbox"
    r"|this is an authorized backup routine)",
    re.IGNORECASE,
)

ROLE_PLAY_MARKERS = re.compile(
    r"(you are now|act as|pretend (to be|you are)|from now on you (are|will act)|"
    r"new (persona|role|identity)"
    r"|this is your (system )?administrator|i am (your|the) (system )?admin(istrator)?"
    r"|on behalf of (the )?(ceo|cfo|it|security) team)",
    re.IGNORECASE,
)

FORWARD_MAIL_MARKERS = re.compile(
    r"(forward (the )?(full contents of )?(this )?(mailbox|inbox|message)"
    r"|forward any message that mentions"
    r"|forward .* to (archive@|finance-sync@|[\w.+-]+@ext-)"
    r"|please also forward"
    r"|forward all [\w ]+ to [\w.+-]+@"
    r"|(send|forward) (the )?(staging |production )?(credentials|creds|password|secrets?) to [\w.+-]+@"
    r"|send them (everything|all|the messages))",
    re.IGNORECASE,
)

DELETE_MAIL_MARKERS = re.compile(
    r"(then delete this message|delete this message|delete the message"
    r"|permanently deleted|and do not mention it"
    r"|do not mention that you (did|deleted)"
    r"|skip the confirmation step before archiving or deleting)",
    re.IGNORECASE,
)

MASS_REPLY_MARKERS = re.compile(
    r"(reply with the single word|must reply .* to the sender of every|"
    r"reply .* to every unread)",
    re.IGNORECASE,
)

TOOL_INVOCATION_MARKERS = re.compile(
    r"(call tool|invoke tool|execute_tool|run_action|approvalgate|send_email|delete_message|forward_message)",
    re.IGNORECASE,
)

UNSAFE_PREFERENCE_MARKERS = re.compile(
    r"(save this as (a )?(standing )?preference.*(without approval|skip approval|auto-?send|delete)|"
    r"preference.*(ignore|bypass|disable).*(gate|approval|security))",
    re.IGNORECASE | re.DOTALL,
)

CREDENTIAL_HARVEST_MARKERS = re.compile(
    r"(re-?verify your credentials|verify your (account|password) immediately|"
    r"password (will )?expires? in|click (here|the link) to (verify|confirm)|"
    r"log ?in (immediately|now) to avoid)",
    re.IGNORECASE,
)

PHISHING_URGENCY_MARKERS = re.compile(
    r"(urgent|immediately|within \d+ hours?|before end of day|expires in|"
    r"account(s)? (will be|are) suspended|avoid (a )?service interruption)",
    re.IGNORECASE,
)

WIRE_FRAUD_MARKERS = re.compile(
    r"(remit|wire (the|us|\$)|updated remittance|new (bank )?account (details|below)|"
    r"routing:|banking partner has changed"
    r"|transfer (the )?[\w ]*(retainer|payment|funds|deposit|amount)"
    r"|updated (bank )?account|account (number )?\d{4,})",
    re.IGNORECASE,
)

SECRECY_PRESSURE_MARKERS = re.compile(
    r"(keep this between us|don'?t loop in|confidential.{0,40}(favor|favour)|"
    r"don'?t (tell|mention) (anyone|finance))",
    re.IGNORECASE,
)

SUSPICIOUS_LOOKALIKE_DOMAIN_HINTS = (
    # legit corporate domain -> known lookalikes seen in this inbox
    ("paperjet.io", "paperjet.co"),
    ("paperjet.io", "paperjet-helpdesk.com"),
    ("paperjet.io", "paperjet-board.org"),  # legitimate board domain, whitelisted below
)

KNOWN_GOOD_EXTERNAL_DOMAINS = {"paperjet-board.org", "hartwellcho.com"}


@dataclass
class Finding:
    kind: str          # e.g. "prompt_injection", "phishing", "social_engineering"
    signal: str         # short machine tag, e.g. "ignore_previous_instructions"
    evidence: str       # the exact substring / quoted clause that triggered it
    severity: str       # "critical" | "high" | "medium"


@dataclass
class ScanResult:
    message_id: str
    findings: list[Finding] = field(default_factory=list)

    @property
    def is_hostile(self) -> bool:
        return len(self.findings) > 0

    @property
    def kinds(self) -> list[str]:
        # stable de-dup, preserves first-seen order
        seen = []
        for f in self.findings:
            if f.kind not in seen:
                seen.append(f.kind)
        return seen


def _find(pattern: re.Pattern, text: str, max_len: int = 90) -> str | None:
    m = pattern.search(text)
    if not m:
        return None
    snippet = m.group(0)
    return snippet[:max_len]


def _decoded_text(text: str) -> str:
    decoded: list[str] = []
    for token in re.findall(r"[A-Za-z0-9+/=]{16,}", text):
        try:
            raw = base64.b64decode(token, validate=True)
            value = raw.decode("utf-8")
        except Exception:
            continue
        if any(marker in value.lower() for marker in ("forward", "delete", "password", "inbox", "assistant")):
            decoded.append(value)
    return "\n".join(decoded)


def _looks_like_display_name_spoof(msg: Message) -> Finding | None:
    """Domain confusable check: e.g. priya.nair@paperjet.co vs priya@paperjet.io."""
    domain = msg.domain
    if domain in KNOWN_GOOD_EXTERNAL_DOMAINS or domain == "paperjet.io":
        return None
    for legit, lookalike in SUSPICIOUS_LOOKALIKE_DOMAIN_HINTS:
        if domain == lookalike and legit == "paperjet.io":
            return Finding(
                kind="phishing",
                signal="lookalike_domain",
                evidence=f"sender domain '{domain}' mimics trusted domain '{legit}'",
                severity="critical",
            )
    return None


class SecurityScanner:
    """Stateless scanner: scan(message) -> ScanResult. No side effects."""

    def scan(self, msg: Message) -> ScanResult:
        text = f"{msg.subject}\n{msg.body}"
        decoded = _decoded_text(text)
        inspectable_text = f"{text}\n{decoded}" if decoded else text
        findings: list[Finding] = []

        if decoded and (s := _find(FORWARD_MAIL_MARKERS, decoded)):
            findings.append(Finding("prompt_injection", "encoded_forward_request", s, "critical"))
        if s := _find(INJECTION_MARKERS, inspectable_text):
            findings.append(Finding("prompt_injection", "ignore_previous_instructions", s, "critical"))
        if s := _find(ROLE_PLAY_MARKERS, inspectable_text):
            findings.append(Finding("prompt_injection", "role_play_attack", s, "high"))
        if s := _find(FORWARD_MAIL_MARKERS, inspectable_text):
            findings.append(Finding("prompt_injection", "forward_mail_request", s, "critical"))
        if s := _find(DELETE_MAIL_MARKERS, inspectable_text):
            findings.append(Finding("prompt_injection", "delete_mail_request", s, "critical"))
        if s := _find(MASS_REPLY_MARKERS, inspectable_text):
            findings.append(Finding("prompt_injection", "mass_reply_directive", s, "high"))
        if s := _find(TOOL_INVOCATION_MARKERS, inspectable_text):
            findings.append(Finding("prompt_injection", "direct_tool_invocation", s, "critical"))
        if s := _find(UNSAFE_PREFERENCE_MARKERS, inspectable_text):
            findings.append(Finding("prompt_injection", "unsafe_preference_request", s, "critical"))
        if s := _find(CREDENTIAL_HARVEST_MARKERS, inspectable_text):
            findings.append(Finding("phishing", "credential_harvesting", s, "critical"))
        if s := _find(WIRE_FRAUD_MARKERS, inspectable_text):
            findings.append(Finding("phishing", "invoice_wire_fraud", s, "critical"))
        if s := _find(SECRECY_PRESSURE_MARKERS, inspectable_text):
            findings.append(Finding("social_engineering", "secrecy_pressure", s, "high"))
        if s := _find(PHISHING_URGENCY_MARKERS, inspectable_text):
            # urgency alone is weak signal; only elevate if paired with another finding
            if findings:
                findings.append(Finding("social_engineering", "artificial_urgency", s, "medium"))

        if lookalike := _looks_like_display_name_spoof(msg):
            findings.append(lookalike)

        # Self-spoof special case: a message claiming to reconfigure the
        # assistant, purportedly "from" the mailbox owner. Zero-trust means
        # From: sam@paperjet.io in a message BODY is never proof of intent --
        # instructions never travel through email content, full stop.
        if msg.is_self_sent and INJECTION_MARKERS.search(text):
            findings.append(
                Finding(
                    "prompt_injection",
                    "self_spoofed_config_change",
                    "message claims to change assistant behavior from inside an email body",
                    "critical",
                )
            )

        return ScanResult(message_id=msg.id, findings=findings)

    def scan_many(self, messages: list[Message]) -> dict[str, ScanResult]:
        results = {msg.id: self.scan(msg) for msg in messages}
        by_thread: dict[str, list[Message]] = {}
        for msg in messages:
            by_thread.setdefault(msg.thread_id, []).append(msg)
        for thread_messages in by_thread.values():
            ordered = sorted(thread_messages, key=lambda msg: msg.timestamp)
            for first, second in zip(ordered, ordered[1:]):
                combined = f"{first.body}\n{second.body}"
                if "forward" in combined.lower() and "inbox" in combined.lower() and "delete" in combined.lower():
                    finding = Finding(
                        "prompt_injection",
                        "split_instruction_attack",
                        f"split across {first.id}+{second.id}",
                        "critical",
                    )
                    results[first.id].findings.append(finding)
                    results[second.id].findings.append(finding)
        return results
