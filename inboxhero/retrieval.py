"""Retrieval Engine: thread-walk (primary) + keyword search (fallback).

Design choice, same reasoning as the sample: inbox.json already carries
structure via thread_id. Walking the thread is cheaper and more precise than
embeddings for this task; keyword search only kicks in for cross-thread
lookups (e.g. a commitment deadline defined in one thread and referenced from
another, like m040's "two days before the board review" needing m038).
"""
from __future__ import annotations

from dataclasses import dataclass

from .models import Message, by_thread


@dataclass
class Citation:
    message_id: str
    reason: str


class ThreadWalker:
    def __init__(self, messages: list[Message]):
        self.threads = by_thread(messages)

    def earlier_in_thread(self, msg: Message) -> list[Message]:
        thread = self.threads.get(msg.thread_id, [])
        return [m for m in thread if m.timestamp < msg.timestamp]

    def thread(self, thread_id: str) -> list[Message]:
        return self.threads.get(thread_id, [])


class KeywordRetrieval:
    """Fallback cross-thread lookup when thread-walk alone can't ground a claim."""

    def __init__(self, messages: list[Message]):
        self.messages = messages

    def search(self, keywords: list[str], exclude_id: str | None = None) -> list[Message]:
        kws = [k.lower() for k in keywords]
        hits = []
        for m in self.messages:
            if m.id == exclude_id:
                continue
            haystack = f"{m.subject} {m.body}".lower()
            if any(k in haystack for k in kws):
                hits.append(m)
        return hits


class CitationBuilder:
    """Turns retrieval hits into an auditable citation list attached to a draft."""

    @staticmethod
    def build(hits: list[Message], reason: str) -> list[Citation]:
        return [Citation(message_id=m.id, reason=reason) for m in hits]
