"""Integration tests for USD cost-budget enforcement in bench/runner.py (T-Bg2Wq4,
ADR-0009 D3: check-before-schedule, `skipped_budget` status, resume re-attempts
`skipped_budget` records, `cost_budget_usd` excluded from `config_fingerprint`).

Network-free throroughout: every test drives `FakeSubject` with a scripted,
subject-level `fake_cost` (a constant $/task, mirroring the AC1 "fake subject scripted
to report $2/task" wording) -- no real LLM, no subprocess. Uses the shared
`suite_factory`/`subject_factory` fixtures from `tests/bench/conftest.py` (they already
materialize a real instruction.md/fixture per task, which `load_suite` requires), and a
per-test random id suffix (`_uniq()`) so repeated/concurrent test runs on the same day
never collide on `bench_run_id` (mirrors `tests/bench/test_runner.py`'s own convention).

A fixed, injected clock (CLAUDE.md determinism rule) is used everywhere.

The trailing `TestBudgetUnderConcurrency` class (T-Pl3Rx7, ADR-0009 D4) extends this
file with `max_parallel > 1` cases -- the budget check-before-schedule must stay
correct (bounded overshoot only) once tasks can run concurrently. No sleeps-as-
synchronization: a `threading.Barrier` forces genuine, deterministic in-flight overlap.
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from agent_orchestrator.bench import runner
from agent_orchestrator.bench import subjects as subjects_mod
from agent_orchestrator.bench.registries import SUBJECT_REGISTRY
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


# ---------------------------------------------------------------------------
# AC1 -- check-before-schedule boundary: $2/task, cap $5, 6 tasks.
# ---------------------------------------------------------------------------


def test_budget_boundary_overshoot_then_skips_remaining(
    tmp_path: Path, suite_factory: Any, subject_factory: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    uniq = _uniq()
    task_ids = [f"t{i}" for i in range(1, 7)]
    suite_path = suite_factory(
        suite_id=f"budget-ac1-{uniq}", tasks=[_fake_task(tid) for tid in task_ids]
    )
    subject_path = subject_factory(subject_id=f"fake-ac1-{uniq}", extra={"fake_cost": 2.0})

    call_order: list[str] = []

    class _RecordingSubject(subjects_mod.FakeSubject):
        def run(self, task: BenchTask, ctx: RunContext) -> SubjectResult:
            call_order.append(task.id)
            return super().run(task, ctx)

    monkeypatch.setitem(SUBJECT_REGISTRY, "fake", _RecordingSubject)

    record = runner.run_suite(
        suite_path,
        subject_path,
        out_dir=tmp_path / "results",
        cost_budget_usd=5.0,
        clock=_fixed_clock,
    )

    # t1 (cum 0<5), t2 (cum 2<5), t3 (cum 4<5, the crossing task) all run for real;
    # t4/t5/t6 never get a chance to schedule (bounded 1-task overshoot: 3*$2=$6>$5).
    assert call_order == ["t1", "t2", "t3"]
    by_id = {t.task_id: t for t in record.tasks}
    for tid in ("t1", "t2", "t3"):
        assert by_id[tid].subject_status == "succeeded"
        assert by_id[tid].cost_usd == 2.0
    for tid in ("t4", "t5", "t6"):
        assert by_id[tid].subject_status == "skipped_budget"
        assert by_id[tid].solved is False
        assert by_id[tid].score == 0.0
        assert by_id[tid].cost_usd == 0.0
        assert by_id[tid].workspace == ""  # never materialized -- no subprocess, no ws.
    assert record.aggregate.total_cost_usd == 6.0
    assert record.aggregate.total == 6
    assert record.aggregate.solved == 3


def test_budget_skip_logs_skip_budget_event_with_task_running_cost_and_cap(
    tmp_path: Path,
    suite_factory: Any,
    subject_factory: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="agent_orchestrator")
    uniq = _uniq()
    suite_path = suite_factory(
        suite_id=f"budget-log-{uniq}",
        tasks=[_fake_task("t1"), _fake_task("t2"), _fake_task("t3")],
    )
    subject_path = subject_factory(subject_id=f"fake-log-{uniq}", extra={"fake_cost": 5.0})

    runner.run_suite(
        suite_path,
        subject_path,
        out_dir=tmp_path / "results",
        cost_budget_usd=5.0,
        clock=_fixed_clock,
    )

    skip_events = [
        r for r in caplog.records if getattr(r, "event", None) == "bench.task.skip_budget"
    ]
    # t1 runs (cum 0<5, crossing task, cum becomes 5); t2/t3 are both skipped.
    assert len(skip_events) == 2
    for r in skip_events:
        assert r.task_id in ("t2", "t3")
        assert r.running_cost == 5.0
        assert r.cap == 5.0


# ---------------------------------------------------------------------------
# `cost_budget_usd=None` (the default) -- unlimited, no budget check at all.
# ---------------------------------------------------------------------------


def test_budget_none_default_means_unlimited_no_skip(
    tmp_path: Path, suite_factory: Any, subject_factory: Any
) -> None:
    uniq = _uniq()
    suite_path = suite_factory(
        suite_id=f"budget-none-{uniq}",
        tasks=[_fake_task("t1"), _fake_task("t2"), _fake_task("t3")],
    )
    subject_path = subject_factory(subject_id=f"fake-none-{uniq}", extra={"fake_cost": 1000.0})

    # No cost_budget_usd passed at all -- pre-existing callers of run_suite (every test
    # in test_runner.py) must be entirely unaffected by this feature.
    record = runner.run_suite(
        suite_path, subject_path, out_dir=tmp_path / "results", clock=_fixed_clock
    )

    assert all(t.subject_status == "succeeded" for t in record.tasks)
    assert record.aggregate.total_cost_usd == 3000.0


# ---------------------------------------------------------------------------
# AC3 -- resume re-attempts skipped_budget tasks; already-succeeded tasks untouched.
# ---------------------------------------------------------------------------


def test_budget_resume_reattempts_skipped_leaves_succeeded_alone(
    tmp_path: Path, suite_factory: Any, subject_factory: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    uniq = _uniq()
    suite_path = suite_factory(
        suite_id=f"budget-resume-{uniq}",
        tasks=[_fake_task("t1"), _fake_task("t2"), _fake_task("t3")],
    )
    subject_path = subject_factory(subject_id=f"fake-resume-{uniq}", extra={"fake_cost": 3.0})
    out_dir = tmp_path / "results"

    class _LocalCountingSubject(subjects_mod.FakeSubject):
        call_count = 0

        def run(self, task: BenchTask, ctx: RunContext) -> SubjectResult:
            type(self).call_count += 1
            return super().run(task, ctx)

    monkeypatch.setitem(SUBJECT_REGISTRY, "fake", _LocalCountingSubject)

    first = runner.run_suite(
        suite_path, subject_path, out_dir=out_dir, cost_budget_usd=5.0, clock=_fixed_clock
    )
    # t1 (cum 0<5) runs, cost 3, cum=3; t2 (cum 3<5) runs, cost 3, cum=6; t3 (cum 6>=5) skip.
    assert _LocalCountingSubject.call_count == 2
    by_id = {t.task_id: t for t in first.tasks}
    assert by_id["t1"].subject_status == "succeeded"
    assert by_id["t2"].subject_status == "succeeded"
    assert by_id["t3"].subject_status == "skipped_budget"

    # Raise the cap and resume (force=False, the normal CLI re-run path): t3 is
    # re-attempted -- NOT skipped as "already recorded" (the one exception to the
    # resume-skip rule); t1/t2 are NOT re-invoked (already succeeded, untouched).
    second = runner.run_suite(
        suite_path, subject_path, out_dir=out_dir, cost_budget_usd=100.0, clock=_fixed_clock
    )
    assert _LocalCountingSubject.call_count == 3  # only t3 newly invoked
    by_id2 = {t.task_id: t for t in second.tasks}
    assert by_id2["t3"].subject_status == "succeeded"
    assert by_id2["t3"].cost_usd == 3.0
    assert by_id2["t1"].subject_status == "succeeded"
    assert by_id2["t2"].subject_status == "succeeded"

    # AC5: raising the cap between two same-day runs must not trip the W4 fingerprint
    # mismatch guard (cost_budget_usd is excluded from config_fingerprint) -- if it had
    # tripped, the second run_suite call above would have raised BenchError instead of
    # succeeding.
    assert second.config_fingerprint == first.config_fingerprint


def test_budget_resume_still_capped_if_new_cap_still_too_low(
    tmp_path: Path, suite_factory: Any, subject_factory: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A resume that raises the cap but not ENOUGH re-attempts the skipped task, and it
    may still get skipped again under the new (still insufficient) cap."""
    uniq = _uniq()
    suite_path = suite_factory(
        suite_id=f"budget-stillcap-{uniq}", tasks=[_fake_task("t1"), _fake_task("t2")]
    )
    subject_path = subject_factory(subject_id=f"fake-stillcap-{uniq}", extra={"fake_cost": 10.0})
    out_dir = tmp_path / "results"

    class _LocalCountingSubject(subjects_mod.FakeSubject):
        call_count = 0

        def run(self, task: BenchTask, ctx: RunContext) -> SubjectResult:
            type(self).call_count += 1
            return super().run(task, ctx)

    monkeypatch.setitem(SUBJECT_REGISTRY, "fake", _LocalCountingSubject)

    first = runner.run_suite(
        suite_path, subject_path, out_dir=out_dir, cost_budget_usd=5.0, clock=_fixed_clock
    )
    by_id = {t.task_id: t for t in first.tasks}
    assert by_id["t1"].subject_status == "succeeded"
    assert by_id["t2"].subject_status == "skipped_budget"
    assert _LocalCountingSubject.call_count == 1

    # Cap raised, but running_cost recomputed from non-skipped records is still $10
    # (t1's cost) -- 10 >= 6, so t2 is re-attempted (falls through the resume-skip
    # check) but then immediately re-skipped by the (still-crossed) budget check.
    second = runner.run_suite(
        suite_path, subject_path, out_dir=out_dir, cost_budget_usd=6.0, clock=_fixed_clock
    )
    assert _LocalCountingSubject.call_count == 1  # t2 was considered but not actually run
    by_id2 = {t.task_id: t for t in second.tasks}
    assert by_id2["t2"].subject_status == "skipped_budget"


