"""Reference solution for bench fixture: feature-tracker-priority-filter.

Used only by `FakeSubject(scripted_effect="copy-solution")` (bench/subjects.py) --
overlay-copied onto the workspace repo, then the marker dir is removed so it never
reaches a real subject/grader. Never read by ClaudeCliSubject/AoWorkflowSubject.
"""

from __future__ import annotations

from .store import PRIORITY_SORT_WEIGHT, Priority, Status, Task


def apply_filters(
    tasks: list[Task],
    *,
    status: Status | None = None,
    priority: Priority | None = None,
    sort: str | None = None,
) -> list[Task]:
    result = list(tasks)
    if status is not None:
        result = [t for t in result if t.status == status]
    if priority is not None:
        result = [t for t in result if t.priority == priority]
    if sort == "priority":
        # Stable sort: ties (same priority) keep their current relative order, which
        # is id-ascending since `tasks` arrives from `TaskStore.all()` in that order.
        result = sorted(result, key=lambda t: PRIORITY_SORT_WEIGHT[t.priority])
    return result
