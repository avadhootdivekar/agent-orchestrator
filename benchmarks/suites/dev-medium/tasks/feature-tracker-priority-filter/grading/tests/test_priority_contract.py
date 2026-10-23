"""Held-out grading tests for feature-tracker-priority-filter -- NOT part of the
visible tests/ suite an agent sees. Checks the priority contract end to end beyond the
single-filter case tests/test_tracker.py exercises: sort ordering, combined filters, a
malformed CLI value, and that the stored priority is a real `Priority` enum member (not
a raw string that happens to satisfy the one visible equality check).
"""

from __future__ import annotations

import pytest
from tracker.cli import parse_args, run_command
from tracker.store import Priority, TaskStore


def test_list_sort_by_priority_orders_high_to_low_with_id_tiebreak() -> None:
    store = TaskStore()
    run_command(store, parse_args(["add", "Low task", "--priority=low"]))
    run_command(store, parse_args(["add", "High task 1", "--priority=high"]))
    run_command(store, parse_args(["add", "Medium task", "--priority=medium"]))
    run_command(store, parse_args(["add", "High task 2", "--priority=high"]))

    ordered = run_command(store, parse_args(["list", "--sort=priority"]))
    assert [t.title for t in ordered] == [
        "High task 1",
        "High task 2",
        "Medium task",
        "Low task",
    ]


def test_combined_status_and_priority_filters() -> None:
    store = TaskStore()
    id1 = run_command(store, parse_args(["add", "A", "--priority=high"]))
    run_command(store, parse_args(["add", "B", "--priority=high"]))
    store.mark_done(id1)

    pending_high = run_command(store, parse_args(["list", "--status=pending", "--priority=high"]))
    assert [t.title for t in pending_high] == ["B"]


def test_invalid_priority_value_raises() -> None:
    with pytest.raises(ValueError):
        parse_args(["add", "X", "--priority=urgent"])


def test_added_priority_is_a_real_priority_enum_member() -> None:
    """Guards against a shallow fix that stores the raw CLI string instead of
    converting it into `tracker.store.Priority` -- sorting/filtering by priority
    depends on every stored task's `.priority` actually being a `Priority` member.
    """
    store = TaskStore()
    run_command(store, parse_args(["add", "A", "--priority=high"]))
    (task,) = run_command(store, parse_args(["list"]))
    assert isinstance(task.priority, Priority)
    assert task.priority is Priority.HIGH
