"""Task filtering/sorting (bench fixture: feature-tracker-priority-filter).

`apply_filters` runs AFTER `TaskStore.all()` returns its (id-ordered) list; `cli.py`'s
`run_command` decides which filters/sort to apply based on the parsed `Command`.
"""

from __future__ import annotations

from .store import Priority, Status, Task


def apply_filters(
    tasks: list[Task],
    *,
    status: Status | None = None,
    priority: Priority | None = None,
    sort: str | None = None,
) -> list[Task]:
    """Filter `tasks` by `status`/`priority` (either may be `None` to skip that
    filter) and optionally sort the result.

    TODO (this task):
      - `priority` filtering is not implemented yet -- every task passes regardless
        of `priority`.
      - `sort == "priority"` is not honored yet -- the result stays in whatever
        order it arrived in (id order, from `TaskStore.all()`). When honored, it
        must order highest priority first, ties broken by `id` ascending (see
        `tracker.store.PRIORITY_SORT_WEIGHT`).
    """
    result = list(tasks)
    if status is not None:
        result = [t for t in result if t.status == status]
    return result
