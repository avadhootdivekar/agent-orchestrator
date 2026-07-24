"""Tiny argv-style command parser + dispatcher (bench fixture:
feature-tracker-priority-filter).

Parses a list of string args (e.g. `["list", "--status=pending"]`, never real
`sys.argv` -- kept as a plain list so it's directly unit-testable) into a `Command`,
then `run_command` executes it against a `TaskStore`. `--status` is wired end to end
already for both `add` (well, `add` never took a status) and `list`; `--priority`
(on both `add` and `list`) and `--sort=priority` (on `list`) are NOT wired yet -- this
task's job. See instruction.md.
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
    priority: Priority | None = None  # TODO: never populated yet, see parse_args
    sort: str | None = None  # TODO: never populated yet, see parse_args


def _parse_option(arg: str) -> tuple[str, str]:
    key, _, value = arg.partition("=")
    return key.lstrip("-"), value


def parse_args(argv: list[str]) -> Command:
    """Parse `argv` into a `Command`. Raises `ValueError` for an empty/unknown
    command, an `add` with no title, or an option value that fails to convert
    (e.g. `--status=bogus`, `--priority=urgent`)."""
    if not argv:
        raise ValueError("no command given")
    name = argv[0]

    if name == "add":
        if len(argv) < 2:
            raise ValueError("add requires a title")
        cmd = Command(name="add", title=argv[1])
        # TODO: argv[2:] (e.g. "--priority=high") is silently dropped -- `add` never
        # accepts a priority today, so every added task defaults to Priority.MEDIUM
        # regardless of what the caller asked for.
        return cmd

    if name == "list":
        cmd = Command(name="list")
        for arg in argv[1:]:
            key, value = _parse_option(arg)
            if key == "status":
                cmd.status = Status(value)
            # TODO: "priority" and "sort" keys are never recognized here -- both are
            # silently ignored instead of populating cmd.priority / cmd.sort.
        return cmd

    raise ValueError(f"unknown command: {name!r}")


def run_command(store: TaskStore, cmd: Command) -> int | list[Task]:
    """Execute `cmd` against `store`. Returns the new task's id for `add`, or the
    (filtered/sorted) task list for `list`."""
    if cmd.name == "add":
        priority = cmd.priority if cmd.priority is not None else Priority.MEDIUM
        return store.add(cmd.title or "", priority=priority)
    if cmd.name == "list":
        return apply_filters(store.all(), status=cmd.status, priority=cmd.priority, sort=cmd.sort)
    raise ValueError(f"unknown command: {cmd.name!r}")
