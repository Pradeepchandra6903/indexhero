"""Commitment extraction + cross-thread conflict detection (feeds R6 dashboard)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .models import Message, by_id

DEADLINE_HINTS = re.compile(
    r"(by (friday|monday|tuesday|wednesday|thursday|the \d{1,2}(st|nd|rd|th)?)|"
    r"due|deadline|before (month-?end|end of day)|expires? in|hold expires|"
    r"two days before|ahead of the meeting)",
    re.IGNORECASE,
)

TIME_RE = re.compile(r"\b(\d{1,2})(:\d{2})?\s?(am|pm)\b", re.IGNORECASE)
WEEKDAY_RE = re.compile(r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", re.IGNORECASE)
DATE_RE = re.compile(
    r"\b(?:(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s*(\d{1,2})(?:st|nd|rd|th)?"
    r"|the (\d{1,2})(?:st|nd|rd|th))\b",
    re.IGNORECASE,
)


@dataclass
class Commitment:
    message_id: str
    thread_id: str
    text: str
    day_token: str | None
    time_token: str | None
    cited: list[str] = field(default_factory=list)


def extract_commitments(messages: list[Message]) -> list[Commitment]:
    out: list[Commitment] = []
    for m in messages:
        text = f"{m.subject}. {m.body}"
        if not DEADLINE_HINTS.search(text) and not TIME_RE.search(text):
            continue
        time = TIME_RE.search(text)
        # Prefer a concrete calendar date ("the 15th", "September 15") over a
        # bare weekday name, and prefer whichever is nearest to (and
        # preceding) the clock time -- that's how a proposed slot is phrased
        # ("Wednesday at 2:00pm"), not the first weekday mentioned anywhere
        # in the message (which may be the day being moved *away* from, e.g.
        # "from Thursday to Wednesday at 2:00pm").
        window = text[: time.start()] if time else text
        date_matches = list(DATE_RE.finditer(window))
        weekday_matches = list(WEEKDAY_RE.finditer(window))
        day = date_matches[-1] if date_matches else (weekday_matches[-1] if weekday_matches else None)
        if not day and not time:
            continue
        out.append(
            Commitment(
                message_id=m.id,
                thread_id=m.thread_id,
                text=text.strip().replace("\n", " ")[:180],
                day_token=day.group(0).lower() if day else None,
                time_token=time.group(0).lower().replace(" ", "") if time else None,
            )
        )
    return out


def detect_conflicts(commitments: list[Commitment]) -> list[dict]:
    """Group by (day_token normalized, time_token) and flag collisions across
    different threads -- e.g. m010 (investor call Tue 15th 3pm) vs m061
    (dentist appt Sep 15 3pm), and m013 (1:1 moved to Wed 2pm) vs m016 (acme
    demo Wed 2pm)."""
    buckets: dict[tuple[str, str], list[Commitment]] = {}
    for c in commitments:
        if not c.day_token or not c.time_token:
            continue
        key = (_normalize_day(c.day_token), c.time_token)
        buckets.setdefault(key, []).append(c)

    conflicts = []
    for (day, time), items in buckets.items():
        threads = {c.thread_id for c in items}
        if len(items) > 1 and len(threads) > 1:
            conflicts.append(
                {
                    "day": day,
                    "time": time,
                    "message_ids": [c.message_id for c in items],
                    "reason": f"Two independent commitments both land at {day} {time}",
                }
            )
    return conflicts


def _normalize_day(token: str) -> str:
    token = token.lower().strip()
    # Collapse "tuesday the 15th" style variants and "sep 15" -> "15"
    m = re.search(r"\d{1,2}", token)
    if m:
        return m.group(0)
    return token


# ---------------------------------------------------------------------------
# Cross-thread commitments (Part 7).
#
# A message can state a deadline *relative* to an event whose actual date lives
# in a different message, in a different thread ("two days before the board
# review"). Resolving it needs both messages, so the entry below is computed:
# the offset and the event name come from the referring message, the absolute
# date from whichever message actually schedules that event, and the resulting
# date is arithmetic over the two. Nothing is hardcoded per message.
# ---------------------------------------------------------------------------

NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}

RELATIVE_DEADLINE_RE = re.compile(
    r"(?P<offset>\d{1,2}|" + "|".join(NUMBER_WORDS) + r")\s+days?\s+before\s+"
    r"(?:the\s+)?(?P<anchor>[A-Za-z]+(?:\s+[A-Za-z]+){0,2})",
    re.IGNORECASE,
)

# What the deadline applies to, taken from the referring message's own subject.
_FILLER_ANCHOR_WORDS = {"the", "a", "an", "our", "your", "next", "this"}


@dataclass
class DerivedCommitment:
    text: str
    day_token: str
    time_token: str | None
    cited: list[str]
    derivation: str          # which fact came from which message


def _parse_offset(token: str) -> int | None:
    token = token.lower()
    if token.isdigit():
        return int(token)
    return NUMBER_WORDS.get(token)


def _absolute_day(text: str) -> str | None:
    match = DATE_RE.search(text)
    if not match:
        return None
    digits = re.search(r"\d{1,2}", match.group(0))
    return digits.group(0) if digits else None


def _clean_anchor(phrase: str) -> str:
    words = [w for w in phrase.lower().split() if w not in _FILLER_ANCHOR_WORDS]
    return " ".join(words).strip()


def derive_cross_thread_commitments(messages: list[Message]) -> list[DerivedCommitment]:
    """Resolve 'N days before <event>' against the message that dates <event>."""
    derived: list[DerivedCommitment] = []
    for source in messages:
        source_text = f"{source.subject}. {source.body}"
        match = RELATIVE_DEADLINE_RE.search(source_text)
        if not match:
            continue
        offset = _parse_offset(match.group("offset"))
        anchor_phrase = _clean_anchor(match.group("anchor"))
        if offset is None or not anchor_phrase:
            continue

        anchor_msg = None
        anchor_day = None
        for candidate in messages:
            if candidate.id == source.id:
                continue
            candidate_text = f"{candidate.subject}. {candidate.body}"
            if anchor_phrase not in candidate_text.lower():
                continue
            day = _absolute_day(candidate_text)
            if day:
                anchor_msg, anchor_day = candidate, day
                break
        if not anchor_msg or anchor_day is None:
            continue

        due_day = int(anchor_day) - offset
        if due_day < 1:
            continue

        subject = source.subject.strip() or "commitment"
        derived.append(
            DerivedCommitment(
                text=(
                    f"{subject}: due the {due_day} "
                    f"({offset} days before the {anchor_phrase} on the {anchor_day})."
                ),
                day_token=str(due_day),
                time_token=None,
                cited=[anchor_msg.id, source.id],
                derivation=(
                    f"offset '{offset} days before the {anchor_phrase}' and the subject "
                    f"'{subject}' come from {source.id}; the {anchor_day} comes from "
                    f"{anchor_msg.id} ('{anchor_phrase}' scheduled there); "
                    f"{anchor_day} - {offset} = {due_day}"
                ),
            )
        )
    return derived


def validate_citations(cited: list[str], messages: list[Message]) -> tuple[list[str], list[str]]:
    """Split cited ids into (present in the mail store, missing). Part 3/7."""
    known = by_id(messages)
    present = [cid for cid in cited if cid in known]
    missing = [cid for cid in cited if cid not in known]
    return present, missing
