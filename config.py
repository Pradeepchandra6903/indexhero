"""Model-provider configuration, loaded from environment variables.

The assignment requires the provider to be configurable via environment
variables through config.py, with no secrets hardcoded and no .env committed
(see .env.example). InboxHero's default path is fully OFFLINE and
deterministic -- it makes no model calls -- so a key is NOT required to run
any capability. These settings exist so an LLM drafting backend can be dropped
into the Router/Retrieval seams (see architecture.md) without code changes:
set INBOXHERO_MODE=llm and the provider vars below.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    mode: str          # "offline" (default, deterministic) | "llm"
    provider: str      # e.g. "openai" | "anthropic" | "ollama"
    model: str         # model name used for drafting/triage when mode=llm
    endpoint: str      # base URL / host (e.g. local Ollama)
    api_key: str       # NEVER hardcoded; read from env only
    request_delay_s: float   # spacing between calls to respect rate limits
    max_retries: int         # retries on HTTP 429 before giving up


def load_config() -> Config:
    return Config(
        mode=os.getenv("INBOXHERO_MODE", "offline"),
        provider=os.getenv("INBOXHERO_PROVIDER", "none"),
        model=os.getenv("INBOXHERO_MODEL", "none"),
        endpoint=os.getenv("INBOXHERO_ENDPOINT", ""),
        api_key=os.getenv("INBOXHERO_API_KEY", ""),
        request_delay_s=float(os.getenv("INBOXHERO_REQUEST_DELAY_S", "4")),
        max_retries=int(os.getenv("INBOXHERO_MAX_RETRIES", "5")),
    )


CONFIG = load_config()
