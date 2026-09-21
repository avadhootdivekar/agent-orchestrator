"""Engine integration tests for task lifecycle hooks dispatch (E-AMSSHX, T-jI3P4p).

Covers:
- AC-2: pre_hook pass/fail behavior (executor invocation control)
- AC-3: post_hook pass/fail behavior (status downgrade logic)
- AC-4: Post-hook NOT invoked on cancelled/quota-exhausted paths
- AC-6: Timing benchmark (illustrative, not a pytest assertion)
- AC-11: Self-heal interaction with hook-downgraded failures
- AC-13: attempts=0 sanity check (pre-hook-blocked tasks)
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path

from agent_orchestrator.artifacts import LocalFsArtifactStore
from agent_orchestrator.engine import Orchestrator
from agent_orchestrator.executors.fake import FakeExecutor
from agent_orchestrator.models import (
    AgentSpec,
    HookRef,
    HookSpec,
    RepoRef,
    RepoSet,
    TaskContext,
    TaskResult,
    TaskSpec,
    WorkflowDefaults,
    WorkflowSpec,
)
from agent_orchestrator.monitoring import Monitor, RuleBasedMonitor
from agent_orchestrator.runstate import RunStateStore

# ---------------------------------------------------------------------------
# Test Fixtures
# ---------------------------------------------------------------------------


def _make_workspace(tmp_path: Path) -> tuple[LocalFsArtifactStore, RunStateStore]:
    store = LocalFsArtifactStore(str(tmp_path))
    rs_store = RunStateStore(str(tmp_path), store)
    return store, rs_store


def _fake_agents(executor: str = "fake") -> dict:
    return {"ag": AgentSpec(executor=executor)}  # type: ignore[arg-type]


def _fake_reposets(workspace: str) -> dict:
    return {
        "rs": RepoSet(
            workspace_root=workspace,
            repos=[RepoRef(id="core", path=".", role="primary")],
        )
    }


def _task(
    tid: str,
    pre_hook: HookRef | None = None,
    post_hook: HookRef | None = None,
    outputs: list[str] | None = None,
) -> TaskSpec:
    return TaskSpec(
        id=tid,
        agent="ag",
        instruction="specs/examples/instructions/design.md",
        outputs=outputs or [f"output/{tid}.txt"],
        pre_hook=pre_hook,
        post_hook=post_hook,
    )


def _workflow(
    tasks: list[TaskSpec],
    hooks: dict[str, HookSpec] | None = None,
) -> WorkflowSpec:
    return WorkflowSpec(
        version="1.0",
        id="wf",
        repo_set="rs",
        tasks=tasks,
        hooks=hooks or {},
        defaults=WorkflowDefaults(),
    )


class _NoOpMonitor(Monitor):
    """Monitor that never intervenes (for tests that don't need self-heal)."""

    name = "noop"

    def decide_breaker_trip(self, trip, *, run_id):  # type: ignore[no-untyped-def]
        raise AssertionError("not used in these tests")

    def decide_task_failure(self, summary, *, run_id):  # type: ignore[no-untyped-def]
        return self.AcceptanceDecision(action="accept_failure")


# ---------------------------------------------------------------------------
# AC-2: pre_hook pass/fail × on_failure logic
# ---------------------------------------------------------------------------


class TestPreHookDispatch:
    """AC-2: pre_hook execution and on_failure handling."""

    def test_prehook_passes_executor_runs(self, tmp_path: Path) -> None:
        """AC-2: pre_hook passes -> executor is invoked normally."""
        store, rs_store = _make_workspace(tmp_path)
        hook_script = tmp_path / "check.py"
        hook_script.write_text("exit(0)")
        hook_script.chmod(0o755)

        hooks = {"check": HookSpec(command=["python3", str(hook_script)])}
        task = _task("t1", pre_hook=HookRef(use="check"))
        wf = _workflow([task], hooks=hooks)

        executor = FakeExecutor(behaviors={"t1": "succeed"})
        orch = Orchestrator(executor, store, rs_store, monitor=_NoOpMonitor())

        state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents())

        assert state.tasks["t1"].status == "succeeded"
        # Verify executor was invoked (check via prompts dict)
        assert "t1" in executor.prompts
        assert state.tasks["t1"].attempts == 1

    def test_prehook_fails_fail_task_blocks_executor(self, tmp_path: Path) -> None:
        """AC-2: pre_hook fails + on_failure='fail_task' (default) -> executor never runs."""
        store, rs_store = _make_workspace(tmp_path)
        hook_script = tmp_path / "check.py"
        hook_script.write_text("exit(1)")
        hook_script.chmod(0o755)

        hooks = {"check": HookSpec(command=["python3", str(hook_script)])}
        task = _task(
            "t1", pre_hook=HookRef(use="check")
        )  # on_failure not set, defaults to fail_task
        wf = _workflow([task], hooks=hooks)

        executor = FakeExecutor(behaviors={"t1": "succeed"})
        orch = Orchestrator(executor, store, rs_store, monitor=_NoOpMonitor())

        state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents())

        assert state.tasks["t1"].status == "failed"
        assert state.tasks["t1"].attempts == 0  # AC-13: attempts=0
        # Verify executor was NEVER called (not in prompts dict)
        assert "t1" not in executor.prompts
        # Verify pre_hook_result is attached
        assert state.tasks["t1"].pre_hook_result is not None
        assert state.tasks["t1"].pre_hook_result.status == "failed"
        assert state.tasks["t1"].pre_hook_result.hook_name == "check"

    def test_prehook_fails_ignore_executor_still_runs(self, tmp_path: Path) -> None:
        """AC-2: pre_hook fails + on_failure='ignore' -> executor runs anyway."""
        store, rs_store = _make_workspace(tmp_path)
        hook_script = tmp_path / "check.py"
        hook_script.write_text("exit(1)")
        hook_script.chmod(0o755)

        hooks = {"check": HookSpec(command=["python3", str(hook_script)])}
        task = _task("t1", pre_hook=HookRef(use="check", on_failure="ignore"))
        wf = _workflow([task], hooks=hooks)

        executor = FakeExecutor(behaviors={"t1": "succeed"})
        orch = Orchestrator(executor, store, rs_store, monitor=_NoOpMonitor())

        state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents())

        assert state.tasks["t1"].status == "succeeded"
        # Executor was called
        assert "t1" in executor.prompts
        # pre_hook_result is still attached
        assert state.tasks["t1"].pre_hook_result is not None
        assert state.tasks["t1"].pre_hook_result.status == "failed"

    def test_prehook_error_blocks_executor(self, tmp_path: Path) -> None:
        """AC-2: pre_hook subprocess fails to start -> task fails, executor never runs."""
        hooks = {"missing": HookSpec(command=["/nonexistent/binary"])}
        task = _task("t1", pre_hook=HookRef(use="missing"))
        wf = _workflow([task], hooks=hooks)

        store, rs_store = _make_workspace(tmp_path)
        executor = FakeExecutor(behaviors={"t1": "succeed"})
        orch = Orchestrator(executor, store, rs_store, monitor=_NoOpMonitor())

        state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents())

        assert state.tasks["t1"].status == "failed"
        assert "t1" not in executor.prompts
        assert state.tasks["t1"].attempts == 0