# ---------------------------------------------------------------------------
# AC5 -- cost_budget_usd excluded from config_fingerprint (fresh, non-resuming runs).
# ---------------------------------------------------------------------------


def test_budget_cap_excluded_from_config_fingerprint(
    tmp_path: Path, suite_factory: Any, subject_factory: Any
) -> None:
    uniq = _uniq()
    suite_path = suite_factory(suite_id=f"budget-fp-{uniq}", tasks=[_fake_task("t1")])
    subject_path = subject_factory(subject_id=f"fake-fp-{uniq}", extra={"fake_cost": 1.0})

    record_a = runner.run_suite(
        suite_path,
        subject_path,
        out_dir=tmp_path / "results-a",
        cost_budget_usd=5.0,
        clock=_fixed_clock,
    )
    record_b = runner.run_suite(
        suite_path,
        subject_path,
        out_dir=tmp_path / "results-b",
        cost_budget_usd=999.0,
        clock=_fixed_clock,
    )

    assert record_a.config_fingerprint == record_b.config_fingerprint
    # Sanity: the two runs really did differ only in the cap (both tasks succeeded --
    # $1 never approaches either cap), i.e. this isn't a vacuous fingerprint match.
    assert record_a.tasks[0].subject_status == "succeeded"
    assert record_b.tasks[0].subject_status == "succeeded"


