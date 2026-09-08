"""RuleEngine: cheap, deterministic dispatch for high-volume automated mail.

Runs *after* the SecurityScanner (zero-trust: everything is scanned first).
Anything the scanner flags is never auto-archived by this engine, even if it
looks exactly like a newsletter (see m024, which is disguised as a product
digest but carries a prompt injection).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .models import Message

NEWSLETTER_HINTS = re.compile(
    r"(digest|newsletter|top \d+|today'?s (picks|top)|welcome back|recommendations|"
    r"insights|weekly (product|read)|your subscription)",
    re.IGNORECASE,
)
NEWSLETTER_SENDERS = re.compile(
    r"(newsletter|digest|substack|medium\.com|hackernewsletter|producthunt|"
    r"pragmaticengineer|coursera|grammarly)",
    re.IGNORECASE,
)

RECEIPT_HINTS = re.compile(
    r"(charged|was charged|receipt|thanks for your (purchase|order)|order (confirmed|delivered)|"
    r"paid via|payment of \$|invoice paid)",
    re.IGNORECASE,
)

NO_ACTION_HINTS = re.compile(
    r"(no action needed|no action is needed|no further action needed|for your records|"
    r"do not reply|this is an automated receipt|do not share this code|expires in \d+ minutes)",
    re.IGNORECASE,
)

AUTOMATED_SENDER_HINTS = re.compile(
    r"(no-?reply|no_reply|notifications?@|notify@|alerts?@|billing@|receipts?@|"
    r"invoice(\+|@)|info@|updates@|support@|checkin@|orders?@|ship-confirm|"
    r"calendar-notification|mailer-daemon|feedback@|success@|hr@|security@|status@)",
    re.IGNORECASE,
)

# Senders that look automated but actually require a human decision (asking
# for a reply / yes-no / confirmation that creates a commitment). The rule
# engine defers to the human path for these instead of blind-archiving them.
ACTION_REQUIRED_OVERRIDE = re.compile(
    r"(reply confirm|reply to confirm|just need a yes|reply (with )?release)",
    re.IGNORECASE,
)


@dataclass
class RuleVerdict:
    category: str | None   # "newsletter" | "receipt" | "notification" | None (no cheap match)
    reason: str


class RuleEngine:
    """Classifies obviously-automated mail without any model call."""

    def classify(self, msg: Message) -> RuleVerdict:
        text = f"{msg.subject}\n{msg.body}"

        if ACTION_REQUIRED_OVERRIDE.search(text):
            return RuleVerdict(None, "sender looks automated but message demands a human reply/decision")

        sender_is_automated = bool(AUTOMATED_SENDER_HINTS.search(msg.from_))

        if sender_is_automated and RECEIPT_HINTS.search(text):
            return RuleVerdict("receipt", "automated sender + payment/purchase confirmation language")

        if sender_is_automated and NEWSLETTER_SENDERS.search(msg.domain) and NEWSLETTER_HINTS.search(text):
            return RuleVerdict("newsletter", "known content-digest sender + digest/subscription language")
        if NEWSLETTER_SENDERS.search(msg.domain) and NEWSLETTER_HINTS.search(text):
            return RuleVerdict("newsletter", "known content-digest sender domain + digest language")
        if NEWSLETTER_SENDERS.search(msg.domain):
            return RuleVerdict("newsletter", "sender domain itself is a known content-digest/newsletter service")

        if sender_is_automated and NO_ACTION_HINTS.search(text):
            return RuleVerdict("notification", "automated sender + explicit 'no action needed' language")

        if sender_is_automated:
            return RuleVerdict("notification", "automated/system sender, informational content")

        return RuleVerdict(None, "sender does not match known automated patterns")