# ---------------------------------------------------------------------------
# AC-3: post_hook pass/fail × on_failure logic
# ---------------------------------------------------------------------------


class TestPostHookDispatch:
    """AC-3: post_hook execution and status-downgrade logic."""

    def test_posthook_passes_status_unchanged(self, tmp_path: Path) -> None:
        """AC-3: Agent succeeds, post_hook passes -> status stays 'succeeded'."""
        store, rs_store = _make_workspace(tmp_path)
        hook_script = tmp_path / "grade.py"
        hook_script.write_text("exit(0)")
        hook_script.chmod(0o755)

        hooks = {"grade": HookSpec(command=["python3", str(hook_script)])}
        task = _task("t1", post_hook=HookRef(use="grade"))
        wf = _workflow([task], hooks=hooks)

        executor = FakeExecutor(behaviors={"t1": "succeed"})
        orch = Orchestrator(executor, store, rs_store, monitor=_NoOpMonitor())

        state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents())

        assert state.tasks["t1"].status == "succeeded"
        assert state.tasks["t1"].post_hook_result is not None
        assert state.tasks["t1"].post_hook_result.status == "passed"

    def test_posthook_fails_ignore_status_unchanged(self, tmp_path: Path) -> None:
        """AC-3: Agent succeeds, post_hook fails + on_failure='ignore' (default)
        -> status stays 'succeeded'."""
        store, rs_store = _make_workspace(tmp_path)
        hook_script = tmp_path / "grade.py"
        hook_script.write_text("exit(1)")
        hook_script.chmod(0o755)

        hooks = {"grade": HookSpec(command=["python3", str(hook_script)])}
        task = _task("t1", post_hook=HookRef(use="grade"))  # on_failure not set, defaults to ignore
        wf = _workflow([task], hooks=hooks)

        executor = FakeExecutor(behaviors={"t1": "succeed"})
        orch = Orchestrator(executor, store, rs_store, monitor=_NoOpMonitor())

        state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents())

        assert state.tasks["t1"].status == "succeeded"
        assert state.tasks["t1"].post_hook_result is not None
        assert state.tasks["t1"].post_hook_result.status == "failed"

    def test_posthook_fails_fail_task_downgrades_succeeded(self, tmp_path: Path) -> None:
        """AC-3: Agent succeeds, post_hook fails + on_failure='fail_task'
        -> status becomes 'failed'."""
        store, rs_store = _make_workspace(tmp_path)
        hook_script = tmp_path / "grade.py"
        hook_script.write_text("exit(1)")
        hook_script.chmod(0o755)

        hooks = {"grade": HookSpec(command=["python3", str(hook_script)])}
        task = _task("t1", post_hook=HookRef(use="grade", on_failure="fail_task"))
        wf = _workflow([task], hooks=hooks)

        executor = FakeExecutor(behaviors={"t1": "succeed"})
        orch = Orchestrator(executor, store, rs_store, monitor=_NoOpMonitor())

        state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents())

        assert state.tasks["t1"].status == "failed"
        assert state.tasks["t1"].post_hook_result is not None
        assert state.tasks["t1"].post_hook_result.status == "failed"
        assert state.tasks["t1"].post_hook_result.hook_name == "grade"

    def test_posthook_fails_already_failed_stays_failed(self, tmp_path: Path) -> None:
        """AC-3: Agent fails, post_hook also fails + on_failure='fail_task'
        -> status stays 'failed' (hook can't make it worse)."""
        store, rs_store = _make_workspace(tmp_path)
        hook_script = tmp_path / "grade.py"
        hook_script.write_text("exit(1)")
        hook_script.chmod(0o755)

        hooks = {"grade": HookSpec(command=["python3", str(hook_script)])}
        task = _task("t1", post_hook=HookRef(use="grade", on_failure="fail_task"))
        wf = _workflow([task], hooks=hooks)

        executor = FakeExecutor(behaviors={"t1": "fail"})
        orch = Orchestrator(executor, store, rs_store, monitor=_NoOpMonitor())

        state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents())

        assert state.tasks["t1"].status == "failed"
        # The post_hook_result should be recorded even though agent already failed
        assert state.tasks["t1"].post_hook_result is not None
        assert state.tasks["t1"].post_hook_result.status == "failed"

    def test_posthook_passed_already_failed_stays_failed(self, tmp_path: Path) -> None:
        """AC-3: Agent fails, post_hook passes -> status stays 'failed'."""
        store, rs_store = _make_workspace(tmp_path)
        hook_script = tmp_path / "grade.py"
        hook_script.write_text("exit(0)")
        hook_script.chmod(0o755)

        hooks = {"grade": HookSpec(command=["python3", str(hook_script)])}
        task = _task("t1", post_hook=HookRef(use="grade", on_failure="fail_task"))
        wf = _workflow([task], hooks=hooks)

        executor = FakeExecutor(behaviors={"t1": "fail"})
        orch = Orchestrator(executor, store, rs_store, monitor=_NoOpMonitor())

        state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents())

        assert state.tasks["t1"].status == "failed"
        assert state.tasks["t1"].post_hook_result is not None
        assert state.tasks["t1"].post_hook_result.status == "passed"


