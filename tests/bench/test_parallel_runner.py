"""Integration tests for bounded task-level parallelism in bench/runner.py (T-Pl3Rx7,
ADR-0009 D4: `max_parallel` opt-in `ThreadPoolExecutor`, one lock guarding
`tasks_dict`/running-cost/`run.json` persist, id-sorted persisted output, bounded
budget overshoot -- see `tests/bench/test_budget.py::TestBudgetUnderConcurrency` for
the budget x concurrency interaction specifically).

Network-free throughout: every test drives a local subclass of `FakeSubject`
(monkeypatched over the `"fake"` registry entry, auto-restored after each test) --
mirrors the pattern already used across `tests/bench/test_runner.py` and
`tests/bench/test_budget.py`. No sleeps-as-synchronization for correctness assertions:
`threading.Barrier`/`threading.Event` force deterministic overlap. The one exception is
the trailing `@pytest.mark.perf` wall-clock sanity check, which legitimately needs a
real scripted delay to demonstrate genuine concurrency speedup.

A fixed, injected clock (CLAUDE.md determinism rule) is used everywhere.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

import pytest

from agent_orchestrator.bench import runner
from agent_orchestrator.bench import subjects as subjects_mod
from agent_orchestrator.bench.errors import SubjectError
from agent_orchestrator.bench.registries import SUBJECT_REGISTRY
from agent_orchestrator.bench.runner import BenchRunRecord, BenchTaskRecord
from agent_orchestrator.bench.spec import BenchTask
from agent_orchestrator.bench.subjects import RunContext, SubjectResult

_FIXED_NOW = datetime(2026, 7, 22, 12, 0, 0, tzinfo=UTC)


def _fixed_clock() -> datetime:
    return _FIXED_NOW


def _uniq() -> str:
    return uuid.uuid4().hex[:8]


def _fake_task(task_id: str) -> dict[str, Any]:
    return {
        "id": task_id,
        "category": "bugfix",
        "instruction": f"tasks/{task_id}/instruction.md",
        "fixture": f"tasks/{task_id}/fixture",
        "grader": {"type": "fake"},
        "timeout_seconds": 60,
        "tags": [],
    }


# Fields a task record is expected to hold IDENTICALLY regardless of worker count --
# excludes wall-clock/timestamps/workspace path (out_dir differs between the two runs
# being compared here, and timing is expected to differ under concurrency).
_TIMING_INDEPENDENT_FIELDS = (
    "subject_id",
    "task_id",
    "domain",
    "category",
    "solved",
    "score",
    "cost_usd",
    "input_tokens",
    "output_tokens",
    "attempts",
    "turns",
    "subject_status",
    "grader_type",
    "config_fingerprint",
    "raw_error",
    "grader_detail",
    "grader_raw_tail",
)


def _comparable(records: list[BenchTaskRecord]) -> list[dict[str, Any]]:
    return sorted(
        ({f: getattr(t, f) for f in _TIMING_INDEPENDENT_FIELDS} for t in records),
        key=lambda d: str(d["task_id"]),
    )


# ---------------------------------------------------------------------------
# Bounded concurrency + result parity with a serial run of the same suite.
# ---------------------------------------------------------------------------


def test_parallel_bounded_concurrency_and_result_parity_with_serial(
    tmp_path: Path, suite_factory: Any, subject_factory: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    uniq = _uniq()
    max_parallel = 4
    task_ids = [f"t{i}" for i in range(1, 9)]  # 8 tasks -> exactly 2 rounds of 4
    suite_path = suite_factory(
        suite_id=f"parallel-parity-{uniq}", tasks=[_fake_task(tid) for tid in task_ids]
    )
    subject_path = subject_factory(
        subject_id=f"fake-parity-{uniq}",
        extra={"fake_cost": 1.5, "fake_tokens": {"in": 10, "out": 5}},
    )

    # Serial baseline FIRST, before installing the concurrency-probe subclass below.
    serial = runner.run_suite(
        suite_path, subject_path, out_dir=tmp_path / "results-serial", clock=_fixed_clock
    )
    assert len(serial.tasks) == 8

    class _ConcurrencyProbeSubject(subjects_mod.FakeSubject):
        """Increments a shared counter on entry, rendezvous on a `Barrier(max_parallel)`
        (so a round can only release once exactly `max_parallel` calls are in flight
        simultaneously -- deterministic, no sleep), then decrements. `_max_active`
        records the high-water mark across the whole run.
        """

        _prove_lock: ClassVar[threading.Lock] = threading.Lock()
        _active: ClassVar[int] = 0
        _max_active: ClassVar[int] = 0
        _barrier: ClassVar[threading.Barrier] = threading.Barrier(max_parallel, timeout=5)

        def run(self, task: BenchTask, ctx: RunContext) -> SubjectResult:
            cls = type(self)
            with cls._prove_lock:
                cls._active += 1
                cls._max_active = max(cls._max_active, cls._active)
            cls._barrier.wait()
            with cls._prove_lock:
                cls._active -= 1
            return super().run(task, ctx)

    monkeypatch.setitem(SUBJECT_REGISTRY, "fake", _ConcurrencyProbeSubject)

    parallel = runner.run_suite(
        suite_path,
        subject_path,
        out_dir=tmp_path / "results-parallel",
        max_parallel=max_parallel,
        clock=_fixed_clock,
    )

    # Bounded concurrency: the barrier PROVES at least `max_parallel` ran at once (it
    # cannot release with fewer waiters); the pool's own `max_workers=max_parallel`
    # structurally bounds it from above -- so the high-water mark is exactly 4.
    assert _ConcurrencyProbeSubject._max_active == max_parallel

    # All 8 tasks recorded exactly once, none lost.
    assert len(parallel.tasks) == 8
    assert {t.task_id for t in parallel.tasks} == set(task_ids)

    # Persisted run.json stays id-sorted regardless of completion order.
    result_path = tmp_path / "results-parallel" / parallel.bench_run_id / runner.RUN_JSON_FILENAME
    on_disk_ids = [t["task_id"] for t in json.loads(result_path.read_text())["tasks"]]
    assert on_disk_ids == sorted(on_disk_ids)

    # Per-task results identical to the serial run (timing/workspace aside) -- the set
    # of completed results does not depend on worker count for a non-budget-capped run.
    assert _comparable(parallel.tasks) == _comparable(serial.tasks)


def test_parallel_repeated_runs_never_corrupt_or_lose_a_task_record(
    tmp_path: Path, suite_factory: Any, subject_factory: Any
) -> None:
    """AC2: over repeated max_parallel=4 runs (fresh suite id each time -- a distinct
    bench_run_id/out_dir per iteration), every task is recorded exactly once and
    run.json stays id-sorted -- no last-write-wins corruption from two workers'
    persists racing (guarded by the single lock)."""
    for _ in range(5):
        uniq = _uniq()
        task_ids = [f"t{i}" for i in range(1, 9)]
        suite_path = suite_factory(
            suite_id=f"parallel-repeat-{uniq}", tasks=[_fake_task(tid) for tid in task_ids]
        )
        subject_path = subject_factory(subject_id=f"fake-repeat-{uniq}", extra={"fake_cost": 0.5})

        record = runner.run_suite(
            suite_path,
            subject_path,
            out_dir=tmp_path / f"results-{uniq}",
            max_parallel=4,
            clock=_fixed_clock,
        )
        assert [t.task_id for t in record.tasks] == sorted(task_ids)
        assert all(t.subject_status == "succeeded" for t in record.tasks)


# ---------------------------------------------------------------------------
# Error isolation under parallelism.
# ---------------------------------------------------------------------------


def test_parallel_one_task_raising_does_not_kill_the_pool(
    tmp_path: Path, suite_factory: Any, subject_factory: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    uniq = _uniq()
    task_ids = [f"t{i}" for i in range(1, 7)]
    suite_path = suite_factory(
        suite_id=f"parallel-error-{uniq}", tasks=[_fake_task(tid) for tid in task_ids]
    )
    subject_path = subject_factory(subject_id=f"fake-error-{uniq}", type_="fake")

    class _CrashingSubject(subjects_mod.FakeSubject):
        def run(self, task: BenchTask, ctx: RunContext) -> SubjectResult:
            if task.id == "t3":
                raise SubjectError("scripted crash for t3 mid-pool")
            return super().run(task, ctx)

    monkeypatch.setitem(SUBJECT_REGISTRY, "fake", _CrashingSubject)

    record = runner.run_suite(
        suite_path,
        subject_path,
        out_dir=tmp_path / "results",
        max_parallel=4,
        clock=_fixed_clock,
    )

    assert len(record.tasks) == 6
    by_id = {t.task_id: t for t in record.tasks}
    assert by_id["t3"].subject_status == "error"
    assert "scripted crash for t3" in (by_id["t3"].raw_error or "")
    for tid in set(task_ids) - {"t3"}:
        assert by_id[tid].subject_status == "succeeded"


def test_parallel_unexpected_exception_isolated_run_continues(
    tmp_path: Path, suite_factory: Any, subject_factory: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same as above but a non-BenchError exception (the OTHER isolation branch)."""
    uniq = _uniq()
    task_ids = [f"t{i}" for i in range(1, 5)]
    suite_path = suite_factory(
        suite_id=f"parallel-unexpected-{uniq}", tasks=[_fake_task(tid) for tid in task_ids]
    )
    subject_path = subject_factory(subject_id=f"fake-unexpected-{uniq}", type_="fake")

    class _BuggySubject(subjects_mod.FakeSubject):
        def run(self, task: BenchTask, ctx: RunContext) -> SubjectResult:
            if task.id == "t2":
                raise ValueError("not a BenchError at all")
            return super().run(task, ctx)

    monkeypatch.setitem(SUBJECT_REGISTRY, "fake", _BuggySubject)

    record = runner.run_suite(
        suite_path,
        subject_path,
        out_dir=tmp_path / "results",
        max_parallel=4,
        clock=_fixed_clock,
    )

    by_id = {t.task_id: t for t in record.tasks}
    assert by_id["t2"].subject_status == "error"
    assert "unexpected error" in (by_id["t2"].raw_error or "")
    for tid in set(task_ids) - {"t2"}:
        assert by_id[tid].subject_status == "succeeded"


