"""Reference solution for bench fixture: feature-tracker-priority-filter.

Used only by `FakeSubject(scripted_effect="copy-solution")` (bench/subjects.py) --
overlay-copied onto the workspace repo, then the marker dir is removed so it never
reaches a real subject/grader. Never read by ClaudeCliSubject/AoWorkflowSubject.
"""

from __future__ import annotations

from dataclasses import dataclass

from .filters import apply_filters
from .store import Priority, Status, Task, TaskStore


@dataclass
class Command:
    name: str  # "add" | "list"
    title: str | None = None
    status: Status | None = None
    priority: Priority | None = None
    sort: str | None = None


def _parse_option(arg: str) -> tuple[str, str]:
    key, _, value = arg.partition("=")
    return key.lstrip("-"), value


def parse_args(argv: list[str]) -> Command:
    if not argv:
        raise ValueError("no command given")
    name = argv[0]

    if name == "add":
        if len(argv) < 2:
            raise ValueError("add requires a title")
        cmd = Command(name="add", title=argv[1])
        for arg in argv[2:]:
            key, value = _parse_option(arg)
            if key == "priority":
                cmd.priority = Priority(value)
        return cmd

    if name == "list":
        cmd = Command(name="list")
        for arg in argv[1:]:
            key, value = _parse_option(arg)
            if key == "status":
                cmd.status = Status(value)
            elif key == "priority":
                cmd.priority = Priority(value)
            elif key == "sort":
                cmd.sort = value
        return cmd

    raise ValueError(f"unknown command: {name!r}")


def run_command(store: TaskStore, cmd: Command) -> int | list[Task]:
    if cmd.name == "add":
        priority = cmd.priority if cmd.priority is not None else Priority.MEDIUM
        return store.add(cmd.title or "", priority=priority)
    if cmd.name == "list":
        return apply_filters(store.all(), status=cmd.status, priority=cmd.priority, sort=cmd.sort)
    raise ValueError(f"unknown command: {cmd.name!r}")
