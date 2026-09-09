"""Capability registry. Each module exposes run(ctx) -> dict result.

ctx is a plain namespace built once in demo.py (messages, decisions, walker,
memory, trace, gate, args) and passed to every capability so state (like the
preference memory or the trace log) is shared and inspectable.
"""
from . import r1, r2, r3, r4, r5, r6, x1, x2, x3, x4, x5

REGISTRY = {
    "R1": r1.run,
    "R2": r2.run,
    "R3": r3.run,
    "R4": r4.run,
    "R5": r5.run,
    "R6": r6.run,
    "X1": x1.run,
    "X2": x2.run,
    "X3": x3.run,
    "X4": x4.run,
    "X5": x5.run,
}

ORDER = ["R1", "R2", "R3", "R4", "R5", "R6", "X1", "X2", "X3", "X4", "X5"]