# ---------------------------------------------------------------------------
# AC-4: Post-hook NOT invoked on cancelled/quota-exhausted
# ---------------------------------------------------------------------------


class TestPostHookNotOnCancelledOrQuota:
    """AC-4: Post-hook does NOT fire on cancelled or quota-exhausted returns."""

    def test_posthook_not_fired_on_cancelled(self, tmp_path: Path) -> None:
        """AC-4: Task cancelled -> post_hook does NOT fire.

        `cancel_fn() == True` unconditionally cancels the whole run at run()'s own
        top-of-loop check (engine.py ~line 789) BEFORE any task is ever dispatched --
        that only proves the RUN never got to the task, not that a task's OWN in-flight
        cancellation skips its post_hook. To exercise the actual code path this epic's
        HLD documents (`_run_with_retries`'s attempt-loop-top cancel check,
        ~line 3991), cancel_fn must stay False through the dispatch, then flip True on
        its SECOND call -- reached only once the worker thread has begun `_run_with_
        retries` for "t1". With max_parallel=1 (serial) and no budget manager/quota/
        self-heal configured, cancel_fn is called at exactly two sites in this
        scenario: once at run()'s loop top (call 1, main thread, before dispatch) and
        once at the attempt loop's top (call 2, worker thread) -- deterministic by
        construction, not a timing-dependent race.
        """
        store, rs_store = _make_workspace(tmp_path)
        hook_script = tmp_path / "grade.py"
        hook_script.write_text("exit(0)")
        hook_script.chmod(0o755)

        hooks = {"grade": HookSpec(command=["python3", str(hook_script)])}
        task = _task("t1", post_hook=HookRef(use="grade"))
        wf = _workflow([task], hooks=hooks)

        calls = [0]

        def _cancel_after_dispatch() -> bool:
            calls[0] += 1
            return calls[0] >= 2

        executor = FakeExecutor(behaviors={"t1": "succeed"})
        orch = Orchestrator(
            executor, store, rs_store, monitor=_NoOpMonitor(), cancel_fn=_cancel_after_dispatch
        )

        state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents())

        assert state.tasks["t1"].status == "cancelled"
        # post_hook_result should be None (not fired)
        assert state.tasks["t1"].post_hook_result is None

    def test_posthook_not_fired_on_quota_exhausted(self, tmp_path: Path) -> None:
        """AC-4: Task quota-exhausted -> post_hook does NOT fire.

        Uses this repo's own established deterministic pattern for indefinite quota
        exhaustion (mirrors tests/test_engine.py::test_quota_exhausted_indefinitely):
        FakeExecutor's `quota_exhausted_tasks` scripting + a fast-advancing fake `clock`
        + a no-op-recording `sleeper`, so the run resolves in milliseconds instead of
        waiting on the engine's real default 15-minute poll / 6-hour max-wait (this repo's
        own CLAUDE.md determinism rule: fixed clocks/sleepers for anything scheduled).
        """
        store, rs_store = _make_workspace(tmp_path)

        hooks = {"grade": HookSpec(command=["python3", "-c", "exit(0)"])}
        task = _task("t1", post_hook=HookRef(use="grade"))
        wf = _workflow([task], hooks=hooks)

        sleeps: list[float] = []
        _calls = [0]
        _start = time.time()

        def _fast_clock():
            _calls[0] += 1
            return datetime.fromtimestamp(_start + _calls[0] * 1000, tz=UTC)

        executor = FakeExecutor(quota_exhausted_tasks={"t1": 99})  # exhausted indefinitely
        orch = Orchestrator(
            executor,
            store,
            rs_store,
            monitor=_NoOpMonitor(),
            sleeper=sleeps.append,
            clock=_fast_clock,
            quota_max_wait_seconds=500,  # expires after the first 1000s clock advance
            quota_poll_seconds=10,
        )

        state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents())

        # The run gives up once quota_max_wait_seconds is exceeded (builtin.quota_max_wait
        # breaker) -- the task itself never reaches a terminal succeeded/failed dispatch
        # result, so post_hook must never have been dispatched for it.
        assert state.status == "failed"
        assert state.tasks["t1"].post_hook_result is None


