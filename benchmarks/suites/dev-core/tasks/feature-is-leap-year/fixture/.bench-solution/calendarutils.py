"""Reference solution for bench fixture: feature-is-leap-year.

Used only by `FakeSubject(scripted_effect="copy-solution")` (bench/subjects.py) --
overlay-copied onto the workspace repo, then the marker dir is removed so it never
reaches a real subject/grader. Never read by ClaudeCliSubject/AoWorkflowSubject.
"""

from __future__ import annotations


def is_leap_year(year: int) -> bool:
    """Return True if `year` is a Gregorian leap year."""
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
