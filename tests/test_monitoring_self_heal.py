"""Engine integration tests for Consult Point B (T-yjtdAq, epic E-XyfjuZ).

Covers TASK.md acceptance criteria:
1. `self_heal_enabled=False` (default): a task that fails after exhausting `RetryPolicy`
   behaves byte-identically to today (run fails).
2. `self_heal_enabled=True`, `RuleBasedMonitor`, a transient-pattern error then success on
   the healed retry -> the run SUCCEEDS; `count_monitor_heal_retries(state, tid) == 1`; no
   residual "failed" artifacts from the healed attempt.
3. Same setup but the healed retry ALSO fails -> falls through to the existing
   failure/run-fail path exactly as if self-heal were off (bound exhausted after 1 use).
4. `self_heal_enabled=True` but a NON-transient error -> `accept_failure` on the FIRST
   consult (no retry attempted, but the consult DID happen) -> run fails exactly as today.
5. Resume: a persisted heal-retry count (already at the bound before the run stopped for an
   unrelated reason) is NOT reset by `prepare_resume` -- zero fresh heal budget on resume.
6. `ts.started_at` is never reset by the heal-retry requeue (E-3JTmVu single-writer guard).
7. `_read_stderr_tail`/file-I/O-free summary construction is exercised end-to-end (no crash
   even though no capture directory exists for a test-double executor's TaskResult).
8. `max_monitor_calls_per_run` cap exhaustion falls back to accept_failure without consulting.
9. A monitor that raises falls back to the safe default (accept_failure), never crashes.
10. `timed_out`/`cancelled` task statuses are NEVER healed (Design Decision D4 boundary).

A local `_ScriptedExecutor` test double is used (not `FakeExecutor`) because FakeExecutor's
"fail" behavior always returns the same fixed `error="fake failure"` string -- these tests
need control over the EXACT error text to exercise RuleBasedMonitor's transient-pattern
classification deterministically.
"""

from __future__ import annotations

from pathlib import Path

import agent_orchestrator.engine as engine_module
from agent_orchestrator.artifacts import LocalFsArtifactStore
from agent_orchestrator.engine import Orchestrator
from agent_orchestrator.executors.base import Executor
from agent_orchestrator.models import (
    AgentSpec,
    MonitorDecisionRecord,
    RepoRef,
    RepoSet,
    RunState,
    TaskContext,
    TaskResult,
    TaskRunState,
    TaskSpec,
    WorkflowSpec,
    count_monitor_heal_retries,
)
from agent_orchestrator.monitoring import Monitor, RuleBasedMonitor
from agent_orchestrator.runstate import RunStateStore

_STARTED = "2026-01-01T00:00:00+00:00"
_BASE_EPOCH = 1_700_000_000.0


def _make_workspace(tmp_path: Path) -> tuple[LocalFsArtifactStore, RunStateStore]:
    store = LocalFsArtifactStore(str(tmp_path))
    rs_store = RunStateStore(str(tmp_path), store)
    return store, rs_store


def _fake_agents() -> dict:
    return {"ag": AgentSpec(executor="fake")}


def _fake_reposets(workspace: str) -> dict:
    return {
        "rs": RepoSet(
            workspace_root=workspace,
            repos=[RepoRef(id="core", path=".", role="primary")],
        )
    }


def _task(tid: str) -> TaskSpec:
    return TaskSpec(
        id=tid,
        agent="ag",
        instruction="specs/examples/instructions/design.md",
        outputs=[f"output/{tid}.txt"],
    )


def _one_task_workflow() -> WorkflowSpec:
    return WorkflowSpec(version="1.0", id="wf", repo_set="rs", tasks=[_task("flaky")])


class _ScriptedExecutor(Executor):
    """Test double: returns a scripted sequence of `TaskResult`s for one task id (one
    result per `execute()` call; the LAST scripted entry repeats for any call beyond the
    script's length). Writes declared output files on a "succeeded" result so the engine's
    output-existence check passes -- mirrors `FakeExecutor`'s happy path."""

    def __init__(self, task_id: str, script: list[TaskResult]) -> None:
        self._task_id = task_id
        self._script = script
        self._calls = 0

    def execute(self, ctx: TaskContext) -> TaskResult:
        assert ctx.task_id == self._task_id
        idx = min(self._calls, len(self._script) - 1)
        self._calls += 1
        result = self._script[idx]
        if result.status == "succeeded":
            for path in ctx.output_paths:
                Path(path).parent.mkdir(parents=True, exist_ok=True)
                Path(path).write_text("ok")
        return result.model_copy(update={"attempts": 1})


