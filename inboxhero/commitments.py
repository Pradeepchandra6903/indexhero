"""Commitment extraction + cross-thread conflict detection (feeds R6 dashboard)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .models import Message

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
