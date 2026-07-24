from tracker.cli import parse_args, run_command
from tracker.store import Status, TaskStore


def test_parse_args_add_basic() -> None:
    cmd = parse_args(["add", "Buy milk"])
    assert cmd.name == "add"
    assert cmd.title == "Buy milk"


def test_parse_args_list_with_status() -> None:
    cmd = parse_args(["list", "--status=pending"])
    assert cmd.status == Status.PENDING


def test_run_command_add_and_list_roundtrip() -> None:
    store = TaskStore()
    run_command(store, parse_args(["add", "Buy milk"]))
    tasks = run_command(store, parse_args(["list"]))
    assert [t.title for t in tasks] == ["Buy milk"]


def test_run_command_filters_by_status() -> None:
    store = TaskStore()
    id1 = run_command(store, parse_args(["add", "Buy milk"]))
    run_command(store, parse_args(["add", "Walk dog"]))
    store.mark_done(id1)
    pending = run_command(store, parse_args(["list", "--status=pending"]))
    assert [t.title for t in pending] == ["Walk dog"]


def test_add_with_priority_and_filter_by_it() -> None:
    store = TaskStore()
    run_command(store, parse_args(["add", "Urgent fix", "--priority=high"]))
    run_command(store, parse_args(["add", "Someday", "--priority=low"]))
    high_priority = run_command(store, parse_args(["list", "--priority=high"]))
    assert [t.title for t in high_priority] == ["Urgent fix"]