# ---------------------------------------------------------------------------
# `max_parallel` excluded from config_fingerprint.
# ---------------------------------------------------------------------------


def test_max_parallel_excluded_from_config_fingerprint(
    tmp_path: Path, suite_factory: Any, subject_factory: Any
) -> None:
    uniq = _uniq()
    suite_path = suite_factory(
        suite_id=f"parallel-fp-{uniq}", tasks=[_fake_task("t1"), _fake_task("t2")]
    )
    subject_path = subject_factory(subject_id=f"fake-fp-{uniq}")

    serial = runner.run_suite(
        suite_path, subject_path, out_dir=tmp_path / "results-a", max_parallel=1, clock=_fixed_clock
    )
    parallel = runner.run_suite(
        suite_path, subject_path, out_dir=tmp_path / "results-b", max_parallel=4, clock=_fixed_clock
    )

    assert serial.config_fingerprint == parallel.config_fingerprint
    # Sanity: not a vacuous match -- both runs actually completed all tasks.
    assert len(serial.tasks) == 2
    assert len(parallel.tasks) == 2


def test_max_parallel_does_not_block_resume_with_different_worker_count(
    tmp_path: Path, suite_factory: Any, subject_factory: Any
) -> None:
    """A same-day resume with a DIFFERENT `max_parallel` than the original run must
    not trip the W4 config-fingerprint mismatch guard (control-flow, not per-task
    config) -- covers the resume path specifically, not just two independent runs."""
    uniq = _uniq()
    suite_path = suite_factory(suite_id=f"parallel-resume-fp-{uniq}", tasks=[_fake_task("t1")])
    subject_path = subject_factory(subject_id=f"fake-resume-fp-{uniq}")
    out_dir = tmp_path / "results"

    runner.run_suite(suite_path, subject_path, out_dir=out_dir, max_parallel=1, clock=_fixed_clock)
    # Extend the suite (same bench_run_id) and resume with a DIFFERENT max_parallel --
    # must not raise BenchError (W4 mismatch) and must run the new task.
    suite_path = suite_factory(
        suite_id=f"parallel-resume-fp-{uniq}", tasks=[_fake_task("t1"), _fake_task("t2")]
    )
    second = runner.run_suite(
        suite_path, subject_path, out_dir=out_dir, max_parallel=4, clock=_fixed_clock
    )
    by_id = {t.task_id: t for t in second.tasks}
    assert by_id["t2"].subject_status == "succeeded"


