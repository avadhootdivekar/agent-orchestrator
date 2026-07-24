"""Integration tests for bench/runner.py: `run_suite` (T-Run5Tz acceptance criteria).

Network-free throughout: every test drives `FakeSubject`/`FakeGrader` (or a small local
subclass of one, registered over the real registry entry via `monkeypatch.setitem` so
it is automatically restored after each test). The one exception is the "happy path"
test, which pairs `FakeSubject` with a REAL `PytestGrader` over a real tiny fixture
(HLD §13's own integration test strategy: "fake subject writes a real solution file ->
real pytest passes") -- `uv run pytest`/network are never invoked, only a bounded
`sys.executable -m pytest` subprocess against an ephemeral fixture copy.

Workspaces are materialized under the REAL, gitignored `BENCH_WORKSPACE_ROOT` (the
runner does not allow overriding it -- mirrors `tests/bench/test_workspace.py`'s own
rationale: the path guard is defined against that real root). Every suite/subject id
here is namespaced with a per-test random suffix so concurrent/repeated test runs on
the same day never collide on `bench_run_id` (which embeds `suite.id`/`subject.id`) --
`run.json` itself is always written under a per-test `tmp_path` `out_dir`, never the
committed `benchmarks/results/` tree.

A fixed, injected clock (CLAUDE.md determinism rule) is used everywhere so
`bench_run_id`/timestamps are reproducible across calls within a test (needed for the
resume/idempotency assertions); no test asserts on wall-clock deltas, only that
monotonic-measured durations are `>= 0`.
"""

from __future__ import annotations

import json
import logging
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from agent_orchestrator.bench import graders as graders_mod
from agent_orchestrator.bench import runner
from agent_orchestrator.bench import subjects as subjects_mod
from agent_orchestrator.bench.errors import BenchError, GraderError, SubjectError
from agent_orchestrator.bench.registries import GRADER_REGISTRY, SUBJECT_REGISTRY
from agent_orchestrator.bench.spec import (
    BenchSuite,
    BenchTask,
    GraderConfig,
    SubjectSpec,
    load_subject,
    load_suite,
)
from agent_orchestrator.bench.subjects import RunContext, SubjectResult
from agent_orchestrator.bench.workspace import BENCH_WORKSPACE_ROOT

_FIXED_NOW = datetime(2026, 7, 22, 12, 0, 0, tzinfo=UTC)


def _fixed_clock() -> datetime:
    return _FIXED_NOW


# ---------------------------------------------------------------------------
# Local fixture builders (FakeGrader-friendly -- suite_factory/subject_factory from
# tests/bench/conftest.py already materialize a placeholder instruction/fixture per
# task, which is all a `fake`-typed grader needs).
# ---------------------------------------------------------------------------


def _fake_task(
    task_id: str,
    *,
    grader: dict[str, Any] | None = None,
    timeout_seconds: int | None = 60,
) -> dict[str, Any]:
    task: dict[str, Any] = {
        "id": task_id,
        "category": "bugfix",
        "instruction": f"tasks/{task_id}/instruction.md",
        "fixture": f"tasks/{task_id}/fixture",
        "grader": grader or {"type": "fake"},
        "tags": [],
    }
    # The schema types `timeout_seconds` as `integer` (no `null` variant) -- omit the
    # key entirely to mean "unset", rather than sending a `None` that fails validation.
    if timeout_seconds is not None:
        task["timeout_seconds"] = timeout_seconds
    return task


def _write_solvable_pytest_task(base: Path, task_id: str) -> dict[str, Any]:
    """A task whose fixture has a real, failing pytest test plus a `.bench-solution/`
    overlay that fixes it -- `FakeSubject(scripted_effect="copy-solution")` applies the
    overlay, then a REAL `PytestGrader` subprocess proves the fix (HLD §13).
    """
    task_dir = base / "tasks" / task_id
    (task_dir / "fixture").mkdir(parents=True, exist_ok=True)
    (task_dir / "instruction.md").write_text(f"# Fix add() for {task_id}\n")
    (task_dir / "fixture" / "broken.py").write_text("def add(a, b):\n    return a - b\n")
    (task_dir / "fixture" / "test_broken.py").write_text(
        "from broken import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n"
    )
    solution_dir = task_dir / "fixture" / ".bench-solution"
    solution_dir.mkdir(parents=True, exist_ok=True)
    (solution_dir / "broken.py").write_text("def add(a, b):\n    return a + b\n")
    return {
        "id": task_id,
        "category": "bugfix",
        "instruction": f"tasks/{task_id}/instruction.md",
        "fixture": f"tasks/{task_id}/fixture",
        "grader": {
            "type": "pytest",
            "command": f"{sys.executable} -m pytest -q --tb=no .",
            "cwd": ".",
        },
        "timeout_seconds": 60,
        "tags": [],
    }


