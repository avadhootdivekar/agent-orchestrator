"""Tiny stdlib-only stats helpers (bench fixture: bugfix-off-by-one)."""

from __future__ import annotations


def running_total(nums: list[int]) -> list[int]:
    """Return the cumulative sum of ``nums``, one entry per input item.

    BUG: the loop bound is ``len(nums) - 1``, so the last item of ``nums`` never
    gets added — the result is one element short of the input.
    """
    totals: list[int] = []
    total = 0
    for i in range(len(nums) - 1):
        total += nums[i]
        totals.append(total)
    return totals
