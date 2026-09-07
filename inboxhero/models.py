"""Data model + loading for the InboxHero pipeline."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
INBOX_PATH = ROOT / "inbox.json"


@dataclass
class Message:
    id: str
    thread_id: str
    from_: str
    to: str
    subject: str
    timestamp: str
    unread: bool
    body: str
    raw: dict = field(default_factory=dict, repr=False)

    @staticmethod
    def from_dict(d: dict) -> "Message":
        return Message(
            id=d["id"],
            thread_id=d["thread_id"],
            from_=d["from"],
            to=d["to"],
            subject=d.get("subject", ""),
            timestamp=d.get("timestamp", ""),
            unread=bool(d.get("unread", False)),
            body=d.get("body", ""),
            raw=d,
        )

    @property
    def domain(self) -> str:
        return self.from_.split("@")[-1].lower() if "@" in self.from_ else ""

    @property
    def local_part(self) -> str:
        return self.from_.split("@")[0].lower() if "@" in self.from_ else self.from_.lower()

    @property
    def is_self_sent(self) -> bool:
        # "sam@paperjet.io" is the mailbox owner throughout inbox.json
        return self.from_.lower() == "sam@paperjet.io"


def load_inbox(path: Optional[Path] = None) -> list[Message]:
    p = path or INBOX_PATH
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        raise SystemExit(f"inbox not found: {p}")
    except json.JSONDecodeError as e:
        raise SystemExit(f"inbox is not valid JSON ({p}): {e}")
    if not isinstance(data, list):
        raise SystemExit(f"inbox must be a JSON array of messages, got {type(data).__name__}")

    required = ("id", "thread_id", "from", "to", "subject", "timestamp", "body", "unread")
    msgs: list[Message] = []
    seen_ids: set[str] = set()
    for i, d in enumerate(data):
        if not isinstance(d, dict) or any(k not in d for k in required):
            raise SystemExit(f"malformed message at index {i}: required keys are {', '.join(required)}")
        if not isinstance(d["unread"], bool):
            raise SystemExit(f"malformed message {d['id']}: unread must be a boolean")
        if not all(isinstance(d[key], str) for key in required if key != "unread"):
            raise SystemExit(f"malformed message at index {i}: text fields must be strings")
        if not all(d[key] for key in ("id", "thread_id", "from", "to", "timestamp")):
            raise SystemExit(f"malformed message at index {i}: id, thread_id, from, to, and timestamp must be non-empty")
        if d["id"] in seen_ids:
            raise SystemExit(f"duplicate message id: {d['id']}")
        try:
            datetime.fromisoformat(d["timestamp"])
        except ValueError:
            raise SystemExit(f"malformed message {d['id']}: timestamp must be ISO-8601") from None
        seen_ids.add(d["id"])
        msgs.append(Message.from_dict(d))
    # Deterministic ordering: chronological. inbox.json is not sorted, and
    # every capability that reasons about "earlier" messages (grounding,
    # follow-up age, thread walking) depends on stable timestamp order.
    msgs.sort(key=lambda m: (m.timestamp, m.id))
    return msgs


def by_thread(messages: list[Message]) -> dict[str, list[Message]]:
    threads: dict[str, list[Message]] = {}
    for m in messages:
        threads.setdefault(m.thread_id, []).append(m)
    for t in threads.values():
        t.sort(key=lambda m: m.timestamp)
    return threads


def by_id(messages: list[Message]) -> dict[str, Message]:
    return {m.id: m for m in messages}
