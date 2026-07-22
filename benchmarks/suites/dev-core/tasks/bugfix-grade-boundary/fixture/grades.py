"""Tiny stdlib-only grading helper (bench fixture: bugfix-grade-boundary)."""

from __future__ import annotations


def letter_grade(score: int) -> str:
    """Map a 0-100 score to a letter grade using INCLUSIVE thresholds.

    BUG: uses `>` instead of `>=`, so a score exactly on a boundary (e.g. 80) is
    scored one letter grade too low.
    """
    if score > 90:
        return "A"
    if score > 80:
        return "B"
    if score > 70:
        return "C"
    if score > 60:
        return "D"
    return "F"
