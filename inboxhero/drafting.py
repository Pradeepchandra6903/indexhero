"""Draft builder: grounded replies assembled only from retrieved message text.

Part 3 rules this module exists to satisfy:

* a draft may only contain facts that appear in a message the retriever
  actually returned -- every sentence of evidence is quoted from a cited id,
  so a marker can diff the draft against the mail store;
* the cited ids are validated against the mail store before the draft is
  returned, so a draft can never cite a message that does not exist;
* if retrieval finds nothing that supports an answer, the builder refuses and
  produces no draft at all;
* secrets found in email are used *structurally* (host, user, path) but the
  secret itself is masked rather than replayed into a new message.

R2 prints these drafts, R3 sends them (through the approval gate), R4 attaches
preference-driven CCs to them, and X5 negotiates with them, so there is exactly
one place where reply text is produced.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .commitments import (
    Commitment,
    derive_cross_thread_commitments,
    extract_commitments,
    validate_citations,
)
from .memory import CC_KEY_PREFIX, EARLIEST_MEETING_KEY
from .models import Message, by_id
from .retrieval import CitationBuilder, ThreadWalker

# amqp://user:secret@host:5672/vhost -> keep the structure, drop the secret
URL_WITH_SECRET_RE = re.compile(
    r"(?P<scheme>\w+://)(?P<user>[^:/@\s]+):(?P<secret>[^@\s]+)@(?P<host>[^\s/]+)(?P<path>/[^\s.]*)?"
)
ANY_URL_RE = re.compile(r"\b\w+://[^\s]+")

# A message asking for credentials/an endpoint to be re-sent.
SECRET_REQUEST_RE = re.compile(
    r"(resend|re-send|send me|share|need)\b[^.]{0,80}"
    r"(cred|creds|credentials|password|secret|url|queue|connection)",
    re.IGNORECASE,
)

# Local parts that are a role/alias rather than a person, so the draft does not
# open with "Hi Appointments".
ROLE_LOCAL_PART_RE = re.compile(
    r"^(partners?|appointments?|support|info|events?|editor|billing|alerts?|"
    r"no-?reply|notifications?|accounts?|team|hello|contact|admin|sales)",
    re.IGNORECASE,
)

ASK_HINT_RE = re.compile(r"(can you|could you|would you|please|need|does .* work|next steps)", re.IGNORECASE)


@dataclass
class Draft:
    message_id: str
    to: str
    subject: str
    body: str
    cited: list[str] = field(default_factory=list)
    cc: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    kind: str = ""
    refused: bool = False
    refusal_reason: str = ""

    def as_payload(self) -> dict:
        """What the approval gate records and what a send would write out."""
        return {
            "message_id": self.message_id,
            "to": self.to,
            "cc": self.cc,
            "subject": self.subject,
            "body": self.body,
            "cited": self.cited,
            "evidence": self.evidence,
            "draft_kind": self.kind,
        }


def mask_url_secret(url: str) -> str:
    """amqp://u:p@host/vhost -> amqp://u:***@host/vhost (never replay the secret)."""
    return URL_WITH_SECRET_RE.sub(
        lambda m: f"{m.group('scheme')}{m.group('user')}:***@{m.group('host')}{m.group('path') or ''}",
        url,
    )


_ABBREVIATIONS = ("Dr.", "Mr.", "Mrs.", "Ms.", "Prof.", "e.g.", "i.e.", "No.", "vs.")
_ABBREVIATION_GUARD = "\u0001"


def _sentences(text: str) -> list[str]:
    """Split into sentences without breaking on 'Dr.'-style abbreviations."""
    flat = text.replace("\n", " ")
    for abbreviation in _ABBREVIATIONS:
        flat = flat.replace(abbreviation, abbreviation.replace(".", _ABBREVIATION_GUARD))
    parts = re.split(r"(?<=[.!?])\s+", flat)
    return [p.replace(_ABBREVIATION_GUARD, ".").strip() for p in parts if p.strip()]


_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "to", "of", "in", "on", "for", "with",
    "is", "are", "was", "were", "be", "been", "it", "this", "that", "these",
    "those", "i", "you", "we", "they", "he", "she", "me", "my", "your", "our",
    "can", "could", "would", "should", "do", "does", "did", "have", "has", "had",
    "at", "by", "from", "as", "if", "so", "not", "no", "any", "all", "just",
    "need", "please", "thanks", "hi", "hey", "re",
}


