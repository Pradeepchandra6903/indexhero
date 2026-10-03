"""SecurityScanner: zero-trust content inspection.

Design principle (Part 6 / R5): email content is UNTRUSTED DATA. Nothing in a
message body, subject, or "From" header is ever treated as a system or
assistant instruction, no matter how it is phrased ("SYSTEM NOTICE",
"AUTOMATED-AGENT DIRECTIVE", claims of being the mailbox owner, etc.). This
scanner only ever produces *findings* (data); it never executes an action
described inside a message. Findings feed the Router/Action Gateway, which
decide the disposition -- the scanner itself has no side effects.

Each finding carries the *attempted action* (what the message tried to make an
assistant do) and, where one can be extracted from the text, the attacker's
target (destination address, login URL, account/amount). That is what gets
logged and reported, rather than just a threat category.
"""
from __future__ import annotations

import base64
import re
from dataclasses import dataclass, field

from .models import OWNER_ADDRESS, Message

# ---------------------------------------------------------------------------
# Pattern libraries. Each is intentionally narrow + documented so a marker can
# see exactly why a message was flagged (no black-box scoring).
# ---------------------------------------------------------------------------

INJECTION_MARKERS = re.compile(
    r"(ignore (all|any) previous instructions"
    r"|ignore previous instructions"
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

# Domains the mailbox owner legitimately belongs to. Confusable-domain checks
# are computed against this set at runtime -- there is no per-attack list of
# known-bad domains, so a lookalike this inbox has never seen is still caught.
TRUSTED_DOMAINS = {OWNER_ADDRESS.split("@")[-1].lower()}

# Local parts that claim institutional authority. A confusable domain paired
# with one of these is treated as impersonation on its own.
AUTHORITY_LOCAL_PARTS = re.compile(
    r"^(it[-._]?)?(security|support|helpdesk|admin|administrator|sysadmin|root|"
    r"ceo|cfo|payroll|accounts[-._]?payable|billing[-._]?team)",
    re.IGNORECASE,
)

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
URL_RE = re.compile(r"\bhttps?://[^\s]+", re.IGNORECASE)
AMOUNT_RE = re.compile(r"\$[\d,]+(?:\.\d{2})?")
ACCOUNT_RE = re.compile(r"(?:account|routing)\s*:?\s*(\d{4,})", re.IGNORECASE)

# signal -> (what the message tried to make the assistant do, target extractor)
ATTEMPTED_ACTIONS: dict[str, str] = {
    "encoded_forward_request": "forward mailbox contents to an external address (base64-obfuscated)",
    "ignore_previous_instructions": "override the assistant's own instructions from message text",
    "role_play_attack": "impersonate an authority so the assistant grants it privileges",
    "forward_mail_request": "forward mail to an external address",
    "delete_mail_request": "delete mail and conceal the deletion",
    "mass_reply_directive": "auto-reply to every unread message in the mailbox",
    "direct_tool_invocation": "invoke an action tool directly from message text",
    "unsafe_preference_request": "store a standing preference that disables the approval gate",
    "credential_harvesting": "collect the owner's account credentials via an external page",
    "invoice_wire_fraud": "redirect a payment to an attacker-controlled bank account",
    "secrecy_pressure": "suppress disclosure of the request to the owner or finance",
    "artificial_urgency": "pressure an immediate decision before a human can check it",
    "lookalike_domain": "impersonate a trusted domain in the From header",
    "self_spoofed_config_change": "reconfigure the assistant from inside an email body",
    "split_instruction_attack": "split one forward-and-delete instruction across two messages",
}


@dataclass
class Finding:
    kind: str          # e.g. "prompt_injection", "phishing", "social_engineering"
    signal: str         # short machine tag, e.g. "ignore_previous_instructions"
    evidence: str       # the exact substring / quoted clause that triggered it
    severity: str       # "critical" | "high" | "medium"
    attempted_action: str = ""   # what the message tried to make the assistant do
    target: str = ""             # destination/URL/account extracted from the text

    def describe(self) -> str:
        """One line a human can read: action attempted, plus target if known."""
        action = self.attempted_action or self.signal
        return f"{action} -> {self.target}" if self.target else action


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

    @property
    def attempted_actions(self) -> list[str]:
        """Every distinct attempted action, in detection order (for R5/trace)."""
        seen = []
        for f in self.findings:
            described = f.describe()
            if described not in seen:
                seen.append(described)
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


def _external_recipient(text: str, own_domain: str) -> str:
    """First address in the text that is not the mailbox owner's own domain."""
    for address in EMAIL_RE.findall(text):
        if not address.lower().endswith(f"@{own_domain}") and address.lower() != OWNER_ADDRESS:
            return address
    return ""


def _payment_target(text: str) -> str:
    amount = AMOUNT_RE.search(text)
    account = ACCOUNT_RE.search(text)
    parts = []
    if amount:
        parts.append(amount.group(0))
    if account:
        parts.append(f"account ending {account.group(1)[-4:]}")
    return " to ".join(parts)


def _levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i]
        for j, cb in enumerate(b, start=1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


def _registrable_name(domain: str) -> str:
    """'paperjet-helpdesk.com' -> 'paperjet-helpdesk'; 'a.paperjet.co' -> 'paperjet'."""
    labels = [label for label in domain.lower().split(".") if label]
    return labels[-2] if len(labels) >= 2 else (labels[0] if labels else "")


def _confusable_reason(domain: str, trusted: str) -> str | None:
    """Generic confusable-domain test against a trusted domain.

    No list of known-bad domains is consulted: the comparison is computed from
    the trusted domain itself, so an unseen lookalike is detected the same way.
    """
    domain = domain.lower()
    trusted = trusted.lower()
    if not domain or domain == trusted:
        return None

    name = _registrable_name(domain)
    trusted_name = _registrable_name(trusted)
    if not name or not trusted_name:
        return None

    if name == trusted_name:
        return f"same name as '{trusted}' under a different top-level domain"

    tokens = re.split(r"[-_]", name)
    if len(tokens) > 1 and trusted_name in tokens:
        return f"trusted name '{trusted_name}' plus an extra token ('{name}')"

    if len(trusted_name) >= 5 and _levenshtein(name, trusted_name) == 1:
        return f"one-character variation of '{trusted_name}'"

    return None


class SecurityScanner:
    """Stateless scanner: scan(message) -> ScanResult. No side effects."""

    def __init__(self, trusted_domains: set[str] | None = None):
        self.trusted_domains = {d.lower() for d in (trusted_domains or TRUSTED_DOMAINS)}

    # -- confusable sender ------------------------------------------------
    def _confusable_sender(self, msg: Message) -> tuple[str, str] | None:
        domain = msg.domain
        if not domain or domain in self.trusted_domains:
            return None
        for trusted in sorted(self.trusted_domains):
            reason = _confusable_reason(domain, trusted)
            if reason:
                return domain, reason
        return None

    def scan(self, msg: Message) -> ScanResult:
        text = f"{msg.subject}\n{msg.body}"
        decoded = _decoded_text(text)
        inspectable_text = f"{text}\n{decoded}" if decoded else text
        own_domain = next(iter(self.trusted_domains)) if self.trusted_domains else ""
        findings: list[Finding] = []

        def add(kind: str, signal: str, evidence: str, severity: str, target: str = "") -> None:
            findings.append(
                Finding(
                    kind=kind,
                    signal=signal,
                    evidence=evidence,
                    severity=severity,
                    attempted_action=ATTEMPTED_ACTIONS.get(signal, signal),
                    target=target,
                )
            )

        if decoded and (s := _find(FORWARD_MAIL_MARKERS, decoded)):
            add("prompt_injection", "encoded_forward_request", s, "critical",
                _external_recipient(decoded, own_domain))
        if s := _find(INJECTION_MARKERS, inspectable_text):
            add("prompt_injection", "ignore_previous_instructions", s, "critical")
        if s := _find(ROLE_PLAY_MARKERS, inspectable_text):
            add("prompt_injection", "role_play_attack", s, "high")
        if s := _find(FORWARD_MAIL_MARKERS, inspectable_text):
            add("prompt_injection", "forward_mail_request", s, "critical",
                _external_recipient(inspectable_text, own_domain))
        if s := _find(DELETE_MAIL_MARKERS, inspectable_text):
            add("prompt_injection", "delete_mail_request", s, "critical", msg.id)
        if s := _find(MASS_REPLY_MARKERS, inspectable_text):
            add("prompt_injection", "mass_reply_directive", s, "high")
        if s := _find(TOOL_INVOCATION_MARKERS, inspectable_text):
            add("prompt_injection", "direct_tool_invocation", s, "critical")
        if s := _find(UNSAFE_PREFERENCE_MARKERS, inspectable_text):
            add("prompt_injection", "unsafe_preference_request", s, "critical")
        if s := _find(CREDENTIAL_HARVEST_MARKERS, inspectable_text):
            url = URL_RE.search(inspectable_text)
            add("phishing", "credential_harvesting", s, "critical", url.group(0) if url else "")
        if s := _find(WIRE_FRAUD_MARKERS, inspectable_text):
            add("phishing", "invoice_wire_fraud", s, "critical", _payment_target(inspectable_text))
        if s := _find(SECRECY_PRESSURE_MARKERS, inspectable_text):
            add("social_engineering", "secrecy_pressure", s, "high")
        if s := _find(PHISHING_URGENCY_MARKERS, inspectable_text):
            # urgency alone is weak signal; only elevate if paired with another finding
            if findings:
                add("social_engineering", "artificial_urgency", s, "medium")

        # Confusable sender domain. Computed generically, but a lookalike on its
        # own is a weak signal (legitimate vendors use related-looking domains),
        # so it is only raised when something else in the message is already
        # suspicious or the local part claims institutional authority.
        if confusable := self._confusable_sender(msg):
            domain, reason = confusable
            claims_authority = bool(AUTHORITY_LOCAL_PARTS.match(msg.local_part))
            if findings or claims_authority:
                add(
                    "phishing",
                    "lookalike_domain",
                    f"sender domain '{domain}': {reason}",
                    "critical",
                    msg.from_,
                )

        # Self-spoof special case: a message claiming to reconfigure the
        # assistant, purportedly "from" the mailbox owner. Zero-trust means
        # From: the owner in a message BODY is never proof of intent --
        # instructions never travel through email content, full stop.
        if msg.is_self_sent and INJECTION_MARKERS.search(text):
            add(
                "prompt_injection",
                "self_spoofed_config_change",
                "message claims to change assistant behavior from inside an email body",
                "critical",
                msg.from_,
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
                        attempted_action=ATTEMPTED_ACTIONS["split_instruction_attack"],
                        target=f"{first.id}+{second.id}",
                    )
                    results[first.id].findings.append(finding)
                    results[second.id].findings.append(finding)
        return results