# ---------------------------------------------------------------------------
# Crash-safety: every intermediate persist under the lock is valid, parseable JSON.
# ---------------------------------------------------------------------------


def test_parallel_every_intermediate_persist_snapshot_is_valid_json(
    tmp_path: Path, suite_factory: Any, subject_factory: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    uniq = _uniq()
    task_ids = [f"t{i}" for i in range(1, 9)]
    suite_path = suite_factory(
        suite_id=f"parallel-crash-{uniq}", tasks=[_fake_task(tid) for tid in task_ids]
    )
    subject_path = subject_factory(subject_id=f"fake-crash-{uniq}")

    snapshots: list[str] = []
    orig_persist = runner._persist_record

    def _wrapped_persist(path: Path, record: BenchRunRecord) -> None:
        orig_persist(path, record)
        # Read back what actually landed on disk (not just the in-memory `record`) --
        # proves the write-temp + atomic-rename under the lock left a real, complete,
        # parseable file at every single intermediate step, not just the final one.
        snapshots.append(path.read_text())

    monkeypatch.setattr(runner, "_persist_record", _wrapped_persist)

    record = runner.run_suite(
        suite_path,
        subject_path,
        out_dir=tmp_path / "results",
        max_parallel=4,
        clock=_fixed_clock,
    )

    assert len(record.tasks) == 8
    # One persist per completed task (no batching), same cadence as serial.
    assert len(snapshots) == 8
    for snapshot in snapshots:
        parsed = json.loads(snapshot)  # raises if truncated/corrupt -- crash-safety proof
        BenchRunRecord.model_validate(parsed)  # raises if schema-mismatched


# ---------------------------------------------------------------------------
# Wall-clock sanity: max_parallel=4 must actually be faster than max_parallel=1.
# ---------------------------------------------------------------------------


@pytest.mark.perf
def test_parallel_wall_clock_speedup_over_serial(
    tmp_path: Path, suite_factory: Any, subject_factory: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """6 tasks with a scripted delay each: serial wall-clock ~= 6*delay; max_parallel=4
    wall-clock ~= 2 rounds*delay -- a >=2.5x speedup proves real concurrency (not just a
    non-erroring code path). A real delay is unavoidable here (this IS the thing being
    measured) -- not sleep-as-synchronization for a correctness assertion elsewhere in
    this file.

    Best-of-`_ATTEMPTS` (not sleep-as-synchronization -- a retry against a shared-host
    scheduling noise floor): a wall-clock ratio is inherently noisy under CPU contention
    from whatever else is running on the same host/CI runner at the same time, and one
    slow serial-run outlier (or one throttled parallel-run outlier) should not flake an
    otherwise-real, reproducible speedup. Any regression that broke concurrency entirely
    (e.g. `max_parallel` silently falling back to serial) would still fail every
    attempt, since the ratio would sit at ~1.0x regardless of host noise.
    """
    uniq = _uniq()
    task_ids = [f"t{i}" for i in range(1, 7)]
    suite_path = suite_factory(
        suite_id=f"parallel-perf-{uniq}", tasks=[_fake_task(tid) for tid in task_ids]
    )
    subject_path = subject_factory(subject_id=f"fake-perf-{uniq}")

    _SCRIPTED_DELAY_SECONDS = 0.2
    _RATIO_THRESHOLD = 2.5
    _ATTEMPTS = 3

    class _SlowSubject(subjects_mod.FakeSubject):
        def run(self, task: BenchTask, ctx: RunContext) -> SubjectResult:
            time.sleep(_SCRIPTED_DELAY_SECONDS)
            return super().run(task, ctx)

    monkeypatch.setitem(SUBJECT_REGISTRY, "fake", _SlowSubject)

    measurements: list[tuple[float, float, float]] = []
    for attempt in range(_ATTEMPTS):
        t0 = time.monotonic()
        runner.run_suite(
            suite_path,
            subject_path,
            out_dir=tmp_path / f"results-serial-{attempt}",
            max_parallel=1,
            clock=_fixed_clock,
        )
        serial_elapsed = time.monotonic() - t0

        t0 = time.monotonic()
        runner.run_suite(
            suite_path,
            subject_path,
            out_dir=tmp_path / f"results-parallel-{attempt}",
            max_parallel=4,
            clock=_fixed_clock,
        )
        parallel_elapsed = time.monotonic() - t0

        ratio = serial_elapsed / parallel_elapsed
        measurements.append((serial_elapsed, parallel_elapsed, ratio))
        if ratio >= _RATIO_THRESHOLD:
            break

    best = max(measurements, key=lambda m: m[2])
    assert best[2] >= _RATIO_THRESHOLD, (
        f"expected >={_RATIO_THRESHOLD}x speedup at max_parallel=4 over serial in "
        f"{_ATTEMPTS} attempts, best was {best[2]:.2f}x "
        f"(serial={best[0]:.3f}s, parallel={best[1]:.3f}s); all attempts: {measurements}"
    )
