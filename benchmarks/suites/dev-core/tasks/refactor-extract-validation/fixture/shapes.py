"""Tiny stdlib-only geometry helpers (bench fixture: refactor-extract-validation).

`rectangle_area` and `triangle_area` both work correctly already -- this fixture's
tests pass unmodified. The problem is DRY: the exact same validation check is
duplicated in both functions instead of living in one shared helper.
"""

from __future__ import annotations


def rectangle_area(w: float, h: float) -> float:
    if w < 0 or h < 0:
        raise ValueError("dimensions must be non-negative")
    return w * h


def triangle_area(base: float, height: float) -> float:
    if base < 0 or height < 0:
        raise ValueError("dimensions must be non-negative")
    return 0.5 * base * height
