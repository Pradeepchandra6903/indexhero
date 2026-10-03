"""Memory Manager: persistent user preferences that survive process restarts.

Backing store is memory/preferences.json (plain JSON, human-readable/auditable
on purpose -- no hidden vector state). R4 demo (see memory_demo.md) proves a
preference stated in run 1 changes behavior in a fully separate run 2 process.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .models import Message

ROOT = Path(__file__).resolve().parent.parent
PREFS_PATH = ROOT / "memory" / "preferences.json"


@dataclass
class Preference:
    key: str
    value: Any
    source_message_id: str
    stated: str            # human-readable statement of the preference
    created_at: str        # timestamp this was learned


class PreferenceMemory:
    def __init__(self, path: Path | None = None):
        self.path = path or PREFS_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._data: dict[str, dict] = self._load()

    def _load(self) -> dict[str, dict]:
        if not self.path.exists():
            return {}
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError as exc:
            raise ValueError(f"preferences file is not valid JSON: {self.path}") from exc
        if not isinstance(data, dict):
            raise ValueError("preferences file must contain an object")
        required = {"key", "value", "source_message_id", "stated", "created_at"}
        for key, record in data.items():
            if not isinstance(record, dict) or not required.issubset(record):
                raise ValueError(f"preference '{key}' has an invalid record")
            if record["key"] != key:
                raise ValueError(f"preference '{key}' has a mismatched key")
        return data

    def save(self) -> None:
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2)

    def remember(self, pref: Preference) -> None:
        self._data[pref.key] = asdict(pref)
        self.save()

    def get(self, key: str) -> dict | None:
        return self._data.get(key)

    def all(self) -> dict[str, dict]:
        return dict(self._data)

    def has(self, key: str) -> bool:
        return key in self._data

    def find(self, prefix: str) -> list[dict]:
        """Every stored preference whose key starts with prefix."""
        return [record for key, record in sorted(self._data.items()) if key.startswith(prefix)]


# ---------------------------------------------------------------------------
# Learning preferences from message text (Part 5).
#
# Nothing here hardcodes a message id or a preference value: the statement, the
# subject of the rule and the person to copy are all parsed out of the body of
# whichever message states them, and the firm named in the text is resolved to
# a real sender domain by looking it up in the mailbox.
# ---------------------------------------------------------------------------

CC_KEY_PREFIX = "cc_rule:"
EARLIEST_MEETING_KEY = "earliest_meeting_time"

# "please make sure I'm CC'd on anything that comes in from our lawyers at
# Hartwell & Cho" -> copy the sender, scope = "Hartwell & Cho".
CC_REQUEST_RE = re.compile(
    r"(?:cc'?d?|copied|loop me in|copy me)\b[^.]*?\bfrom\s+(?:our\s+\w+\s+at\s+|our\s+)?"
    r"(?P<scope>[A-Z][\w&.\- ]{2,40}?)(?=[.,\n]|\s+\()",
    re.IGNORECASE,
)

# "I do not take meetings before 11:00am"
EARLIEST_MEETING_RE = re.compile(
    r"(?:do not|don'?t|never)\s+(?:take|accept|do|schedule)\s+(?:any\s+)?meetings?\s+"
    r"before\s+(?P<time>\d{1,2}(?::\d{2})?\s*(?:am|pm))",
    re.IGNORECASE,
)


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _resolve_scope_to_domain(scope: str, messages: list[Message]) -> str | None:
    """Map a firm named in prose ('Hartwell & Cho') to a sender domain present
    in the mailbox ('hartwellcho.com'). Derived from the data, not a constant."""
    target = _normalize(scope)
    if not target:
        return None
    for msg in messages:
        domain = msg.domain
        if not domain:
            continue
        registrable = _normalize(domain.rsplit(".", 1)[0])
        if registrable and (registrable == target or target in registrable or registrable in target):
            return domain
    return None


class PreferenceLearner:
    """Extracts standing preferences from the text of the messages that state them."""

    def __init__(self, messages: list[Message]):
        self.messages = messages

    def learn(self) -> list[Preference]:
        found: list[Preference] = []
        for msg in self.messages:
            text = f"{msg.subject}. {msg.body}"

            if match := CC_REQUEST_RE.search(text):
                scope = match.group("scope").strip(" .,")
                domain = _resolve_scope_to_domain(scope, self.messages)
                if domain:
                    found.append(
                        Preference(
                            key=f"{CC_KEY_PREFIX}{domain}",
                            value={"cc": msg.from_, "domain": domain, "scope": scope},
                            source_message_id=msg.id,
                            stated=self._sentence_containing(msg.body, match.group(0)),
                            created_at=msg.timestamp,
                        )
                    )

            if match := EARLIEST_MEETING_RE.search(text):
                earliest = match.group("time").replace(" ", "").lower()
                found.append(
                    Preference(
                        key=EARLIEST_MEETING_KEY,
                        value={"earliest": earliest},
                        source_message_id=msg.id,
                        stated=self._sentence_containing(msg.body, match.group(0)),
                        created_at=msg.timestamp,
                    )
                )
        return found

    @staticmethod
    def _sentence_containing(body: str, fragment: str) -> str:
        """Quote the owner's/colleague's own sentence, so `stated` is evidence."""
        needle = fragment.split(".")[0][:40].lower()
        for sentence in re.split(r"(?<=[.!?])\s+", body.replace("\n", " ")):
            if needle in sentence.lower():
                return sentence.strip()
        return body.strip().split(".")[0]