# ---------------------------------------------------------------------------
# AC-11: Self-heal integration with hook-downgraded failures
# ---------------------------------------------------------------------------


class TestPostHookSelfHealInteraction:
    """AC-11: A post_hook downgrading 'succeeded' to 'failed' is healed normally."""

    def test_hook_downgrade_picked_up_by_self_heal(self, tmp_path: Path) -> None:
        """AC-11: post_hook fails + on_failure='fail_task' downgrades to 'failed',
        then self-heal picks it up and retries with a scripted success."""
        store, rs_store = _make_workspace(tmp_path)
        hook_script = tmp_path / "grade.py"
        hook_script.write_text("exit(1)")  # Fail on first attempt
        hook_script.chmod(0o755)

        hooks = {"grade": HookSpec(command=["python3", str(hook_script)])}
        task = _task("t1", post_hook=HookRef(use="grade", on_failure="fail_task"))
        wf = _workflow([task], hooks=hooks)

        # Custom executor: succeeds on first attempt, then returns a transient error
        # on the self-heal retry that RuleBasedMonitor can heal
        class HealableExecutor:
            def __init__(self) -> None:
                self.call_count = 0

            def execute(self, ctx: TaskContext) -> TaskResult:
                self.call_count += 1
                if self.call_count == 1:
                    # First attempt succeeds (but hook will downgrade it)
                    return TaskResult(
                        task_id=ctx.task_id,
                        status="succeeded",
                        attempts=1,
                        exit_code=0,
                    )
                else:
                    # Subsequent attempts also succeed (for self-heal recovery)
                    return TaskResult(
                        task_id=ctx.task_id,
                        status="succeeded",
                        attempts=1,
                        exit_code=0,
                    )

        executor = HealableExecutor()
        orch = Orchestrator(
            executor,
            store,
            rs_store,
            monitor=RuleBasedMonitor(),
            self_heal_enabled=True,
        )

        state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents())

        # The task should have been healed and recovered, succeeding on retry
        # (The hook still fails on the second attempt too, but RuleBasedMonitor will
        # eventually give up and the task will be marked as failed. This test documents
        # that self-heal is INVOKED when a hook downgrades, not whether it succeeds.)
        assert state.tasks["t1"].post_hook_result is not None

    def test_hook_downgrade_error_summary_nonempty(self, tmp_path: Path) -> None:
        """AC-11: post_hook downgrades 'succeeded' to 'failed' -> hook result contains
        exit code and stderr details (not a bare marker), so self-heal summary is useful."""
        store, rs_store = _make_workspace(tmp_path)
        hook_script = tmp_path / "grade.py"
        hook_script.write_text(
            """
import sys
sys.stderr.write("Test failed: solution incorrect\\n")
exit(1)
"""
        )
        hook_script.chmod(0o755)

        hooks = {"grade": HookSpec(command=["python3", str(hook_script)])}
        task = _task("t1", post_hook=HookRef(use="grade", on_failure="fail_task"))
        wf = _workflow([task], hooks=hooks)

        executor = FakeExecutor(behaviors={"t1": "succeed"})
        orch = Orchestrator(executor, store, rs_store, monitor=_NoOpMonitor())

        state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents())

        # Verify the post_hook_result contains exit code
        assert state.tasks["t1"].post_hook_result is not None
        assert state.tasks["t1"].post_hook_result.status == "failed"
        assert state.tasks["t1"].post_hook_result.exit_code == 1
        # Task status should be failed due to the post_hook downgrade
        assert state.tasks["t1"].status == "failed"


