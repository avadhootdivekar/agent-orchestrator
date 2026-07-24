"""Reference solution for bench fixture: bugfix-off-by-one.

Used only by `FakeSubject(scripted_effect="copy-solution")` (bench/subjects.py) --
overlay-copied onto the workspace repo, then the marker dir is removed so it never
reaches a real subject/grader. Never read by ClaudeCliSubject/AoWorkflowSubject.
"""

from __future__ import annotations


def running_total(nums: list[int]) -> list[int]:
    """Return the cumulative sum of ``nums``, one entry per input item."""
    totals: list[int] = []
    total = 0
    for i in range(len(nums)):
        total += nums[i]
        totals.append(total)
    return totals
