"""Reference solution for bench fixture: feature-count-vowels.

Used only by `FakeSubject(scripted_effect="copy-solution")` (bench/subjects.py) --
overlay-copied onto the workspace repo, then the marker dir is removed so it never
reaches a real subject/grader. Never read by ClaudeCliSubject/AoWorkflowSubject.
"""

from __future__ import annotations

_VOWELS = frozenset("aeiouAEIOU")


def count_vowels(s: str) -> int:
    """Return the number of vowel characters (a, e, i, o, u; case-insensitive) in s."""
    return sum(1 for ch in s if ch in _VOWELS)