def _write_suite(base: Path, suite_id: str, tasks: list[dict[str, Any]], **extra: Any) -> Path:
    data: dict[str, Any] = {
        "version": "1.0",
        "id": suite_id,
        "domain": "software",
        "description": "runner integration test suite",
        "tasks": tasks,
    }
    data.update(extra)
    path = base / "suite.json"
    path.write_text(json.dumps(data, indent=2))
    return path


def _write_fake_subject(base: Path, subject_id: str, **extra: Any) -> Path:
    data: dict[str, Any] = {"version": "1.0", "id": subject_id, "type": "fake"}
    data.update(extra)
    path = base / "subject.json"
    path.write_text(json.dumps(data, indent=2))
    return path


def _uniq() -> str:
    return uuid.uuid4().hex[:8]


# ---------------------------------------------------------------------------
# AC1 -- happy path: 3 tasks, id-sorted, real pytest grading, bench.task.end logged.
# ---------------------------------------------------------------------------


def test_run_suite_happy_path_three_tasks_sorted_order(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="agent_orchestrator")
    uniq = _uniq()
    suite_path = _write_suite(
        tmp_path,
        f"rt-happy-{uniq}",
        [
            _write_solvable_pytest_task(tmp_path, "c-task"),
            _write_solvable_pytest_task(tmp_path, "a-task"),
            _write_solvable_pytest_task(tmp_path, "b-task"),
        ],
    )
    subject_path = _write_fake_subject(
        tmp_path,
        f"fake-happy-{uniq}",
        scripted_effect="copy-solution",
        fake_cost=0.02,
        fake_tokens={"in": 10, "out": 5},
    )
    out_dir = tmp_path / "results"

    record = runner.run_suite(suite_path, subject_path, out_dir=out_dir, clock=_fixed_clock)

    assert [t.task_id for t in record.tasks] == ["a-task", "b-task", "c-task"]
    for t in record.tasks:
        assert t.subject_status == "succeeded"
        assert t.solved is True
        assert t.score == 1.0
        assert t.cost_usd == 0.02
        # AC7: workspace persists after the run (audit; no auto-delete).
        assert Path(t.workspace).exists()

    result_path = out_dir / record.bench_run_id / runner.RUN_JSON_FILENAME
    on_disk = json.loads(result_path.read_text())
    assert len(on_disk["tasks"]) == 3
    assert on_disk["schema_version"] == runner.RUN_RECORD_SCHEMA_VERSION
    assert on_disk["aggregate"]["solved"] == 3

    # AC7: the committed fixture itself is never mutated, only the workspace copy.
    original_broken = tmp_path / "tasks" / "a-task" / "fixture" / "broken.py"
    assert "a - b" in original_broken.read_text()

    task_end_events = [r for r in caplog.records if getattr(r, "event", None) == "bench.task.end"]
    assert len(task_end_events) == 3
    for r in task_end_events:
        assert r.solved is True
        assert r.cost_usd == 0.02
        assert r.wall_clock_seconds >= 0
        assert r.subject_status == "succeeded"

    run_start = [r for r in caplog.records if getattr(r, "event", None) == "bench.run.start"]
    run_end = [r for r in caplog.records if getattr(r, "event", None) == "bench.run.end"]
    assert len(run_start) == 1
    assert run_start[0].task_count == 3
    assert len(run_end) == 1
    assert run_end[0].solved == 3
    assert run_end[0].total == 3


# ---------------------------------------------------------------------------
# AC1 (order) -- execution order itself, decoupled from the always-sorted output list.
# ---------------------------------------------------------------------------


