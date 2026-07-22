"""In-memory task store (bench fixture: feature-tracker-priority-filter)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Status(Enum):
    PENDING = "pending"
    DONE = "done"


class Priority(Enum):
    """Deliberately a plain `Enum` (not a `str` subclass): a raw string like `"high"`
    is NOT equal to `Priority.HIGH` unless it is explicitly converted via
    `Priority(value)` first. Any layer that skips that conversion and compares/sorts
    raw strings against `Task.priority` will silently misbehave -- see
    instruction.md.
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


# Sort weight: lower number sorts first (highest priority first).
PRIORITY_SORT_WEIGHT: dict[Priority, int] = {
    Priority.HIGH: 0,
    Priority.MEDIUM: 1,
    Priority.LOW: 2,
}


@dataclass
class Task:
    id: int
    title: str
    status: Status = Status.PENDING
    priority: Priority = Priority.MEDIUM


class TaskStore:
    """In-memory task storage. Ordering/filtering by priority is NOT this class's
    job -- see `tracker.filters.apply_filters`, which is what `tracker.cli.run_command`
    calls after fetching `all()`.
    """

    def __init__(self) -> None:
        self._tasks: dict[int, Task] = {}
        self._next_id = 1

    def add(self, title: str, priority: Priority = Priority.MEDIUM) -> int:
        task_id = self._next_id
        self._next_id += 1
        self._tasks[task_id] = Task(id=task_id, title=title, priority=priority)
        return task_id

    def mark_done(self, task_id: int) -> None:
        self._tasks[task_id].status = Status.DONE

    def all(self) -> list[Task]:
        """Every task, ordered by id ascending (insertion order)."""
        return sorted(self._tasks.values(), key=lambda t: t.id)