def _keywords(text: str) -> set[str]:
    tokens = re.findall(r"[a-z0-9]{3,}", text.lower())
    return {t for t in tokens if t not in _STOPWORDS}


def _rank_by_overlap(target_text: str, candidates: list[Message]) -> list[Message]:
    """Most topically relevant earlier messages first, recency as tie-break."""
    wanted = _keywords(target_text)
    scored = [
        (len(wanted & _keywords(f"{m.subject} {m.body}")), m.timestamp, m)
        for m in candidates
    ]
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [m for _, _, m in scored]


def _sentence_with(text: str, needle: str) -> str:
    for sentence in _sentences(text):
        if needle.lower() in sentence.lower():
            return sentence
    return _sentences(text)[0] if _sentences(text) else text.strip()


def _ask_sentence(msg: Message) -> str:
    sentences = _sentences(msg.body)
    for sentence in sentences:
        if "?" in sentence:
            return sentence
    for sentence in sentences:
        if ASK_HINT_RE.search(sentence):
            return sentence
    return sentences[0] if sentences else msg.subject


def _salutation(msg: Message) -> str:
    local = msg.local_part
    if ROLE_LOCAL_PART_RE.match(local):
        return "Hi --"
    name = re.split(r"[._+-]", local)[0]
    return f"Hi {name.capitalize()} --" if name.isalpha() else "Hi --"


def _reply_subject(msg: Message) -> str:
    subject = msg.subject.strip() or "(no subject)"
    return subject if subject.lower().startswith("re:") else f"Re: {subject}"


def _slot(commitment: Commitment | None) -> str:
    if not commitment:
        return "that slot"
    parts = [p for p in (commitment.day_token, commitment.time_token) if p]
    return " ".join(parts) if parts else "that slot"


def _ordinal(day_token: str) -> str:
    """'16' -> '16th'. Left alone if it is not a bare number ('monday')."""
    if not day_token.isdigit():
        return day_token
    number = int(day_token)
    if 11 <= number % 100 <= 13:
        return f"{number}th"
    suffix = {1: "st", 2: "nd", 3: "rd"}.get(number % 10, "th")
    return f"{number}{suffix}"


def _hour_24(time_token: str) -> int:
    match = re.match(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)", time_token, re.IGNORECASE)
    if not match:
        return -1
    hour = int(match.group(1)) % 12
    if match.group(3).lower() == "pm":
        hour += 12
    return hour