def test_run_suite_executes_tasks_in_sorted_id_order(
    tmp_path: Path, suite_factory: Any, subject_factory: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    uniq = _uniq()
    suite_path = suite_factory(
        suite_id=f"rt-order-{uniq}",
        tasks=[_fake_task("c-task"), _fake_task("a-task"), _fake_task("b-task")],
    )
    subject_path = subject_factory(subject_id=f"fake-order-{uniq}", type_="fake")

    call_order: list[str] = []

    class _OrderRecordingSubject(subjects_mod.FakeSubject):
        def run(self, task: BenchTask, ctx: RunContext) -> SubjectResult:
            call_order.append(task.id)
            return super().run(task, ctx)

    monkeypatch.setitem(SUBJECT_REGISTRY, "fake", _OrderRecordingSubject)

    runner.run_suite(suite_path, subject_path, out_dir=tmp_path / "results", clock=_fixed_clock)

    assert call_order == ["a-task", "b-task", "c-task"]


# ---------------------------------------------------------------------------
# AC2 -- resume skips completed (idempotent, run.json byte-unchanged); force re-runs.
# ---------------------------------------------------------------------------


def test_run_suite_resume_skips_completed_then_force_reruns(
    tmp_path: Path, suite_factory: Any, subject_factory: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    uniq = _uniq()
    suite_path = suite_factory(
        suite_id=f"rt-resume-{uniq}",
        tasks=[_fake_task("t1"), _fake_task("t2"), _fake_task("t3")],
    )
    subject_path = subject_factory(subject_id=f"fake-resume-{uniq}", type_="fake")
    out_dir = tmp_path / "results"

    class _CountingSubject(subjects_mod.FakeSubject):
        call_count = 0

        def run(self, task: BenchTask, ctx: RunContext) -> SubjectResult:
            type(self).call_count += 1
            return super().run(task, ctx)

    monkeypatch.setitem(SUBJECT_REGISTRY, "fake", _CountingSubject)

    first = runner.run_suite(suite_path, subject_path, out_dir=out_dir, clock=_fixed_clock)
    assert _CountingSubject.call_count == 3
    assert len(first.tasks) == 3

    result_path = out_dir / first.bench_run_id / runner.RUN_JSON_FILENAME
    before = result_path.read_text()

    second = runner.run_suite(suite_path, subject_path, out_dir=out_dir, clock=_fixed_clock)
    assert _CountingSubject.call_count == 3  # nothing re-invoked -- fully skipped
    assert result_path.read_text() == before  # AC2: run.json byte-unchanged
    assert len(second.tasks) == 3

    third = runner.run_suite(
        suite_path, subject_path, out_dir=out_dir, force=True, clock=_fixed_clock
    )
    assert _CountingSubject.call_count == 6  # all 3 re-run
    assert len(third.tasks) == 3


# ---------------------------------------------------------------------------
# AC3 -- "interrupt after task 2" (simulated via task_filter) -> resume runs only t3.
# ---------------------------------------------------------------------------


def test_run_suite_task_filter_then_resume_runs_remaining(
    tmp_path: Path, suite_factory: Any, subject_factory: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    uniq = _uniq()
    suite_path = suite_factory(
        suite_id=f"rt-filter-{uniq}",
        tasks=[_fake_task("t1"), _fake_task("t2"), _fake_task("t3")],
    )
    subject_path = subject_factory(subject_id=f"fake-filter-{uniq}", type_="fake")
    out_dir = tmp_path / "results"

    call_order: list[str] = []

    class _RecordingSubject(subjects_mod.FakeSubject):
        def run(self, task: BenchTask, ctx: RunContext) -> SubjectResult:
            call_order.append(task.id)
            return super().run(task, ctx)

    monkeypatch.setitem(SUBJECT_REGISTRY, "fake", _RecordingSubject)

    # "Interrupt after task 2": this call only considers t1/t2.
    first = runner.run_suite(
        suite_path, subject_path, out_dir=out_dir, task_filter=["t1", "t2"], clock=_fixed_clock
    )
    assert {t.task_id for t in first.tasks} == {"t1", "t2"}
    assert call_order == ["t1", "t2"]
    result_path = out_dir / first.bench_run_id / runner.RUN_JSON_FILENAME
    on_disk = json.loads(result_path.read_text())
    assert {t["task_id"] for t in on_disk["tasks"]} == {"t1", "t2"}

    # Resume: full suite, force=False -> only the un-recorded t3 runs.
    second = runner.run_suite(suite_path, subject_path, out_dir=out_dir, clock=_fixed_clock)
    assert call_order == ["t1", "t2", "t3"]
    assert {t.task_id for t in second.tasks} == {"t1", "t2", "t3"}


def test_run_suite_task_filter_matching_nothing_writes_no_file(
    tmp_path: Path, suite_factory: Any, subject_factory: Any
) -> None:
    uniq = _uniq()
    suite_path = suite_factory(suite_id=f"rt-empty-{uniq}", tasks=[_fake_task("t1")])
    subject_path = subject_factory(subject_id=f"fake-empty-{uniq}", type_="fake")
    out_dir = tmp_path / "results"

    record = runner.run_suite(
        suite_path,
        subject_path,
        out_dir=out_dir,
        task_filter=["does-not-exist"],
        clock=_fixed_clock,
    )

    assert record.tasks == []
    assert record.ended_at is None
    assert record.aggregate.total == 0
    result_path = out_dir / record.bench_run_id / runner.RUN_JSON_FILENAME
    assert not result_path.exists()


# ---------------------------------------------------------------------------
# AC4 -- one task's Subject.run raising never aborts the run (BenchError, then a
# genuinely unexpected exception); a Grader.grade failure is isolated the same way.
# ---------------------------------------------------------------------------


def test_run_suite_subject_bencherror_isolated_run_continues(
    tmp_path: Path, suite_factory: Any, subject_factory: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    uniq = _uniq()
    suite_path = suite_factory(
        suite_id=f"rt-crash-{uniq}",
        tasks=[_fake_task("t1"), _fake_task("t2"), _fake_task("t3")],
    )
    subject_path = subject_factory(subject_id=f"fake-crash-{uniq}", type_="fake")

    class _CrashingSubject(subjects_mod.FakeSubject):
        def run(self, task: BenchTask, ctx: RunContext) -> SubjectResult:
            if task.id == "t2":
                raise SubjectError("scripted crash for t2")
            return super().run(task, ctx)

    monkeypatch.setitem(SUBJECT_REGISTRY, "fake", _CrashingSubject)

    record = runner.run_suite(
        suite_path, subject_path, out_dir=tmp_path / "results", clock=_fixed_clock
    )

    assert len(record.tasks) == 3
    by_id = {t.task_id: t for t in record.tasks}
    assert by_id["t2"].subject_status == "error"
    assert "scripted crash for t2" in (by_id["t2"].raw_error or "")
    assert by_id["t1"].subject_status == "succeeded"
    assert by_id["t3"].subject_status == "succeeded"


def test_run_suite_subject_unexpected_exception_isolated_run_continues(
    tmp_path: Path, suite_factory: Any, subject_factory: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    uniq = _uniq()
    suite_path = suite_factory(
        suite_id=f"rt-unexpected-{uniq}",
        tasks=[_fake_task("t1"), _fake_task("t2")],
    )
    subject_path = subject_factory(subject_id=f"fake-unexpected-{uniq}", type_="fake")

    class _BuggySubject(subjects_mod.FakeSubject):
        def run(self, task: BenchTask, ctx: RunContext) -> SubjectResult:
            if task.id == "t2":
                raise ValueError("not a BenchError at all")
            return super().run(task, ctx)

    monkeypatch.setitem(SUBJECT_REGISTRY, "fake", _BuggySubject)

    record = runner.run_suite(
        suite_path, subject_path, out_dir=tmp_path / "results", clock=_fixed_clock
    )

    by_id = {t.task_id: t for t in record.tasks}
    assert by_id["t2"].subject_status == "error"
    assert "unexpected error" in (by_id["t2"].raw_error or "")
    assert by_id["t1"].subject_status == "succeeded"


def test_run_suite_grader_error_isolated_run_continues(
    tmp_path: Path, suite_factory: Any, subject_factory: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    uniq = _uniq()
    suite_path = suite_factory(
        suite_id=f"rt-gerr-{uniq}",
        tasks=[
            _fake_task("t1"),
            _fake_task("t2", grader={"type": "fake", "command": "raise"}),
            _fake_task("t3"),
        ],
    )
    subject_path = subject_factory(subject_id=f"fake-gerr-{uniq}", type_="fake")

    class _CrashingGrader(graders_mod.FakeGrader):
        def _grade(self, cfg: GraderConfig, ctx: Any) -> graders_mod.GradeResult:
            if cfg.command == "raise":
                raise GraderError("scripted grader crash")
            return super()._grade(cfg, ctx)

    monkeypatch.setitem(GRADER_REGISTRY, "fake", _CrashingGrader)

    record = runner.run_suite(
        suite_path, subject_path, out_dir=tmp_path / "results", clock=_fixed_clock
    )

    by_id = {t.task_id: t for t in record.tasks}
    assert by_id["t2"].subject_status == "error"
    assert "scripted grader crash" in (by_id["t2"].raw_error or "")
    assert by_id["t1"].subject_status == "succeeded"
    assert by_id["t1"].solved is True
    assert by_id["t3"].subject_status == "succeeded"


# ---------------------------------------------------------------------------
# Per-task persist -- "kill after task 1" simulation: run.json already has t1's
# metric on disk by the time t2's Subject.run is invoked (not a single batched write).
# ---------------------------------------------------------------------------


def test_run_suite_persists_after_each_task_not_batched(
    tmp_path: Path, suite_factory: Any, subject_factory: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    uniq = _uniq()
    suite_path = suite_factory(
        suite_id=f"rt-persist-{uniq}",
        tasks=[_fake_task("t1"), _fake_task("t2"), _fake_task("t3")],
    )
    subject_path = subject_factory(subject_id=f"fake-persist-{uniq}", type_="fake")
    out_dir = tmp_path / "results"

    loaded_suite = load_suite(suite_path)
    loaded_subject = load_subject(subject_path)
    bench_run_id = runner.compute_bench_run_id(loaded_suite, loaded_subject, _fixed_clock)
    result_path = (
        runner.resolve_result_dir(bench_run_id, out_dir=out_dir) / runner.RUN_JSON_FILENAME
    )

    seen_before_t2: dict[str, Any] = {}

    class _PeekingSubject(subjects_mod.FakeSubject):
        def run(self, task: BenchTask, ctx: RunContext) -> SubjectResult:
            if task.id == "t2":
                on_disk = json.loads(result_path.read_text())
                seen_before_t2["task_ids"] = {t["task_id"] for t in on_disk["tasks"]}
            return super().run(task, ctx)

    monkeypatch.setitem(SUBJECT_REGISTRY, "fake", _PeekingSubject)

    runner.run_suite(suite_path, subject_path, out_dir=out_dir, clock=_fixed_clock)

    assert seen_before_t2["task_ids"] == {"t1"}


# ---------------------------------------------------------------------------
# Timeout propagation: task-level wins; falls back to run-level default_timeout,
# then to the suite's own `defaults.timeout_seconds`.
# ---------------------------------------------------------------------------


def test_run_suite_timeout_propagation(
    tmp_path: Path, suite_factory: Any, subject_factory: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    uniq = _uniq()
    suite_path = suite_factory(
        suite_id=f"rt-timeout-{uniq}",
        tasks=[
            _fake_task("t-explicit", timeout_seconds=42),
            _fake_task("t-default", timeout_seconds=None),
        ],
    )
    subject_path = subject_factory(subject_id=f"fake-timeout-{uniq}", type_="fake")

    seen: dict[str, int] = {}

    class _RecordingSubject(subjects_mod.FakeSubject):
        def run(self, task: BenchTask, ctx: RunContext) -> SubjectResult:
            seen[task.id] = ctx.timeout_seconds
            return super().run(task, ctx)

    monkeypatch.setitem(SUBJECT_REGISTRY, "fake", _RecordingSubject)

    runner.run_suite(
        suite_path,
        subject_path,
        out_dir=tmp_path / "results",
        default_timeout=99,
        clock=_fixed_clock,
    )

    assert seen["t-explicit"] == 42  # task's own bound wins
    assert seen["t-default"] == 99  # falls back to the run-level override


def test_run_suite_timeout_falls_back_to_suite_defaults(
    tmp_path: Path, suite_factory: Any, subject_factory: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    uniq = _uniq()
    suite_path = suite_factory(
        suite_id=f"rt-sdef-{uniq}",
        tasks=[_fake_task("t1", timeout_seconds=None)],
        extra_top_level={"defaults": {"timeout_seconds": 77}},
    )
    subject_path = subject_factory(subject_id=f"fake-sdef-{uniq}", type_="fake")

    seen: dict[str, int] = {}

    class _RecordingSubject(subjects_mod.FakeSubject):
        def run(self, task: BenchTask, ctx: RunContext) -> SubjectResult:
            seen[task.id] = ctx.timeout_seconds
            return super().run(task, ctx)

    monkeypatch.setitem(SUBJECT_REGISTRY, "fake", _RecordingSubject)

    runner.run_suite(suite_path, subject_path, out_dir=tmp_path / "results", clock=_fixed_clock)

    assert seen["t1"] == 77


def test_run_suite_timeout_last_resort_builtin_default(
    tmp_path: Path, suite_factory: Any, subject_factory: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    uniq = _uniq()
    suite_path = suite_factory(
        suite_id=f"rt-builtin-{uniq}", tasks=[_fake_task("t1", timeout_seconds=None)]
    )
    subject_path = subject_factory(subject_id=f"fake-builtin-{uniq}", type_="fake")

    seen: dict[str, int] = {}

    class _RecordingSubject(subjects_mod.FakeSubject):
        def run(self, task: BenchTask, ctx: RunContext) -> SubjectResult:
            seen[task.id] = ctx.timeout_seconds
            return super().run(task, ctx)

    monkeypatch.setitem(SUBJECT_REGISTRY, "fake", _RecordingSubject)

    runner.run_suite(suite_path, subject_path, out_dir=tmp_path / "results", clock=_fixed_clock)

    assert seen["t1"] == runner.DEFAULT_TASK_TIMEOUT_SECONDS


# ---------------------------------------------------------------------------
# Path guard: every recorded workspace stays under BENCH_WORKSPACE_ROOT.
# ---------------------------------------------------------------------------


def test_run_suite_workspaces_stay_under_bench_workspace_root(
    tmp_path: Path, suite_factory: Any, subject_factory: Any
) -> None:
    uniq = _uniq()
    suite_path = suite_factory(
        suite_id=f"rt-guard-{uniq}", tasks=[_fake_task("t1"), _fake_task("t2")]
    )
    subject_path = subject_factory(subject_id=f"fake-guard-{uniq}", type_="fake")

    record = runner.run_suite(
        suite_path, subject_path, out_dir=tmp_path / "results", clock=_fixed_clock
    )

    root = BENCH_WORKSPACE_ROOT.resolve()
    assert record.tasks  # sanity: the suite actually ran
    for t in record.tasks:
        capture_path = Path(t.workspace).resolve()
        assert capture_path == root or root in capture_path.parents


# ---------------------------------------------------------------------------
# Upfront config errors: unknown subject type, corrupt existing run.json.
# ---------------------------------------------------------------------------


def test_run_suite_unregistered_subject_type_raises(
    tmp_path: Path, suite_factory: Any, subject_factory: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    uniq = _uniq()
    suite_path = suite_factory(suite_id=f"rt-unreg-{uniq}", tasks=[_fake_task("t1")])
    subject_path = subject_factory(subject_id=f"fake-unreg-{uniq}", type_="fake")

    monkeypatch.delitem(SUBJECT_REGISTRY, "fake")

    with pytest.raises(BenchError, match="No Subject registered"):
        runner.run_suite(suite_path, subject_path, out_dir=tmp_path / "results", clock=_fixed_clock)


def test_run_suite_corrupt_existing_run_json_raises(
    tmp_path: Path, suite_factory: Any, subject_factory: Any
) -> None:
    uniq = _uniq()
    suite_path = suite_factory(suite_id=f"rt-corrupt-{uniq}", tasks=[_fake_task("t1")])
    subject_path = subject_factory(subject_id=f"fake-corrupt-{uniq}", type_="fake")
    out_dir = tmp_path / "results"

    loaded_suite = load_suite(suite_path)
    loaded_subject = load_subject(subject_path)
    bench_run_id = runner.compute_bench_run_id(loaded_suite, loaded_subject, _fixed_clock)
    result_dir = runner.resolve_result_dir(bench_run_id, out_dir=out_dir)
    result_dir.mkdir(parents=True)
    (result_dir / runner.RUN_JSON_FILENAME).write_text("{not valid json")

    with pytest.raises(BenchError, match="Failed to read existing run record"):
        runner.run_suite(suite_path, subject_path, out_dir=out_dir, clock=_fixed_clock)


# ---------------------------------------------------------------------------
# AC6 -- config_fingerprint stability.
# ---------------------------------------------------------------------------


def test_relativize_golden_branches(tmp_path: Path) -> None:
    """Unit coverage for `_relativize_golden`'s three branches directly (W2 helper):
    already-relative input passes through untouched; an absolute path under
    `suite_dir` is relativized; an absolute path OUTSIDE `suite_dir` falls back to
    just the basename."""
    suite_dir = tmp_path / "suite-dir"
    suite_dir.mkdir()

    assert runner._relativize_golden("golden/out.txt", suite_dir) == "golden/out.txt"

    under = suite_dir / "golden" / "out.txt"
    assert runner._relativize_golden(str(under), suite_dir) == "golden/out.txt"

    outside = tmp_path / "elsewhere" / "out.txt"
    assert runner._relativize_golden(str(outside), suite_dir) == "out.txt"


def test_task_config_fingerprint_stable_and_sensitive_to_changes(tmp_path: Path) -> None:
    task = BenchTask(
        id="t1",
        category="bugfix",
        instruction="i.md",
        fixture="fx",
        grader=GraderConfig(type="fake"),
    )
    other_task = task.model_copy(update={"id": "t2"})
    subject = SubjectSpec(version="1.0", id="s1", type="fake")
    other_subject = subject.model_copy(update={"id": "s2"})

    fp1 = runner._task_config_fingerprint(task, subject, "ao-0.1.0", tmp_path)
    fp2 = runner._task_config_fingerprint(task, subject, "ao-0.1.0", tmp_path)
    assert fp1 == fp2  # stable across identical (task, subject, ao_version, suite_dir)

    assert runner._task_config_fingerprint(other_task, subject, "ao-0.1.0", tmp_path) != fp1
    assert runner._task_config_fingerprint(task, other_subject, "ao-0.1.0", tmp_path) != fp1
    assert runner._task_config_fingerprint(task, subject, "ao-0.2.0", tmp_path) != fp1


# ---------------------------------------------------------------------------
# W2 regression -- config_fingerprint must be checkout-path-independent even though
# `load_suite` rewrites an `equals_file` assertion's `golden` to an ABSOLUTE path.
# ---------------------------------------------------------------------------


def _write_equals_file_task_suite(base: Path, suite_id: str, task_id: str = "t1") -> Path:
    """A minimal, self-contained suite with one `file_assertion`/`equals_file` task,
    materialized fully under *base* (instruction, fixture, golden) -- mirrors
    `conftest.py`'s `suite_factory` shape but written directly so this can be called
    twice against two independent directory trees (two "checkouts")."""
    (base / "fixture").mkdir(parents=True)
    (base / "fixture" / "placeholder.txt").write_text("fixture content\n")
    (base / "instruction.md").write_text("# fix\n")
    golden_dir = base / "golden"
    golden_dir.mkdir(parents=True)
    (golden_dir / "out.txt").write_text("golden content\n")

    task = {
        "id": task_id,
        "category": "test",
        "instruction": "instruction.md",
        "fixture": "fixture",
        "grader": {
            "type": "file_assertion",
            "assertions": [{"type": "equals_file", "path": "out.txt", "golden": "golden/out.txt"}],
        },
    }
    return _write_suite(base, suite_id, [task])


def test_task_config_fingerprint_checkout_path_independent(tmp_path: Path) -> None:
    """The regression the reviewer reproduced: byte-identical suites (same
    `equals_file` golden reference) checked out under two different absolute paths
    must hash to the SAME per-task `config_fingerprint`, even though `load_suite`
    rewrites `golden` to an absolute (and thus checkout-path-dependent) path.
    """
    uniq = _uniq()
    checkout_a = tmp_path / "checkout-a"
    checkout_b = tmp_path / "checkout-b-a-longer-dirname"
    checkout_a.mkdir()
    checkout_b.mkdir()

    suite_path_a = _write_equals_file_task_suite(checkout_a, f"fp-checkout-{uniq}")
    suite_path_b = _write_equals_file_task_suite(checkout_b, f"fp-checkout-{uniq}")

    suite_a = load_suite(suite_path_a)
    suite_b = load_suite(suite_path_b)
    golden_a = suite_a.tasks[0].grader.assertions[0].golden
    golden_b = suite_b.tasks[0].grader.assertions[0].golden
    assert golden_a is not None and golden_b is not None
    # Sanity: load_suite really did rewrite `golden` to two DIFFERENT absolute paths
    # (otherwise this test would not be exercising the regression at all).
    assert golden_a != golden_b

    subject = SubjectSpec(version="1.0", id=f"s-{uniq}", type="fake")
    fp_a = runner._task_config_fingerprint(
        suite_a.tasks[0], subject, "ao-0.1.0", suite_path_a.resolve().parent
    )
    fp_b = runner._task_config_fingerprint(
        suite_b.tasks[0], subject, "ao-0.1.0", suite_path_b.resolve().parent
    )
    assert fp_a == fp_b


def test_run_suite_task_fingerprint_checkout_path_independent_end_to_end(
    tmp_path: Path,
) -> None:
    """Same regression, exercised through the full `run_suite` entrypoint rather than
    calling `_task_config_fingerprint` directly -- proves `suite_base_dir` is actually
    threaded through from `run_suite` into the fingerprint computation.
    """
    uniq = _uniq()
    checkout_a = tmp_path / "checkout-a"
    checkout_b = tmp_path / "checkout-b-a-longer-dirname"
    checkout_a.mkdir()
    checkout_b.mkdir()

    suite_path_a = _write_equals_file_task_suite(checkout_a, f"fp-e2e-{uniq}")
    suite_path_b = _write_equals_file_task_suite(checkout_b, f"fp-e2e-{uniq}")
    subject_path_a = _write_fake_subject(checkout_a, f"fp-e2e-subj-{uniq}")
    subject_path_b = _write_fake_subject(checkout_b, f"fp-e2e-subj-{uniq}")

    record_a = runner.run_suite(
        suite_path_a, subject_path_a, out_dir=tmp_path / "results-a", clock=_fixed_clock
    )
    record_b = runner.run_suite(
        suite_path_b, subject_path_b, out_dir=tmp_path / "results-b", clock=_fixed_clock
    )

    assert record_a.tasks[0].config_fingerprint == record_b.tasks[0].config_fingerprint


# ---------------------------------------------------------------------------
# W4 -- same-day resume with a changed subject/suite config must not silently
# overwrite run-level config_fingerprint; force=True re-runs everything instead.
# ---------------------------------------------------------------------------


def test_run_suite_resume_after_subject_config_change_raises_typed_error(
    tmp_path: Path, suite_factory: Any, subject_factory: Any
) -> None:
    uniq = _uniq()
    suite_path = suite_factory(
        suite_id=f"rt-cfgchg-{uniq}", tasks=[_fake_task("t1"), _fake_task("t2")]
    )
    subject_path = subject_factory(
        subject_id=f"fake-cfgchg-{uniq}", type_="fake", extra={"model": "model-a"}
    )
    out_dir = tmp_path / "results"

    first = runner.run_suite(suite_path, subject_path, out_dir=out_dir, clock=_fixed_clock)
    assert len(first.tasks) == 2

    # Mutate the subject spec ON DISK (same bench_run_id: same suite/subject id, same
    # UTC day) -- the run-level config_fingerprint recomputed on the next call must no
    # longer match what's already on record.
    data = json.loads(subject_path.read_text())
    data["model"] = "model-b"
    subject_path.write_text(json.dumps(data))

    with pytest.raises(BenchError, match="force"):
        runner.run_suite(suite_path, subject_path, out_dir=out_dir, clock=_fixed_clock)

    # The stale record on disk must be left untouched by the rejected resume attempt.
    result_path = out_dir / first.bench_run_id / runner.RUN_JSON_FILENAME
    on_disk = json.loads(result_path.read_text())
    assert on_disk["subject"]["resolved_config"]["model"] == "model-a"


def test_run_suite_resume_after_subject_config_change_force_reruns_all(
    tmp_path: Path, suite_factory: Any, subject_factory: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    uniq = _uniq()
    suite_path = suite_factory(
        suite_id=f"rt-cfgfrc-{uniq}", tasks=[_fake_task("t1"), _fake_task("t2")]
    )
    subject_path = subject_factory(
        subject_id=f"fake-cfgfrc-{uniq}", type_="fake", extra={"model": "model-a"}
    )
    out_dir = tmp_path / "results"

    class _CountingSubject(subjects_mod.FakeSubject):
        call_count = 0

        def run(self, task: BenchTask, ctx: RunContext) -> SubjectResult:
            type(self).call_count += 1
            return super().run(task, ctx)

    monkeypatch.setitem(SUBJECT_REGISTRY, "fake", _CountingSubject)

    first = runner.run_suite(suite_path, subject_path, out_dir=out_dir, clock=_fixed_clock)
    assert _CountingSubject.call_count == 2

    data = json.loads(subject_path.read_text())
    data["model"] = "model-b"
    subject_path.write_text(json.dumps(data))

    # force=True bypasses the mismatch check entirely and re-runs every task under
    # the new config -- succeeds, and the record now reflects the new model.
    second = runner.run_suite(
        suite_path, subject_path, out_dir=out_dir, force=True, clock=_fixed_clock
    )
    assert _CountingSubject.call_count == 4  # both tasks re-invoked
    assert len(second.tasks) == 2
    assert second.subject.model == "model-b"
    assert second.config_fingerprint != first.config_fingerprint


def test_run_suite_resume_without_config_change_still_resumes_normally(
    tmp_path: Path, suite_factory: Any, subject_factory: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sanity companion to the mismatch tests above: an unmodified suite/subject
    resumes exactly as before W4 (no false positive on an unchanged config)."""
    uniq = _uniq()
    suite_path = suite_factory(
        suite_id=f"rt-cfgok-{uniq}", tasks=[_fake_task("t1"), _fake_task("t2")]
    )
    subject_path = subject_factory(subject_id=f"fake-cfgok-{uniq}", type_="fake")
    out_dir = tmp_path / "results"

    class _CountingSubject(subjects_mod.FakeSubject):
        call_count = 0

        def run(self, task: BenchTask, ctx: RunContext) -> SubjectResult:
            type(self).call_count += 1
            return super().run(task, ctx)

    monkeypatch.setitem(SUBJECT_REGISTRY, "fake", _CountingSubject)

    runner.run_suite(suite_path, subject_path, out_dir=out_dir, clock=_fixed_clock)
    assert _CountingSubject.call_count == 2

    second = runner.run_suite(suite_path, subject_path, out_dir=out_dir, clock=_fixed_clock)
    assert _CountingSubject.call_count == 2  # untouched: fully skipped, no mismatch
    assert len(second.tasks) == 2


def test_run_config_fingerprint_stable_and_sensitive_to_changes() -> None:
    suite = BenchSuite(version="1.0", id="s1", domain="software", tasks=[])
    other_suite = suite.model_copy(update={"version": "1.1"})
    subject = SubjectSpec(version="1.0", id="sub1", type="fake")

    overrides = {"budget_total": None, "max_turns": None, "default_timeout": None}
    fp1 = runner._run_config_fingerprint(suite, subject, overrides, "ao-0.1.0", "claude-1.0")
    fp2 = runner._run_config_fingerprint(suite, subject, overrides, "ao-0.1.0", "claude-1.0")
    assert fp1 == fp2

    assert (
        runner._run_config_fingerprint(other_suite, subject, overrides, "ao-0.1.0", "claude-1.0")
        != fp1
    )
    other_overrides = {"budget_total": 5, "max_turns": None, "default_timeout": None}
    assert (
        runner._run_config_fingerprint(suite, subject, other_overrides, "ao-0.1.0", "claude-1.0")
        != fp1
    )
