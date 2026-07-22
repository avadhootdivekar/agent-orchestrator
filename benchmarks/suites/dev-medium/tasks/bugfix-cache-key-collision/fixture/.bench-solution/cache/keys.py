"""Reference solution for bench fixture: bugfix-cache-key-collision.

Used only by `FakeSubject(scripted_effect="copy-solution")` (bench/subjects.py) --
overlay-copied onto the workspace repo, then the marker dir is removed so it never
reaches a real subject/grader. Never read by ClaudeCliSubject/AoWorkflowSubject.
"""

from __future__ import annotations


def normalize_key(key: str) -> str:
    """Normalize `key` for case-insensitive, surrounding-whitespace-tolerant lookups.

    Only SURROUNDING whitespace is stripped -- internal whitespace is part of the
    key's identity (the root-cause bug this fixture ships with removed ALL whitespace,
    silently colliding distinct keys like "alice" and "ali ce").
    """
    return key.strip().lower()
