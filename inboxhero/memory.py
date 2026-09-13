"""Memory Manager: persistent user preferences that survive process restarts.

Backing store is memory/preferences.json (plain JSON, human-readable/auditable
on purpose -- no hidden vector state). R4 demo (see memory_demo.md) proves a
preference stated in run 1 changes behavior in a fully separate run 2 process.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

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
