#!/usr/bin/env python3
"""InboxHero entry point.

    python demo.py --cap R1
    python demo.py --cap R2 --msg m008
    python demo.py --cap R3 --dry-run
    python demo.py --cap R4          # run twice, in two separate processes
    python demo.py --all             # every capability, in rubric order
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from inboxhero.capabilities import ORDER, REGISTRY
from inboxhero.memory import PreferenceMemory
from inboxhero.models import load_inbox
from inboxhero.router import Router
from inboxhero.trace import TraceLogger


def build_context(args) -> SimpleNamespace:
    messages = load_inbox()
    router = Router()
    decisions = [router.route(m) for m in messages]
    trace = TraceLogger(truncate=getattr(args, "fresh_trace", False))
    memory = PreferenceMemory()
    return SimpleNamespace(
        messages=messages,
        decisions=decisions,
        trace=trace,
        memory=memory,
        args=args,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="InboxHero")
    parser.add_argument("--cap", choices=list(REGISTRY.keys()), help="Run one capability")
    parser.add_argument("--all", action="store_true", help="Run every capability in rubric order")
    parser.add_argument("--msg", default=None, help="Target message id (R2)")
    parser.add_argument("--dry-run", action="store_true", help="Show irreversible actions without executing them (R3)")
    parser.add_argument("--auto-deny", action="store_true", help="Non-interactive mode: deny every approval prompt")
    parser.add_argument("--fresh-trace", action="store_true", help="Truncate trace.jsonl before this run")
    args = parser.parse_args()

    if not args.cap and not args.all:
        parser.print_help()
        return 1

    ctx = build_context(args)

    results = {}
    caps = ORDER if args.all else [args.cap]
    for cap in caps:
        print(f"\n{'=' * 20} {cap} {'=' * 20}")
        results[cap] = REGISTRY[cap](ctx)

    if args.all:
        print("\n" + "=" * 46)
        print(json.dumps({k: (v if isinstance(v, (int, str)) else "ok") for k, v in results.items()}, indent=2, default=str))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
