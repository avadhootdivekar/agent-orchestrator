"""Reference solution for bench fixture: refactor-extract-validation.

Used only by `FakeSubject(scripted_effect="copy-solution")` (bench/subjects.py) --
overlay-copied onto the workspace repo, then the marker dir is removed so it never
reaches a real subject/grader. Never read by ClaudeCliSubject/AoWorkflowSubject.
"""

from __future__ import annotations


def _validate_non_negative(*values: float) -> None:
    if any(v < 0 for v in values):
        raise ValueError("dimensions must be non-negative")


def rectangle_area(w: float, h: float) -> float:
    _validate_non_negative(w, h)
    return w * h


def triangle_area(base: float, height: float) -> float:
    _validate_non_negative(base, height)
    return 0.5 * base * height
