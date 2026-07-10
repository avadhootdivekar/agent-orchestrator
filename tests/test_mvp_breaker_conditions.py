"""Unit tests for the six MVP circuit-breaker conditions (T-q5n7k2, epic E-rc7k2v).

Covers TASK.md acceptance criteria 1-6 at the `Breaker.evaluate()` level (fixed/injected
clock, no engine wiring needed -- the task-boundary hook itself is already covered by
tests/test_engine_breakers.py from T-x8v4d3):

1. Each of the six conditions trips exactly at threshold and NOT one unit before.
2. `verdict` uses the shared `read_bool_field` reader and only evaluates once the named
   task is settled succeeded.
3. `injected_task_count` counts `len(state.injected_tasks)` including nested-emitted
   tasks (E2 / epic E-gd8m4x).
4. `consecutive_failures` uses completion order (`ended_at`) and resets on success;
   reconstructs correctly from `ended_at` alone (resume simulation).
5. `stop_file` uses `store.exists` only -- never touches content.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agent_orchestrator.artifacts import ArtifactStore, read_bool_field
from agent_orchestrator.breakers import (
    BREAKER_REGISTRY,
    BreakerContext,
    ConsecutiveFailuresBreaker,
    InjectedTaskCountBreaker,
    RunWallClockSecondsBreaker,
    StopFileBreaker,
    TaskFailuresBreaker,
    VerdictBreaker,
    _consecutive_failure_streak,
)
from agent_orchestrator.errors import ControlFileError
from agent_orchestrator.models import CircuitBreakerSpec, RunState, TaskRunState, TaskSpec

_BASE_EPOCH: float = datetime(2026, 1, 1, tzinfo=UTC).timestamp()


def _run_state(**overrides: object) -> RunState:
    base: dict[str, object] = dict(
        run_id="r1",
        workflow_id="wf",
        repo_set="rs",
        started_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
    )
    base.update(overrides)
    return RunState(**base)  # type: ignore[arg-type]


def _task_spec(tid: str) -> TaskSpec:
    return TaskSpec(id=tid, agent="ag", instruction="specs/instructions/i.md")


# ---------------------------------------------------------------------------
# Registry wiring sanity (guardrail: the six MVP conditions must be permanently
# registered at import time, not left for a fixture to install)
# ---------------------------------------------------------------------------


class TestRegistryWiring:
    def test_all_six_mvp_conditions_registered_at_import_time(self) -> None:
        expected = {
            "task_failures",
            "consecutive_failures",
            "run_wall_clock_seconds",
            "verdict",
            "injected_task_count",
            "stop_file",
        }
        assert expected <= BREAKER_REGISTRY.keys()


# ---------------------------------------------------------------------------
# task_failures
# ---------------------------------------------------------------------------


class TestTaskFailuresBreaker:
    BREAKER = TaskFailuresBreaker()

    def _state(self, failing: int, succeeding: int = 0) -> RunState:
        tasks = {
            f"fail{i}": TaskRunState(status="failed", ended_at=f"2026-01-01T00:00:{i:02d}+00:00")
            for i in range(failing)
        }
        tasks.update(
            {
                f"ok{i}": TaskRunState(
                    status="succeeded", ended_at=f"2026-01-01T00:01:{i:02d}+00:00"
                )
                for i in range(succeeding)
            }
        )
        return _run_state(tasks=tasks)

    @pytest.mark.parametrize(
        "failing,threshold,expect_trip",
        [(1, 2, False), (2, 2, True), (3, 2, True)],
        ids=["below", "at", "above"],
    )
    def test_trips_exactly_at_threshold(
        self, store: ArtifactStore, failing: int, threshold: int, expect_trip: bool
    ) -> None:
        spec = CircuitBreakerSpec(
            id="b", condition="task_failures", action="fail", threshold=threshold
        )
        ctx = BreakerContext(state=self._state(failing), clock_epoch=_BASE_EPOCH, store=store)

        result = self.BREAKER.evaluate(spec, ctx)

        if expect_trip:
            assert result is not None
            assert result.detail == {"failures": failing, "threshold": threshold}
        else:
            assert result is None

    def test_timed_out_counts_as_failure(self, store: ArtifactStore) -> None:
        tasks = {"a": TaskRunState(status="timed_out", ended_at="2026-01-01T00:00:01+00:00")}
        spec = CircuitBreakerSpec(id="b", condition="task_failures", action="fail", threshold=1)
        ctx = BreakerContext(state=_run_state(tasks=tasks), clock_epoch=_BASE_EPOCH, store=store)

        result = self.BREAKER.evaluate(spec, ctx)

        assert result is not None
        assert result.detail == {"failures": 1, "threshold": 1}

    def test_succeeded_and_pending_tasks_never_counted(self, store: ArtifactStore) -> None:
        ctx = BreakerContext(
            state=self._state(failing=0, succeeding=5), clock_epoch=_BASE_EPOCH, store=store
        )
        spec = CircuitBreakerSpec(id="b", condition="task_failures", action="fail", threshold=1)

        assert self.BREAKER.evaluate(spec, ctx) is None

    def test_unset_threshold_never_trips(self, store: ArtifactStore) -> None:
        spec = CircuitBreakerSpec(id="b", condition="task_failures", action="fail")
        ctx = BreakerContext(state=self._state(failing=5), clock_epoch=_BASE_EPOCH, store=store)

        assert self.BREAKER.evaluate(spec, ctx) is None


# ---------------------------------------------------------------------------
# consecutive_failures
# ---------------------------------------------------------------------------


class TestConsecutiveFailuresStreakHelper:
    """Direct tests of `_consecutive_failure_streak`, the pure completion-order helper."""

    def test_streak_counts_trailing_failures_only_scrambled_insertion_order(self) -> None:
        # Chronological order (by ended_at): t1 succeeded, t2 failed, t3 timed_out (most
        # recent). Inserted into the dict out of chronological order to prove the streak
        # is derived from ended_at, not dict/insertion order.
        tasks = {
            "t3": TaskRunState(status="timed_out", ended_at="2026-01-01T00:00:03+00:00"),
            "t1": TaskRunState(status="succeeded", ended_at="2026-01-01T00:00:01+00:00"),
            "t2": TaskRunState(status="failed", ended_at="2026-01-01T00:00:02+00:00"),
        }
        assert _consecutive_failure_streak(_run_state(tasks=tasks)) == 2

    def test_streak_resets_to_zero_on_trailing_success(self) -> None:
        tasks = {
            "t1": TaskRunState(status="failed", ended_at="2026-01-01T00:00:01+00:00"),
            "t2": TaskRunState(status="failed", ended_at="2026-01-01T00:00:02+00:00"),
            "t3": TaskRunState(status="succeeded", ended_at="2026-01-01T00:00:03+00:00"),
        }
        assert _consecutive_failure_streak(_run_state(tasks=tasks)) == 0

    def test_unsettled_tasks_excluded_from_ordering(self) -> None:
        tasks = {
            "t1": TaskRunState(status="failed", ended_at="2026-01-01T00:00:01+00:00"),
            "t2": TaskRunState(status="pending", ended_at=None),
            "t3": TaskRunState(status="not_taken", ended_at=None),
        }
        # t2/t3 never settled (ended_at is None) so they're excluded; t1 is the only
        # settled task and it's a failure => streak 1.
        assert _consecutive_failure_streak(_run_state(tasks=tasks)) == 1

    def test_reconstructs_correctly_after_json_round_trip_resume_simulation(self) -> None:
        """Simulates a resume: the RunState is deserialized fresh from JSON with no
        extra in-memory bookkeeping -- the streak must still reconstruct purely from
        `ended_at` (LLD §7 note: no new persisted ordering field)."""
        tasks = {
            "t2": TaskRunState(status="failed", ended_at="2026-01-01T00:00:02+00:00"),
            "t1": TaskRunState(status="failed", ended_at="2026-01-01T00:00:01+00:00"),
            "t3": TaskRunState(status="failed", ended_at="2026-01-01T00:00:03+00:00"),
        }
        state = _run_state(tasks=tasks)

        resumed = RunState.model_validate_json(state.model_dump_json())

        assert _consecutive_failure_streak(resumed) == 3


class TestConsecutiveFailuresBreaker:
    BREAKER = ConsecutiveFailuresBreaker()

    def _trailing_failure_state(self, streak: int) -> RunState:
        tasks = {
            f"t{i}": TaskRunState(status="failed", ended_at=f"2026-01-01T00:00:{i:02d}+00:00")
            for i in range(streak)
        }
        return _run_state(tasks=tasks)

    @pytest.mark.parametrize(
        "streak,threshold,expect_trip",
        [(1, 2, False), (2, 2, True), (3, 2, True)],
        ids=["below", "at", "above"],
    )
    def test_trips_exactly_at_threshold(
        self, store: ArtifactStore, streak: int, threshold: int, expect_trip: bool
    ) -> None:
        spec = CircuitBreakerSpec(
            id="b", condition="consecutive_failures", action="fail", threshold=threshold
        )
        ctx = BreakerContext(
            state=self._trailing_failure_state(streak), clock_epoch=_BASE_EPOCH, store=store
        )

        result = self.BREAKER.evaluate(spec, ctx)

        if expect_trip:
            assert result is not None
            assert result.detail == {"streak": streak, "threshold": threshold}
        else:
            assert result is None

    def test_unset_threshold_never_trips(self, store: ArtifactStore) -> None:
        spec = CircuitBreakerSpec(id="b", condition="consecutive_failures", action="fail")
        ctx = BreakerContext(
            state=self._trailing_failure_state(5), clock_epoch=_BASE_EPOCH, store=store
        )

        assert self.BREAKER.evaluate(spec, ctx) is None


# ---------------------------------------------------------------------------
# run_wall_clock_seconds
# ---------------------------------------------------------------------------


class TestRunWallClockSecondsBreaker:
    BREAKER = RunWallClockSecondsBreaker()
    _STARTED = "2026-01-01T00:00:00+00:00"
    _STARTED_EPOCH = datetime.fromisoformat(_STARTED).timestamp()

    @pytest.mark.parametrize(
        "elapsed,threshold,expect_trip",
        [(99.0, 100, False), (100.0, 100, True), (150.0, 100, True)],
        ids=["below", "at", "above"],
    )
    def test_trips_exactly_at_threshold(
        self, store: ArtifactStore, elapsed: float, threshold: int, expect_trip: bool
    ) -> None:
        spec = CircuitBreakerSpec(
            id="b", condition="run_wall_clock_seconds", action="stop", threshold=threshold
        )
        state = _run_state(started_at=self._STARTED)
        ctx = BreakerContext(state=state, clock_epoch=self._STARTED_EPOCH + elapsed, store=store)

        result = self.BREAKER.evaluate(spec, ctx)

        if expect_trip:
            assert result is not None
            assert result.detail == {"elapsed": elapsed, "threshold": threshold}
        else:
            assert result is None

    def test_resumed_run_started_long_ago_trips_immediately(self, store: ArtifactStore) -> None:
        """LLD §2.2: measured from the *original* run start, not resume time -- a run
        resumed long after the deadline trips at the very next boundary."""
        spec = CircuitBreakerSpec(
            id="b", condition="run_wall_clock_seconds", action="stop", threshold=60
        )
        state = _run_state(started_at=self._STARTED)
        ctx = BreakerContext(state=state, clock_epoch=self._STARTED_EPOCH + 999_999, store=store)

        result = self.BREAKER.evaluate(spec, ctx)

        assert result is not None

    def test_never_calls_time_time_directly(
        self, store: ArtifactStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import time as time_module

        def _boom(*_a: object, **_kw: object) -> float:
            raise AssertionError("must use ctx.clock_epoch, never call time.time() directly")

        monkeypatch.setattr(time_module, "time", _boom)
        spec = CircuitBreakerSpec(
            id="b", condition="run_wall_clock_seconds", action="stop", threshold=1
        )
        state = _run_state(started_at=self._STARTED)
        ctx = BreakerContext(state=state, clock_epoch=self._STARTED_EPOCH + 5, store=store)

        result = self.BREAKER.evaluate(spec, ctx)

        assert result is not None

    def test_unset_threshold_never_trips(self, store: ArtifactStore) -> None:
        spec = CircuitBreakerSpec(id="b", condition="run_wall_clock_seconds", action="stop")
        state = _run_state(started_at=self._STARTED)
        ctx = BreakerContext(state=state, clock_epoch=self._STARTED_EPOCH + 999_999, store=store)

        assert self.BREAKER.evaluate(spec, ctx) is None


# ---------------------------------------------------------------------------
# verdict
# ---------------------------------------------------------------------------


class TestVerdictBreaker:
    BREAKER = VerdictBreaker()

    def _write_verdict(self, workspace: Path, halted: bool, name: str = "verdict.json") -> None:
        (workspace / name).write_text(json.dumps({"halt": halted}))

    def test_not_evaluated_before_task_settled_succeeded(
        self, store: ArtifactStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AC2: short-circuits BEFORE reading the control file while the named task is
        still pending/running -- proved by making read_bool_field explode if called."""

        def _boom(*_a: object, **_kw: object) -> bool:
            raise AssertionError("verdict must not be read before task_id settles succeeded")

        monkeypatch.setattr("agent_orchestrator.breakers.read_bool_field", _boom)
        spec = CircuitBreakerSpec(
            id="b",
            condition="verdict",
            action="fail",
            task_id="gate",
            verdict_path="verdict.json",
        )
        tasks = {"gate": TaskRunState(status="pending")}
        ctx = BreakerContext(state=_run_state(tasks=tasks), clock_epoch=_BASE_EPOCH, store=store)

        assert self.BREAKER.evaluate(spec, ctx) is None

    def test_not_evaluated_when_task_failed_instead_of_succeeded(
        self, store: ArtifactStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _boom(*_a: object, **_kw: object) -> bool:
            raise AssertionError("verdict must not be read when task_id did not succeed")

        monkeypatch.setattr("agent_orchestrator.breakers.read_bool_field", _boom)
        spec = CircuitBreakerSpec(
            id="b",
            condition="verdict",
            action="fail",
            task_id="gate",
            verdict_path="verdict.json",
        )
        tasks = {"gate": TaskRunState(status="failed", ended_at="2026-01-01T00:00:01+00:00")}
        ctx = BreakerContext(state=_run_state(tasks=tasks), clock_epoch=_BASE_EPOCH, store=store)

        assert self.BREAKER.evaluate(spec, ctx) is None

    def test_not_evaluated_when_task_id_unknown(self, store: ArtifactStore) -> None:
        spec = CircuitBreakerSpec(
            id="b",
            condition="verdict",
            action="fail",
            task_id="does-not-exist",
            verdict_path="verdict.json",
        )
        ctx = BreakerContext(state=_run_state(), clock_epoch=_BASE_EPOCH, store=store)

        assert self.BREAKER.evaluate(spec, ctx) is None

    def test_trips_when_succeeded_and_field_true(
        self, store: ArtifactStore, workspace: Path
    ) -> None:
        self._write_verdict(workspace, True)
        spec = CircuitBreakerSpec(
            id="b",
            condition="verdict",
            action="fail",
            task_id="gate",
            verdict_path="verdict.json",
        )
        tasks = {"gate": TaskRunState(status="succeeded", ended_at="2026-01-01T00:00:01+00:00")}
        ctx = BreakerContext(state=_run_state(tasks=tasks), clock_epoch=_BASE_EPOCH, store=store)

        result = self.BREAKER.evaluate(spec, ctx)

        assert result is not None
        assert result.detail == {"task_id": "gate", "field": "halt"}

    def test_no_trip_when_succeeded_and_field_false(
        self, store: ArtifactStore, workspace: Path
    ) -> None:
        self._write_verdict(workspace, False)
        spec = CircuitBreakerSpec(
            id="b",
            condition="verdict",
            action="fail",
            task_id="gate",
            verdict_path="verdict.json",
        )
        tasks = {"gate": TaskRunState(status="succeeded", ended_at="2026-01-01T00:00:01+00:00")}
        ctx = BreakerContext(state=_run_state(tasks=tasks), clock_epoch=_BASE_EPOCH, store=store)

        assert self.BREAKER.evaluate(spec, ctx) is None

    def test_custom_field_name_honoured(self, store: ArtifactStore, workspace: Path) -> None:
        (workspace / "verdict.json").write_text(json.dumps({"should_stop": True}))
        spec = CircuitBreakerSpec(
            id="b",
            condition="verdict",
            action="fail",
            task_id="gate",
            verdict_path="verdict.json",
            field="should_stop",
        )
        tasks = {"gate": TaskRunState(status="succeeded", ended_at="2026-01-01T00:00:01+00:00")}
        ctx = BreakerContext(state=_run_state(tasks=tasks), clock_epoch=_BASE_EPOCH, store=store)

        result = self.BREAKER.evaluate(spec, ctx)

        assert result is not None
        assert result.detail == {"task_id": "gate", "field": "should_stop"}

    def test_uses_shared_read_bool_field_reader_no_second_reader(
        self, store: ArtifactStore, workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AC2: verify breakers.py delegates to the shared `read_bool_field` (spied via
        its import-time binding in the breakers module) rather than reimplementing JSON
        parsing itself."""
        self._write_verdict(workspace, True)
        calls: list[tuple[str, str]] = []
        real = read_bool_field

        def _spy(store_: ArtifactStore, path: str, field: str) -> bool:
            calls.append((path, field))
            return real(store_, path, field)

        monkeypatch.setattr("agent_orchestrator.breakers.read_bool_field", _spy)
        spec = CircuitBreakerSpec(
            id="b",
            condition="verdict",
            action="fail",
            task_id="gate",
            verdict_path="verdict.json",
        )
        tasks = {"gate": TaskRunState(status="succeeded", ended_at="2026-01-01T00:00:01+00:00")}
        ctx = BreakerContext(state=_run_state(tasks=tasks), clock_epoch=_BASE_EPOCH, store=store)

        result = self.BREAKER.evaluate(spec, ctx)

        assert result is not None
        assert calls == [("verdict.json", "halt")]

    def test_missing_verdict_file_after_success_propagates_control_file_error(
        self, store: ArtifactStore
    ) -> None:
        """A malformed/missing verdict file once the task has genuinely succeeded is a
        real spec/artifact defect -- it must raise, never be silently swallowed into a
        "no trip" result."""
        spec = CircuitBreakerSpec(
            id="b",
            condition="verdict",
            action="fail",
            task_id="gate",
            verdict_path="missing.json",
        )
        tasks = {"gate": TaskRunState(status="succeeded", ended_at="2026-01-01T00:00:01+00:00")}
        ctx = BreakerContext(state=_run_state(tasks=tasks), clock_epoch=_BASE_EPOCH, store=store)

        with pytest.raises(ControlFileError):
            self.BREAKER.evaluate(spec, ctx)


# ---------------------------------------------------------------------------
# injected_task_count
# ---------------------------------------------------------------------------


class TestInjectedTaskCountBreaker:
    BREAKER = InjectedTaskCountBreaker()

    @pytest.mark.parametrize(
        "count,threshold,expect_trip",
        [(1, 2, False), (2, 2, True), (3, 2, True)],
        ids=["below", "at", "above"],
    )
    def test_trips_exactly_at_threshold(
        self, store: ArtifactStore, count: int, threshold: int, expect_trip: bool
    ) -> None:
        injected = [_task_spec(f"inj{i}") for i in range(count)]
        spec = CircuitBreakerSpec(
            id="b", condition="injected_task_count", action="stop", threshold=threshold
        )
        state = _run_state(injected_tasks=injected)
        ctx = BreakerContext(state=state, clock_epoch=_BASE_EPOCH, store=store)

        result = self.BREAKER.evaluate(spec, ctx)

        if expect_trip:
            assert result is not None
            assert result.detail == {"count": count, "threshold": threshold}
        else:
            assert result is None

    def test_counts_nested_emitted_tasks_by_construction(self, store: ArtifactStore) -> None:
        """A task emitted by a task that was itself emitted earlier (nested emission,
        injection depth 2+) lands in the same flat `state.injected_tasks` list that
        `engine.py` appends to regardless of nesting -- epic E-gd8m4x's runaway
        fan-out cap relies on this flat count including nested waves."""
        wave1 = _task_spec("wave1-emitter")  # top-level emit
        wave2_a = _task_spec("wave2-a")  # emitted BY wave1-emitter (nested, depth 2)
        wave2_b = _task_spec("wave2-b")  # emitted BY wave1-emitter (nested, depth 2)
        state = _run_state(injected_tasks=[wave1, wave2_a, wave2_b])
        spec = CircuitBreakerSpec(
            id="b", condition="injected_task_count", action="stop", threshold=3
        )
        ctx = BreakerContext(state=state, clock_epoch=_BASE_EPOCH, store=store)

        result = self.BREAKER.evaluate(spec, ctx)

        assert result is not None
        assert result.detail == {"count": 3, "threshold": 3}

    def test_unset_threshold_never_trips(self, store: ArtifactStore) -> None:
        spec = CircuitBreakerSpec(id="b", condition="injected_task_count", action="stop")
        state = _run_state(injected_tasks=[_task_spec("a"), _task_spec("b")])
        ctx = BreakerContext(state=state, clock_epoch=_BASE_EPOCH, store=store)

        assert self.BREAKER.evaluate(spec, ctx) is None


# ---------------------------------------------------------------------------
# stop_file
# ---------------------------------------------------------------------------


class _ExistsOnlyStore(ArtifactStore):
    """Test double implementing only `exists`; `resolve`/`size` raise if called at all --
    proves `StopFileBreaker` never touches anything beyond existence (NFR-1, AC5)."""

    def __init__(self, existing_paths: set[str]) -> None:
        self._existing = existing_paths

    def exists(self, path: str) -> bool:
        return path in self._existing

    def resolve(self, path: str) -> str:
        raise AssertionError("stop_file must never resolve a path -- existence-only")

    def size(self, path: str) -> int:
        raise AssertionError("stop_file must never check size -- existence-only")


class TestStopFileBreaker:
    BREAKER = StopFileBreaker()

    def test_no_trip_when_path_absent(self) -> None:
        spec = CircuitBreakerSpec(id="b", condition="stop_file", action="stop", path="control/stop")
        ctx = BreakerContext(
            state=_run_state(), clock_epoch=_BASE_EPOCH, store=_ExistsOnlyStore(set())
        )

        assert self.BREAKER.evaluate(spec, ctx) is None

    def test_trips_when_path_exists(self) -> None:
        spec = CircuitBreakerSpec(id="b", condition="stop_file", action="stop", path="control/stop")
        ctx = BreakerContext(
            state=_run_state(),
            clock_epoch=_BASE_EPOCH,
            store=_ExistsOnlyStore({"control/stop"}),
        )

        result = self.BREAKER.evaluate(spec, ctx)

        assert result is not None
        assert result.detail == {"path": "control/stop"}

    def test_never_touches_content_only_existence(self) -> None:
        """AC5: the double raises from resolve()/size() if either is ever called; a
        clean pass here proves stop_file used store.exists() exclusively."""
        spec = CircuitBreakerSpec(id="b", condition="stop_file", action="stop", path="control/stop")
        ctx = BreakerContext(
            state=_run_state(),
            clock_epoch=_BASE_EPOCH,
            store=_ExistsOnlyStore({"control/stop"}),
        )

        result = self.BREAKER.evaluate(spec, ctx)

        assert result is not None  # no AssertionError raised => existence-only path taken

    def test_unset_path_never_trips(self) -> None:
        spec = CircuitBreakerSpec(id="b", condition="stop_file", action="stop")
        ctx = BreakerContext(
            state=_run_state(), clock_epoch=_BASE_EPOCH, store=_ExistsOnlyStore(set())
        )

        assert self.BREAKER.evaluate(spec, ctx) is None