def _failed(error: str) -> TaskResult:
    return TaskResult(task_id="flaky", status="failed", attempts=1, exit_code=1, error=error)


_SUCCEEDED = TaskResult(task_id="flaky", status="succeeded", attempts=1, exit_code=0)


class _RaisesIfCalledMonitor(Monitor):
    name = "raises-if-called"

    def decide_breaker_trip(self, trip, *, run_id):  # type: ignore[no-untyped-def]
        raise AssertionError("decide_breaker_trip must not be called in these tests")

    def decide_task_failure(self, summary, *, run_id):  # type: ignore[no-untyped-def]
        raise AssertionError("decide_task_failure must not be called")


class _RaisingMonitor(Monitor):
    name = "raising"

    def decide_breaker_trip(self, trip, *, run_id):  # type: ignore[no-untyped-def]
        raise AssertionError("not used in these tests")

    def decide_task_failure(self, summary, *, run_id):  # type: ignore[no-untyped-def]
        raise RuntimeError("simulated monitor bug")


# ---------------------------------------------------------------------------
# AC1: self-heal disabled (default) is byte-identical to today
# ---------------------------------------------------------------------------


class TestSelfHealDisabledByDefault:
    def test_disabled_self_heal_fails_exactly_like_today(self, tmp_path: Path) -> None:
        store, rs_store = _make_workspace(tmp_path)
        wf = _one_task_workflow()
        executor = _ScriptedExecutor("flaky", [_failed("connection reset by peer")])
        orch = Orchestrator(executor, store, rs_store, monitor=_RaisesIfCalledMonitor())

        state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents())

        assert state.status == "failed"
        assert state.tasks["flaky"].status == "failed"
        assert state.monitor_decisions == []


# ---------------------------------------------------------------------------
# AC2/AC3: transient error heals once; a second failure exhausts the bound
# ---------------------------------------------------------------------------


class TestSelfHealTransientRetry:
    def test_transient_error_then_success_heals_and_run_succeeds(self, tmp_path: Path) -> None:
        store, rs_store = _make_workspace(tmp_path)
        wf = _one_task_workflow()
        executor = _ScriptedExecutor("flaky", [_failed("connection reset by peer"), _SUCCEEDED])
        sleeps: list[float] = []
        orch = Orchestrator(
            executor,
            store,
            rs_store,
            sleeper=sleeps.append,
            monitor=RuleBasedMonitor(wait_seconds=5.0),
            self_heal_enabled=True,
        )

        state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents())

        assert state.status == "succeeded"
        assert state.tasks["flaky"].status == "succeeded"
        assert sleeps == [5.0]
        assert count_monitor_heal_retries(state, "flaky") == 1
        assert len(state.monitor_decisions) == 1
        rec = state.monitor_decisions[0]
        assert rec.consult_point == "task_failure"
        assert rec.subject_id == "flaky"
        assert rec.decision == "retry"

    def test_transient_error_heals_once_then_fails_again_ends_run(self, tmp_path: Path) -> None:
        store, rs_store = _make_workspace(tmp_path)
        wf = _one_task_workflow()
        executor = _ScriptedExecutor(
            "flaky",
            [_failed("connection reset by peer"), _failed("connection reset by peer")],
        )
        orch = Orchestrator(
            executor,
            store,
            rs_store,
            sleeper=lambda _s: None,
            monitor=RuleBasedMonitor(),
            self_heal_enabled=True,
        )

        state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents())

        assert state.status == "failed"
        assert state.tasks["flaky"].status == "failed"
        # First consult: retry (heal budget used). Second boundary: bound already
        # exhausted (max_heal_retries_per_task default 1) -- no second consult attempted.
        assert len(state.monitor_decisions) == 1
        assert state.monitor_decisions[0].decision == "retry"
        assert count_monitor_heal_retries(state, "flaky") == 1

    def test_cancel_during_self_heal_wait_cancels_the_run(self, tmp_path: Path) -> None:
        """A cancel request arriving while waiting for the healed retry must cancel the
        run (not silently swallow the cancellation) -- distinct from the bound/cap and
        accept_failure paths above."""
        store, rs_store = _make_workspace(tmp_path)
        wf = _one_task_workflow()
        executor = _ScriptedExecutor("flaky", [_failed("connection reset by peer"), _SUCCEEDED])
        cancel_calls: list[int] = []

        def _cancel_fn() -> bool:
            cancel_calls.append(1)
            # cancel_fn() is checked 3 times before the self-heal wait's own check on a
            # normal first pass: (1) the main loop's top-of-iteration check, (2)
            # _run_with_retries' per-attempt check (max_attempts=1 -> exactly once), then
            # (3) the self-heal wait's own check -- return False for the first two so the
            # task genuinely dispatches and fails, True from the third call onward.
            return len(cancel_calls) >= 3

        orch = Orchestrator(
            executor,
            store,
            rs_store,
            sleeper=lambda _s: None,
            cancel_fn=_cancel_fn,
            monitor=RuleBasedMonitor(),
            self_heal_enabled=True,
        )

        state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents())

        assert state.status == "cancelled"
        # The retry verdict was still recorded before the cancel check ran.
        assert len(state.monitor_decisions) == 1
        assert state.monitor_decisions[0].decision == "retry"

    def test_heal_retries_do_not_consume_retry_policy_attempts(self, tmp_path: Path) -> None:
        """A task with RetryPolicy.max_attempts=1 (no ordinary retries at all) can still be
        healed -- proving heal retries are a wholly separate mechanism/counter."""
        store, rs_store = _make_workspace(tmp_path)
        wf = _one_task_workflow()
        assert wf.defaults.retries.max_attempts == 1
        executor = _ScriptedExecutor("flaky", [_failed("timeout"), _SUCCEEDED])
        orch = Orchestrator(
            executor,
            store,
            rs_store,
            sleeper=lambda _s: None,
            monitor=RuleBasedMonitor(),
            self_heal_enabled=True,
        )

        state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents())

        assert state.status == "succeeded"
        assert state.tasks["flaky"].attempts == 1  # the WINNING attempt's own count


