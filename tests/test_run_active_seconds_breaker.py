"""Unit tests for `run_active_seconds` (T-69MnaW, epic E-3JTmVu).

Follows the style of `tests/test_mvp_breaker_conditions.py` (fixed/injected clock, no engine
wiring needed). Covers TASK.md acceptance criteria:

1. Correct summation across multiple settled tasks.
2. Immune to a large gap between one task's `ended_at` and the next task's `started_at` --
   the key differentiator vs `run_wall_clock_seconds` (same fixture must show
   `run_wall_clock_seconds` tripping while `run_active_seconds` does not).
3. Reconstructs identically after a JSON round-trip (resume simulation), mirroring
   `_consecutive_failure_streak`'s existing round-trip test.
4. `run_wall_clock_seconds` stays completely unchanged (byte-identical) -- both coexist.
5. Registered in `BREAKER_REGISTRY` at import time.
6. Engine-level integration (reviewer-flagged gap, closed): a task that goes through
   engine.py's quota-exhaustion wait-and-redispatch loop still reports the wait as elapsed
   `run_active_seconds` time -- proving `TaskRunState.started_at` is preserved across a
   redispatch of the SAME task rather than reset on each retry (engine.py's single
   `ts.started_at` writer is now guarded to fire only on the task's first-ever dispatch).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

import agent_orchestrator.engine as engine_module
from agent_orchestrator.artifacts import ArtifactStore, LocalFsArtifactStore
from agent_orchestrator.breakers import (
    BREAKER_REGISTRY,
    BreakerContext,
    RunActiveSecondsBreaker,
    RunWallClockSecondsBreaker,
    _settled_task_active_seconds,
)
from agent_orchestrator.engine import Orchestrator
from agent_orchestrator.executors.fake import FakeExecutor
from agent_orchestrator.models import (
    AgentSpec,
    CircuitBreakerSpec,
    RepoRef,
    RepoSet,
    RunState,
    TaskRunState,
    TaskSpec,
    WorkflowSpec,
)
from agent_orchestrator.runstate import RunStateStore

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


class TestRegistryWiring:
    def test_run_active_seconds_registered_at_import_time(self) -> None:
        assert "run_active_seconds" in BREAKER_REGISTRY
        assert isinstance(BREAKER_REGISTRY["run_active_seconds"], RunActiveSecondsBreaker)


class TestSettledTaskActiveSecondsHelper:
    """Direct tests of `_settled_task_active_seconds`, the pure reconstruction helper."""

    def test_sums_multiple_settled_tasks(self) -> None:
        tasks = {
            "a": TaskRunState(
                status="succeeded",
                started_at="2026-01-01T00:00:00+00:00",
                ended_at="2026-01-01T00:00:10+00:00",  # 10s
            ),
            "b": TaskRunState(
                status="failed",
                started_at="2026-01-01T00:01:00+00:00",
                ended_at="2026-01-01T00:01:25+00:00",  # 25s
            ),
        }
        assert _settled_task_active_seconds(_run_state(tasks=tasks)) == 35.0

    def test_unsettled_tasks_contribute_zero(self) -> None:
        tasks = {
            "a": TaskRunState(status="succeeded", started_at=None, ended_at=None),
            "b": TaskRunState(status="pending"),
            "c": TaskRunState(status="running", started_at="2026-01-01T00:00:00+00:00"),
        }
        assert _settled_task_active_seconds(_run_state(tasks=tasks)) == 0.0

    def test_gap_between_tasks_not_counted(self) -> None:
        """The key differentiator: a huge gap between task a's ended_at and task b's
        started_at (e.g. an operator pause) contributes nothing -- only the tasks'
        own started_at..ended_at windows are summed."""
        tasks = {
            "a": TaskRunState(
                status="succeeded",
                started_at="2026-01-01T00:00:00+00:00",
                ended_at="2026-01-01T00:00:05+00:00",  # 5s
            ),
            "b": TaskRunState(
                status="succeeded",
                # Started a full day after "a" ended -- simulates an operator pause.
                started_at="2026-01-02T00:00:00+00:00",
                ended_at="2026-01-02T00:00:07+00:00",  # 7s
            ),
        }
        assert _settled_task_active_seconds(_run_state(tasks=tasks)) == 12.0

    def test_reconstructs_correctly_after_json_round_trip_resume_simulation(self) -> None:
        tasks = {
            "a": TaskRunState(
                status="succeeded",
                started_at="2026-01-01T00:00:00+00:00",
                ended_at="2026-01-01T00:00:03+00:00",
            ),
        }
        state = _run_state(tasks=tasks)
        resumed = RunState.model_validate_json(state.model_dump_json())
        assert _settled_task_active_seconds(resumed) == 3.0


class TestRunActiveSecondsBreaker:
    BREAKER = RunActiveSecondsBreaker()

    def _state_with_active_seconds(self, seconds: float) -> RunState:
        start_epoch = datetime.fromisoformat("2026-01-01T00:00:00+00:00").timestamp()
        end_iso = datetime.fromtimestamp(start_epoch + seconds, tz=UTC).isoformat()
        tasks = {
            "a": TaskRunState(
                status="succeeded",
                started_at="2026-01-01T00:00:00+00:00",
                ended_at=end_iso,
            )
        }
        return _run_state(tasks=tasks)

    @pytest.mark.parametrize(
        "elapsed,threshold,expect_trip",
        [(99.0, 100, False), (100.0, 100, True), (150.0, 100, True)],
        ids=["below", "at", "above"],
    )
    def test_trips_exactly_at_threshold(
        self, store: ArtifactStore, elapsed: float, threshold: int, expect_trip: bool
    ) -> None:
        spec = CircuitBreakerSpec(
            id="b", condition="run_active_seconds", action="stop", threshold=threshold
        )
        state = self._state_with_active_seconds(elapsed)
        ctx = BreakerContext(state=state, clock_epoch=_BASE_EPOCH, store=store)

        result = self.BREAKER.evaluate(spec, ctx)

        if expect_trip:
            assert result is not None
            assert result.detail == {"elapsed": elapsed, "threshold": threshold}
        else:
            assert result is None

    def test_unset_threshold_never_trips(self, store: ArtifactStore) -> None:
        spec = CircuitBreakerSpec(id="b", condition="run_active_seconds", action="stop")
        state = self._state_with_active_seconds(999_999.0)
        ctx = BreakerContext(state=state, clock_epoch=_BASE_EPOCH, store=store)

        assert self.BREAKER.evaluate(spec, ctx) is None

    def test_immune_to_pause_resume_gap_unlike_run_wall_clock_seconds(
        self, store: ArtifactStore
    ) -> None:
        """The key differentiator (TASK.md AC4): same fixture, `run_wall_clock_seconds`
        trips (measures from RunState.started_at, includes the pause gap) while
        `run_active_seconds` does not (only counts genuine task execution time)."""
        # Run "started" long ago; task "a" only actually ran for 5 real seconds, but the
        # evaluation clock is now 10_000s past the run's original start (a long pause).
        tasks = {
            "a": TaskRunState(
                status="succeeded",
                started_at="2026-01-01T00:00:00+00:00",
                ended_at="2026-01-01T00:00:05+00:00",
            )
        }
        state = _run_state(started_at="2026-01-01T00:00:00+00:00", tasks=tasks)
        clock_epoch = datetime.fromisoformat("2026-01-01T00:00:00+00:00").timestamp() + 10_000
        ctx = BreakerContext(state=state, clock_epoch=clock_epoch, store=store)

        wall_clock_spec = CircuitBreakerSpec(
            id="deadline", condition="run_wall_clock_seconds", action="stop", threshold=1000
        )
        active_spec = CircuitBreakerSpec(
            id="active", condition="run_active_seconds", action="stop", threshold=1000
        )

        wall_clock_result = RunWallClockSecondsBreaker().evaluate(wall_clock_spec, ctx)
        active_result = self.BREAKER.evaluate(active_spec, ctx)

        assert wall_clock_result is not None  # deadline semantic: the pause counts, trips
        assert active_result is None  # active semantic: only 5s of real work, does not trip

    def test_never_calls_time_time_directly(
        self, store: ArtifactStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import time as time_module

        def _boom(*_a: object, **_kw: object) -> float:
            raise AssertionError("must never call time.time() directly")

        monkeypatch.setattr(time_module, "time", _boom)
        spec = CircuitBreakerSpec(
            id="b", condition="run_active_seconds", action="stop", threshold=1
        )
        state = self._state_with_active_seconds(5.0)
        ctx = BreakerContext(state=state, clock_epoch=_BASE_EPOCH, store=store)

        result = self.BREAKER.evaluate(spec, ctx)

        assert result is not None


# ---------------------------------------------------------------------------
# Engine-level integration: quota-exhaustion wait time is genuinely counted.
# ---------------------------------------------------------------------------


class _StepDatetime(datetime):
    """Monkeypatch target for `agent_orchestrator.engine`'s bare `datetime` name.

    `TaskRunState.started_at`/`ended_at` are written via direct `datetime.now(UTC)` calls in
    engine.py (NOT the injectable `self._clock`), so this is the only way to make those two
    particular timestamps deterministic in a test. Returns a fixed EARLY sentinel on the
    first call and a fixed, far-LATER sentinel on every call after that -- so if
    `started_at` were (bug) reset on every quota-wait redispatch, it would end up equal to
    the LATE sentinel (same as `ended_at`, elapsed 0); with the fix (write-once), it stays
    at the EARLY sentinel, giving a large, deliberate, unmistakable elapsed value.
    """

    calls = 0

    @classmethod
    def now(cls, tz=None):
        cls.calls += 1
        epoch = _BASE_EPOCH if cls.calls == 1 else _BASE_EPOCH + 9999
        return datetime.fromtimestamp(epoch, tz=tz)


def _fake_reposets(workspace: str) -> dict:
    return {
        "rs": RepoSet(
            workspace_root=workspace,
            repos=[RepoRef(id="core", path=".", role="primary")],
        )
    }


class TestRunActiveSecondsCountsQuotaWaitTime:
    def test_started_at_preserved_across_quota_exhaustion_redispatches(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _StepDatetime.calls = 0
        monkeypatch.setattr(engine_module, "datetime", _StepDatetime)

        store = LocalFsArtifactStore(str(tmp_path))
        rs_store = RunStateStore(str(tmp_path), store)
        wf = WorkflowSpec(
            version="1.0",
            id="wf",
            repo_set="rs",
            tasks=[
                TaskSpec(
                    id="a",
                    agent="ag",
                    instruction="specs/examples/instructions/design.md",
                    outputs=["out/a.txt"],
                )
            ],
        )
        # Two quota-exhaustion failures before success; sleeper is a no-op recorder so the
        # test runs instantly regardless of the (fixed, injected) clock/poll settings.
        executor = FakeExecutor(quota_exhausted_tasks={"a": 2})
        sleeps: list[float] = []
        orch = Orchestrator(
            executor,
            store,
            rs_store,
            sleeper=sleeps.append,
            clock=lambda: datetime.fromtimestamp(_BASE_EPOCH, tz=UTC),
            quota_max_wait_seconds=3600,
            quota_poll_seconds=10,
        )

        state = orch.run(wf, _fake_reposets(str(tmp_path)), {"ag": AgentSpec(executor="fake")})

        assert state.status == "succeeded"
        assert len(sleeps) == 2  # both quota-exhaustion waits genuinely happened
        ts = state.tasks["a"]
        assert ts.started_at is not None
        assert ts.ended_at is not None
        elapsed = _settled_task_active_seconds(state)
        # Only 2 real datetime.now(UTC) calls total for this task: started_at at the FIRST
        # dispatch (EARLY sentinel) and ended_at at the final successful settle (LATE
        # sentinel) -- proving started_at was never reset across the two redispatches.
        assert elapsed == 9999.0

        spec = CircuitBreakerSpec(
            id="active", condition="run_active_seconds", action="stop", threshold=9999
        )
        ctx = BreakerContext(state=state, clock_epoch=_BASE_EPOCH, store=store)
        assert RunActiveSecondsBreaker().evaluate(spec, ctx) is not None