class DraftBuilder:
    """Single source of reply text. Retrieval first, then grounded assembly."""

    def __init__(self, messages: list[Message], decisions=None, memory=None):
        self.messages = messages
        self.by_id = by_id(messages)
        self.walker = ThreadWalker(messages)
        self.memory = memory
        self.decisions = {d.message_id: d for d in (decisions or [])}
        self.commitments = {c.message_id: c for c in extract_commitments(messages)}
        self.derived = {d.cited[-1]: d for d in derive_cross_thread_commitments(messages)}

    # -- public ----------------------------------------------------------
    def build(self, message_id: str) -> Draft:
        target = self.by_id.get(message_id)
        if not target:
            return Draft(
                message_id=message_id, to="", subject="", body="",
                refused=True, refusal_reason=f"{message_id} is not in the mail store",
            )

        decision = self.decisions.get(message_id)
        if decision is not None and decision.category == "security_threat":
            return self._refuse(
                target,
                "message was flagged as untrusted content; no reply is drafted for it",
            )

        built = (
            self._draft_secret_request(target)
            or self._draft_derived_deadline(target)
            or self._draft_conflict(target, decision)
            or self._draft_preference_violation(target)
            or self._draft_thread_context(target)
            or self._draft_preference_cc(target)
        )
        return self._finalize(target, built)

    def build_all(self, message_ids) -> dict[str, Draft]:
        return {mid: self.build(mid) for mid in message_ids}

    def preference_violations(self) -> list[dict]:
        """Every message proposing a time earlier than the stored cutoff.

        Driven entirely by the learned preference and the commitments parsed
        out of the inbox, so a new message proposing an early slot is picked up
        without touching this code.

        Each hit is tagged `negotiable` when it is a human still waiting on an
        answer, and advisory otherwise -- an automated calendar notice or an
        already-settled commitment is worth surfacing but not worth writing a
        counter-proposal to.
        """
        preference = self._earliest_meeting()
        if not preference:
            return []
        limit = _hour_24((preference.get("value") or {}).get("earliest", ""))
        if limit < 0:
            return []
        violations = []
        for message_id, commitment in self.commitments.items():
            if not commitment.time_token:
                continue
            proposed = _hour_24(commitment.time_token)
            if proposed < 0 or proposed >= limit:
                continue
            decision = self.decisions.get(message_id)
            category = decision.category if decision else ""
            disposition = decision.disposition if decision else ""
            violations.append(
                {
                    "message_id": message_id,
                    "proposed": _slot(commitment),
                    "earliest": (preference.get("value") or {}).get("earliest", ""),
                    "preference_source": preference.get("source_message_id", ""),
                    "category": category,
                    "disposition": disposition,
                    "negotiable": category == "human_conversation"
                    and disposition in ("reply", "delegate"),
                }
            )
        return sorted(violations, key=lambda v: v["message_id"])

    def build_preference_violation(self, message_id: str) -> Draft:
        """Counter-proposal for one early-slot request (used by X5).

        Separate from `build` because a message can both violate the cutoff and
        collide with another commitment; `build` picks one disposition, while
        X5 is specifically negotiating the cutoff.
        """
        target = self.by_id.get(message_id)
        if not target:
            return Draft(
                message_id=message_id, to="", subject="", body="",
                refused=True, refusal_reason=f"{message_id} is not in the mail store",
            )
        built = self._draft_preference_violation(target)
        if not built:
            return self._refuse(target, "no stored preference is violated by this message")
        return self._finalize(target, built)

    def _finalize(self, target: Message, built) -> Draft:
        if not built:
            return self._refuse(
                target,
                "no earlier message in the inbox supports an answer, so nothing is drafted",
            )

        kind, cited, evidence, body = built
        present, missing = validate_citations(cited, self.messages)
        if missing or not present:
            return self._refuse(
                target,
                f"retrieved citation(s) {missing or cited} are not present in the mail store",
            )

        return Draft(
            message_id=target.id,
            to=target.from_,
            subject=_reply_subject(target),
            body=body,
            cited=present,
            cc=self._applicable_cc(target),
            evidence=evidence,
            kind=kind,
        )

    def citations_for(self, draft: Draft):
        return CitationBuilder.build(
            [self.by_id[cid] for cid in draft.cited], reason=f"grounded {draft.kind} draft"
        )

    # -- preference-driven CC (Part 5) -----------------------------------
    def _applicable_cc(self, target: Message) -> list[str]:
        if not self.memory:
            return []
        cc: list[str] = []
        for record in self.memory.find(CC_KEY_PREFIX):
            domain = (record.get("value") or {}).get("domain", "")
            address = (record.get("value") or {}).get("cc", "")
            if domain and address and target.domain.endswith(domain) and address not in cc:
                cc.append(address)
        return cc

    def _earliest_meeting(self) -> dict | None:
        return self.memory.get(EARLIEST_MEETING_KEY) if self.memory else None

    # -- refusal ---------------------------------------------------------
    def _refuse(self, target: Message, reason: str) -> Draft:
        return Draft(
            message_id=target.id,
            to=target.from_,
            subject=_reply_subject(target),
            body="",
            refused=True,
            refusal_reason=reason,
        )

    # -- grounded variants ----------------------------------------------
    def _draft_secret_request(self, target: Message):
        """Asked to resend an endpoint/credential already present in the thread."""
        if not SECRET_REQUEST_RE.search(target.body):
            return None
        for source in reversed(self.walker.earlier_in_thread(target)):
            match = URL_WITH_SECRET_RE.search(source.body)
            if not match:
                continue
            quoted = mask_url_secret(_sentence_with(source.body, match.group(0)))
            host = match.group("host")
            path = match.group("path") or ""
            body = (
                f"{_salutation(target)} this is already answered in {source.id} "
                f"({source.timestamp[:10]}), which says: \"{quoted}\"\n\n"
                f"So the endpoint is {host}{path} with user {match.group('user')}. "
                f"I am not repeating the password in a mail thread -- ping me directly "
                f"for that part."
            )
            return "credential_request", [source.id], [quoted], body
        return None

    def _draft_derived_deadline(self, target: Message):
        """Deadline stated relative to an event dated in another message."""
        derived = self.derived.get(target.id)
        if not derived:
            return None
        anchor_id = derived.cited[0]
        anchor = self.by_id.get(anchor_id)
        if not anchor:
            return None
        anchor_sentence = _sentence_with(anchor.body, "the ")
        offset = derived.text.split(" days before")[0].split("(")[-1]
        anchor_day = derived.text.split("on the ")[-1].rstrip(".).")
        body = (
            f"{_salutation(target)} yes. {anchor_id} puts it on the "
            f"{_ordinal(anchor_day)}, so counting back {offset} days, "
            f"I will have the {target.subject.strip()} circulated by the "
            f"{_ordinal(derived.day_token)}.\n\nGrounding: \"{anchor_sentence}\" "
            f"({anchor_id}) and your own \"{_ask_sentence(target)}\" ({target.id})."
        )
        return "derived_deadline", list(derived.cited), [anchor_sentence, derived.derivation], body

    def _draft_conflict(self, target: Message, decision):
        """Proposed slot already taken by a commitment in another message."""
        if decision is None:
            return None
        conflict_ids = [cid for cid in decision.flag_values("conflict") if cid in self.by_id]
        if not conflict_ids:
            return None
        other = self.by_id[conflict_ids[0]]
        other_slot = _slot(self.commitments.get(other.id))
        mine = _slot(self.commitments.get(target.id))
        other_sentence = _sentence_with(other.body, other_slot.split()[-1] if other_slot else "")
        body = (
            f"{_salutation(target)} {mine} does not work on my side. "
            f"{other.id} already has that slot: \"{other_sentence}\" "
            f"(from {other.from_}). Can we pick another time rather than double-book?"
        )
        return "conflict", [target.id, other.id], [other_sentence], body

    def _draft_preference_violation(self, target: Message):
        """Proposed time is earlier than a stored standing preference."""
        preference = self._earliest_meeting()
        commitment = self.commitments.get(target.id)
        if not preference or not commitment or not commitment.time_token:
            return None
        earliest = (preference.get("value") or {}).get("earliest", "")
        proposed_hour = _hour_24(commitment.time_token)
        limit_hour = _hour_24(earliest)
        if proposed_hour < 0 or limit_hour < 0 or proposed_hour >= limit_hour:
            return None
        source_id = preference.get("source_message_id", "")
        body = (
            f"{_salutation(target)} {_slot(commitment)} is earlier than a standing "
            f"rule on this mailbox: \"{preference.get('stated', '')}\" ({source_id}). "
            f"Could we do {earliest} or later instead? I have not picked an "
            f"alternative slot because no free time is recorded in the inbox."
        )
        return "preference_violation", [source_id], [preference.get("stated", "")], body

    def _draft_thread_context(self, target: Message):
        """Fall back to the thread: quote the ask plus the most relevant earlier mail."""
        earlier = self.walker.earlier_in_thread(target)
        if not earlier:
            return None
        ask = _ask_sentence(target)
        ranked = _rank_by_overlap(f"{target.subject} {ask}", earlier)[:2]
        context_msgs = sorted(ranked, key=lambda m: m.timestamp)
        context = _sentences(context_msgs[-1].body)
        context_sentence = context[0] if context else context_msgs[-1].subject
        body = (
            f"{_salutation(target)} on \"{ask}\" -- noted, I will "
            f"come back with the specifics.\n\nContext I am working from "
            f"({', '.join(m.id for m in context_msgs)}): \"{context_sentence}\""
        )
        return (
            "thread_context",
            [m.id for m in context_msgs],
            [context_sentence],
            body,
        )

    def _draft_preference_cc(self, target: Message):
        """No thread history, but a stored CC rule covers this correspondent.

        The grounding is the preference statement itself, so the acknowledgement
        quotes the message that asked for the CC and copies the requester.
        """
        if not self.memory:
            return None
        for record in self.memory.find(CC_KEY_PREFIX):
            value = record.get("value") or {}
            domain, address = value.get("domain", ""), value.get("cc", "")
            if not domain or not address or not target.domain.endswith(domain):
                continue
            source_id = record.get("source_message_id", "")
            stated = record.get("stated", "")
            body = (
                f"{_salutation(target)} received -- \"{_ask_sentence(target)}\" is "
                f"with me and I am routing it for sign-off rather than answering it "
                f"myself.\n\nCopying {address} on this thread per a standing rule on "
                f"the mailbox: \"{stated}\" ({source_id})."
            )
            return "preference_cc", [source_id], [stated], body
        return None