# ---------------------------------------------------------------------------
# Late-gate reviewer finding (Critical #1): a healed cycle's REAL actuals must not be
# silently discarded -- cumulative_* must SUM across the failed-then-healed cycle, not
# just reflect the last (winning) cycle. Same bug class E-9h3m7k fixed for retries WITHIN
# one _run_with_retries call; this closes it for self-heal's cross-call redispatch too.
# ---------------------------------------------------------------------------


class TestSelfHealAccumulatesActualsAcrossHealedCycles:
    def test_cumulative_actuals_sum_across_failed_and_healed_cycles(self, tmp_path: Path) -> None:
        store, rs_store = _make_workspace(tmp_path)
        wf = _one_task_workflow()
        failed_with_actuals = TaskResult(
            task_id="flaky",
            status="failed",
            attempts=1,
            exit_code=1,
            error="connection reset by peer",
            actuals_available=True,
            input_tokens=1000,
            output_tokens=500,
            cache_creation_input_tokens=10,
            cache_read_input_tokens=20,
            cost_usd=2.0,
        )
        succeeded_with_actuals = TaskResult(
            task_id="flaky",
            status="succeeded",
            attempts=1,
            exit_code=0,
            actuals_available=True,
            input_tokens=300,
            output_tokens=100,
            cache_creation_input_tokens=5,
            cache_read_input_tokens=15,
            cost_usd=1.0,
        )
        executor = _ScriptedExecutor("flaky", [failed_with_actuals, succeeded_with_actuals])
        orch = Orchestrator(
            executor,
            store,
            rs_store,
            sleeper=lambda _s: None,
            monitor=RuleBasedMonitor(),
            self_heal_enabled=True,
        )

        state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents())

        assert state.status == "succeeded"
        ts = state.tasks["flaky"]
        # Reviewer-verified bug (pre-fix): these would equal ONLY the winning cycle's
        # values (300/100/5/15/1.0) -- the failed cycle's real spend was silently dropped.
        assert ts.cumulative_input_tokens == 1300  # 1000 + 300
        assert ts.cumulative_output_tokens == 600  # 500 + 100
        assert ts.cumulative_cache_creation_input_tokens == 15  # 10 + 5
        assert ts.cumulative_cache_read_input_tokens == 35  # 20 + 15
        assert ts.cumulative_cost_usd == 3.0  # 2.0 + 1.0

    def test_task_cost_usd_breaker_trips_across_a_healed_retry(self, tmp_path: Path) -> None:
        """The exact scenario TaskCostUsdBreaker's own docstring describes ("a task that
        burns $1 on two failed attempts before a $1.50 success trips at cumulative $2.50,
        not just the winning attempt's cost") -- proven here across a HEAL-triggered
        redispatch specifically (not an internal RetryPolicy attempt), which is the gap
        the late-gate reviewer found: a naive last-cycle-only accounting would under-report
        cumulative cost and never trip this breaker."""
        from agent_orchestrator.breakers import BreakerContext, TaskCostUsdBreaker
        from agent_orchestrator.models import CircuitBreakerSpec

        store, rs_store = _make_workspace(tmp_path)
        wf = _one_task_workflow()
        failed_with_cost = TaskResult(
            task_id="flaky",
            status="failed",
            attempts=1,
            error="timeout",
            actuals_available=True,
            cost_usd=2.0,
        )
        succeeded_with_cost = TaskResult(
            task_id="flaky", status="succeeded", attempts=1, actuals_available=True, cost_usd=1.5
        )
        executor = _ScriptedExecutor("flaky", [failed_with_cost, succeeded_with_cost])
        orch = Orchestrator(
            executor,
            store,
            rs_store,
            sleeper=lambda _s: None,
            monitor=RuleBasedMonitor(),
            self_heal_enabled=True,
        )

        state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents())

        assert state.status == "succeeded"
        assert state.tasks["flaky"].cumulative_cost_usd == 3.5  # 2.0 + 1.5

        # A task_cost_usd breaker with threshold=2.50 must trip against the CORRECT
        # cumulative total (3.5), which a last-cycle-only accounting (1.5) would have
        # wrongly missed.
        spec = CircuitBreakerSpec(
            id="cost-cap", condition="task_cost_usd", action="fail", threshold=2.5
        )
        ctx = BreakerContext(state=state, clock_epoch=0.0, store=store)
        result = TaskCostUsdBreaker().evaluate(spec, ctx)
        assert result is not None
        assert result.detail == {"task_id": "flaky", "cost_usd": 3.5, "threshold": 2.5}


