"""Tiny stdlib-only math helper (bench fixture: test-write-clamp)."""

from __future__ import annotations


def clamp(value: float, low: float, high: float) -> float:
    """Restrict `value` to the inclusive range [low, high]."""
    if value < low:
        return low
    if value > high:
        return high
    return value