# ---------------------------------------------------------------------------
# AC6 -- cost_usd is None (e.g. missing state.json) counts as $0 for the running total.
# ---------------------------------------------------------------------------


def test_budget_none_cost_counts_as_zero_for_running_total(
    tmp_path: Path, suite_factory: Any, subject_factory: Any
) -> None:
    uniq = _uniq()
    task_ids = [f"t{i}" for i in range(1, 6)]
    suite_path = suite_factory(
        suite_id=f"budget-nonecost-{uniq}", tasks=[_fake_task(tid) for tid in task_ids]
    )
    # No fake_cost set -> FakeSubject reports cost_usd=None for every task.
    subject_path = subject_factory(subject_id=f"fake-nonecost-{uniq}")

    record = runner.run_suite(
        suite_path,
        subject_path,
        out_dir=tmp_path / "results",
        cost_budget_usd=1.0,
        clock=_fixed_clock,
    )

    # Every task's None cost is treated as $0 -- the running total never reaches the
    # $1 cap, so ALL five tasks run for real; none are skipped_budget.
    assert all(t.subject_status == "succeeded" for t in record.tasks)
    assert all(t.cost_usd is None for t in record.tasks)
    assert record.aggregate.total_cost_usd == 0.0


def test_budget_resume_recomputes_running_cost_treating_none_as_zero(
    tmp_path: Path, suite_factory: Any, subject_factory: Any
) -> None:
    """AC6, resume path specifically: an existing record with a None-cost, non-skipped
    task must recompute running_cost as $0 for it (not, say, raise or treat as
    infinite), so a low-cap resume still runs a brand-new task added to the suite.
    """
    uniq = _uniq()
    suite_id = f"budget-resume-none-{uniq}"
    suite_path = suite_factory(suite_id=suite_id, tasks=[_fake_task("t1")])
    subject_path = subject_factory(subject_id=f"fake-resume-none-{uniq}")  # cost_usd=None
    out_dir = tmp_path / "results"

    first = runner.run_suite(
        suite_path, subject_path, out_dir=out_dir, cost_budget_usd=1.0, clock=_fixed_clock
    )
    assert first.tasks[0].subject_status == "succeeded"
    assert first.tasks[0].cost_usd is None

    # Extend the suite with a second task (same suite id -> same bench_run_id) and
    # resume under the same low cap -- if the None cost had instead been treated as
    # "unbounded" this new task would incorrectly be skipped_budget; treated as $0 it
    # runs normally.
    suite_path = suite_factory(suite_id=suite_id, tasks=[_fake_task("t1"), _fake_task("t2")])
    second = runner.run_suite(
        suite_path, subject_path, out_dir=out_dir, cost_budget_usd=1.0, clock=_fixed_clock
    )
    by_id = {t.task_id: t for t in second.tasks}
    assert by_id["t2"].subject_status == "succeeded"