# ---------------------------------------------------------------------------
# AC4: a non-transient error is accepted without ever attempting a retry
# ---------------------------------------------------------------------------


class TestSelfHealNonTransientError:
    def test_non_transient_error_consults_but_never_retries(self, tmp_path: Path) -> None:
        store, rs_store = _make_workspace(tmp_path)
        wf = _one_task_workflow()
        executor = _ScriptedExecutor("flaky", [_failed("AssertionError: bad output shape")])
        orch = Orchestrator(
            executor,
            store,
            rs_store,
            monitor=RuleBasedMonitor(),
            self_heal_enabled=True,
        )

        state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents())

        assert state.status == "failed"
        assert executor._calls == 1  # never re-dispatched
        assert len(state.monitor_decisions) == 1
        assert state.monitor_decisions[0].decision == "accept_failure"


# ---------------------------------------------------------------------------
# AC10: timed_out / cancelled are never healed (Design Decision D4)
# ---------------------------------------------------------------------------


class TestSelfHealNeverAppliesToTimeoutOrCancel:
    def test_timed_out_status_is_never_healed(self, tmp_path: Path) -> None:
        store, rs_store = _make_workspace(tmp_path)
        wf = _one_task_workflow()
        timed_out = TaskResult(task_id="flaky", status="timed_out", attempts=1, error="timeout")
        executor = _ScriptedExecutor("flaky", [timed_out])
        orch = Orchestrator(
            executor, store, rs_store, monitor=_RaisesIfCalledMonitor(), self_heal_enabled=True
        )

        state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents())

        assert state.status == "failed"
        assert state.tasks["flaky"].status == "timed_out"
        assert state.monitor_decisions == []


# ---------------------------------------------------------------------------
# AC5: resume honours a persisted heal-retry count
# ---------------------------------------------------------------------------


class TestResumeHonoursPersistedHealState:
    def test_resumed_run_does_not_reset_the_heal_bound(self, tmp_path: Path) -> None:
        store, rs_store = _make_workspace(tmp_path)
        wf = _one_task_workflow()

        prior = RunState(
            run_id="wf-20260101T000000Z",
            workflow_id="wf",
            repo_set="rs",
            started_at=_STARTED,
            updated_at=_STARTED,
            status="failed",
            tasks={"flaky": TaskRunState(status="pending")},
            monitor_decisions=[
                MonitorDecisionRecord(
                    at=_STARTED,
                    consult_point="task_failure",
                    subject_id="flaky",
                    decision="retry",
                    monitor="rules",
                )
            ],
        )
        rs_store.save(prior)
        resumed = rs_store.load(prior.run_id)
        resumed = rs_store.prepare_resume(resumed, wf)
        assert resumed.monitor_decisions == prior.monitor_decisions  # untouched by resume

        executor = _ScriptedExecutor("flaky", [_failed("connection reset")])
        orch = Orchestrator(
            executor,
            store,
            rs_store,
            monitor=_RaisesIfCalledMonitor(),  # must NOT be consulted -- bound already used
            self_heal_enabled=True,
        )

        state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents(), run_state=resumed)

        assert state.status == "failed"
        assert state.tasks["flaky"].status == "failed"
        assert len(state.monitor_decisions) == 1  # no new entry added