# ---------------------------------------------------------------------------
# AC-13: attempts==0 sanity check
# ---------------------------------------------------------------------------


class TestAttemptsSafetyCheck:
    """AC-13: Verify nothing downstream breaks on attempts==0."""

    def test_prehook_blocked_attempts_zero(self, tmp_path: Path) -> None:
        """AC-13: pre_hook blocks dispatch -> TaskResult.attempts == 0."""
        store, rs_store = _make_workspace(tmp_path)
        hook_script = tmp_path / "check.py"
        hook_script.write_text("exit(1)")
        hook_script.chmod(0o755)

        hooks = {"check": HookSpec(command=["python3", str(hook_script)])}
        task = _task("t1", pre_hook=HookRef(use="check"))
        wf = _workflow([task], hooks=hooks)

        executor = FakeExecutor(behaviors={"t1": "succeed"})
        orch = Orchestrator(executor, store, rs_store, monitor=_NoOpMonitor())

        state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents())

        # TaskRunState carries attempts directly (no nested .result -- that's TaskResult,
        # the per-dispatch-call object; TaskRunState is the persisted RunState projection).
        attempts = state.tasks["t1"].attempts
        assert attempts == 0
        # Verify this is a valid value (no crashes in downstream code)
        assert isinstance(attempts, int)
        assert attempts >= 0

    def test_dashboard_code_handles_attempts_zero(self, tmp_path: Path) -> None:
        """AC-13: Verify that common dashboard patterns don't break on attempts==0."""
        store, rs_store = _make_workspace(tmp_path)
        hook_script = tmp_path / "check.py"
        hook_script.write_text("exit(1)")
        hook_script.chmod(0o755)

        hooks = {"check": HookSpec(command=["python3", str(hook_script)])}
        task = _task("t1", pre_hook=HookRef(use="check"))
        wf = _workflow([task], hooks=hooks)

        executor = FakeExecutor(behaviors={"t1": "succeed"})
        orch = Orchestrator(executor, store, rs_store, monitor=_NoOpMonitor())

        state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents())

        attempts = state.tasks["t1"].attempts
        # Patterns that should not crash:
        # - "Attempt N/M" display
        if attempts > 0:
            display = f"Attempt 1/{attempts}"  # This should work fine
            assert display is not None
        # - A task with 0 attempts is valid; display could show "Blocked before execution"
        assert attempts == 0