# ---------------------------------------------------------------------------
# T-Pl3Rx7 (ADR-0009 D4) -- budget correctness under `max_parallel > 1`.
# ---------------------------------------------------------------------------


class TestBudgetUnderConcurrency:
    """`cost_budget_usd` + `max_parallel > 1` combined (TASK.md AC3/R3): the
    check-before-schedule lock must keep the SAME semantics as serial (see
    `test_budget_boundary_overshoot_then_skips_remaining` above), only with the
    documented bounded overshoot widened from "1 already-in-flight task" to "up to
    `max_parallel` already-in-flight tasks"."""

    def test_overshoot_bounded_by_max_parallel_in_flight_tasks(
        self,
        tmp_path: Path,
        suite_factory: Any,
        subject_factory: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """8 tasks x $3/task, cap=$1, max_parallel=4: a `threading.Barrier(4)`
        deterministically forces the first 4 dispatched tasks (t1-t4, the first 4
        submitted to a 4-worker pool) to all be PAST the budget check -- and thus
        all "in flight" with their cost not yet added -- before any of them returns,
        so all 4 run for real despite a cap that would allow only t1 in serial mode.
        The remaining 4 (t5-t8) are dispatched only once a worker frees up, by which
        point at least one of t1-t4 has already recorded its cost past the cap, so
        every one of them is `skipped_budget`. Asserts the exact documented bound:
        `cap <= total_cost_usd < cap + max_parallel * max_task_cost`.
        """
        uniq = _uniq()
        task_ids = [f"t{i}" for i in range(1, 9)]
        suite_path = suite_factory(
            suite_id=f"budget-conc-overshoot-{uniq}", tasks=[_fake_task(tid) for tid in task_ids]
        )
        subject_path = subject_factory(
            subject_id=f"fake-conc-overshoot-{uniq}", extra={"fake_cost": 3.0}
        )

        max_parallel = 4
        barrier = threading.Barrier(max_parallel, timeout=5)
        barrier_task_ids = frozenset(task_ids[:max_parallel])  # t1..t4

        class _BarrierSubject(subjects_mod.FakeSubject):
            def run(self, task: BenchTask, ctx: RunContext) -> SubjectResult:
                if task.id in barrier_task_ids:
                    barrier.wait()  # rendezvous: all 4 must be dispatched before any returns
                return super().run(task, ctx)

        monkeypatch.setitem(SUBJECT_REGISTRY, "fake", _BarrierSubject)

        record = runner.run_suite(
            suite_path,
            subject_path,
            out_dir=tmp_path / "results",
            cost_budget_usd=1.0,
            max_parallel=max_parallel,
            clock=_fixed_clock,
        )

        assert len(record.tasks) == 8
        by_id = {t.task_id: t for t in record.tasks}
        for tid in barrier_task_ids:
            assert by_id[tid].subject_status == "succeeded"
            assert by_id[tid].cost_usd == 3.0
        for tid in set(task_ids) - barrier_task_ids:
            assert by_id[tid].subject_status == "skipped_budget"
            assert by_id[tid].cost_usd == 0.0

        cap = 1.0
        max_task_cost = 3.0
        assert cap <= record.aggregate.total_cost_usd < cap + max_parallel * max_task_cost
        assert record.aggregate.total_cost_usd == 12.0  # exactly the 4 barrier tasks' cost

        # run.json persisted order is still id-sorted regardless of completion order.
        result_path = tmp_path / "results" / record.bench_run_id / runner.RUN_JSON_FILENAME
        on_disk_ids = [t["task_id"] for t in json.loads(result_path.read_text())["tasks"]]
        assert on_disk_ids == sorted(on_disk_ids)

    def test_resume_under_concurrency_reattempts_all_skipped_budget_tasks(
        self, tmp_path: Path, suite_factory: Any, subject_factory: Any
    ) -> None:
        """A cap of $0 deterministically skips EVERY task regardless of dispatch
        order under concurrency (no boundary ambiguity), proving resume + D4's lock
        combine correctly: raising the cap and re-running with `max_parallel=4`
        re-attempts every previously-skipped task and all succeed."""
        uniq = _uniq()
        task_ids = [f"t{i}" for i in range(1, 7)]
        suite_path = suite_factory(
            suite_id=f"budget-conc-resume-{uniq}", tasks=[_fake_task(tid) for tid in task_ids]
        )
        subject_path = subject_factory(
            subject_id=f"fake-conc-resume-{uniq}", extra={"fake_cost": 1.0}
        )
        out_dir = tmp_path / "results"

        first = runner.run_suite(
            suite_path,
            subject_path,
            out_dir=out_dir,
            cost_budget_usd=0.0,
            max_parallel=4,
            clock=_fixed_clock,
        )
        assert all(t.subject_status == "skipped_budget" for t in first.tasks)

        second = runner.run_suite(
            suite_path,
            subject_path,
            out_dir=out_dir,
            cost_budget_usd=100.0,
            max_parallel=4,
            clock=_fixed_clock,
        )
        assert all(t.subject_status == "succeeded" for t in second.tasks)
        assert second.aggregate.total_cost_usd == 6.0
        assert second.config_fingerprint == first.config_fingerprint