# ---------------------------------------------------------------------------
# AC8: max_monitor_calls_per_run cap enforced in engine code
# ---------------------------------------------------------------------------


class TestMaxMonitorCallsPerRunCapForSelfHeal:
    def test_cap_exhausted_falls_back_to_accept_failure_without_consulting(
        self, tmp_path: Path, read_jsonl
    ) -> None:
        store, rs_store = _make_workspace(tmp_path)
        wf = _one_task_workflow()
        executor = _ScriptedExecutor("flaky", [_failed("connection reset")])
        orch = Orchestrator(
            executor,
            store,
            rs_store,
            monitor=_RaisesIfCalledMonitor(),
            self_heal_enabled=True,
            max_monitor_calls_per_run=0,
        )

        state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents())

        assert state.status == "failed"
        assert state.monitor_decisions == []

        log_path = tmp_path / ".orchestrator" / "runs" / state.run_id / "run.log"
        events = [r.get("event") for r in read_jsonl(log_path)]
        assert "monitor.cap_exceeded" in events


# ---------------------------------------------------------------------------
# AC9: a raising Monitor falls back to the safe default (NFR-2)
# ---------------------------------------------------------------------------


class TestSelfHealMonitorRaisingFallsBackSafely:
    def test_raising_monitor_accepts_failure_never_crashes(self, tmp_path: Path) -> None:
        store, rs_store = _make_workspace(tmp_path)
        wf = _one_task_workflow()
        executor = _ScriptedExecutor("flaky", [_failed("connection reset")])
        orch = Orchestrator(
            executor, store, rs_store, monitor=_RaisingMonitor(), self_heal_enabled=True
        )

        state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents())

        assert state.status == "failed"
        assert len(state.monitor_decisions) == 1
        assert state.monitor_decisions[0].decision == "accept_failure"


# ---------------------------------------------------------------------------
# AC6: started_at is never reset across a heal-retry redispatch
# ---------------------------------------------------------------------------


class _StepDatetime:
    """Monkeypatch target for `agent_orchestrator.engine`'s bare `datetime` name (the same
    technique `tests/test_run_active_seconds_breaker.py` uses for the quota-exhaustion
    equivalent of this test -- `TaskRunState.started_at`/`ended_at` are written via direct
    `datetime.now(UTC)` calls in engine.py, not the injectable clock). Returns a fixed EARLY
    sentinel on the first call, then a far-LATER sentinel on every subsequent call -- if
    `started_at` were (bug) reset on the heal-retry redispatch, it would end up equal to the
    LATE sentinel (elapsed 0); with the guard intact, it stays at the EARLY sentinel."""

    calls = 0

    @classmethod
    def now(cls, tz=None):  # type: ignore[no-untyped-def]
        from datetime import UTC as _UTC
        from datetime import datetime as _dt

        cls.calls += 1
        epoch = _BASE_EPOCH if cls.calls == 1 else _BASE_EPOCH + 9999
        return _dt.fromtimestamp(epoch, tz=tz or _UTC)


class TestStartedAtPreservedAcrossHealRetry:
    def test_started_at_not_reset_by_heal_retry_redispatch(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        _StepDatetime.calls = 0
        monkeypatch.setattr(engine_module, "datetime", _StepDatetime)

        store, rs_store = _make_workspace(tmp_path)
        wf = _one_task_workflow()
        executor = _ScriptedExecutor("flaky", [_failed("connection reset"), _SUCCEEDED])
        orch = Orchestrator(
            executor,
            store,
            rs_store,
            sleeper=lambda _s: None,
            monitor=RuleBasedMonitor(),
            self_heal_enabled=True,
        )

        state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents())

        assert state.status == "succeeded"
        ts = state.tasks["flaky"]
        assert ts.started_at is not None
        assert ts.ended_at is not None
        # Only 2 real datetime.now(UTC) calls total: started_at at the FIRST dispatch
        # (EARLY sentinel) and ended_at at the final successful settle (LATE sentinel) --
        # proving started_at was never overwritten by the heal-retry redispatch.
        from datetime import datetime as _dt

        started_epoch = _dt.fromisoformat(ts.started_at).timestamp()
        ended_epoch = _dt.fromisoformat(ts.ended_at).timestamp()
        assert ended_epoch - started_epoch == 9999.0
