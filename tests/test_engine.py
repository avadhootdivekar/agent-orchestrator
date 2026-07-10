"""Tests for the Orchestrator engine."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_orchestrator.artifacts import LocalFsArtifactStore
from agent_orchestrator.engine import Orchestrator
from agent_orchestrator.executors.base import Executor
from agent_orchestrator.executors.fake import FakeExecutor
from agent_orchestrator.models import (
    AgentSpec,
    RepoRef,
    RepoSet,
    RetryPolicy,
    TaskContext,
    TaskResult,
    TaskSpec,
    WorkflowDefaults,
    WorkflowSpec,
)
from agent_orchestrator.runstate import RunStateStore

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_workspace(tmp_path: Path) -> tuple[LocalFsArtifactStore, RunStateStore]:
    store = LocalFsArtifactStore(str(tmp_path))
    rs_store = RunStateStore(str(tmp_path), store)
    return store, rs_store


def _fake_agents(executor: str = "fake") -> dict:
    return {
        "ag": AgentSpec(executor=executor),  # type: ignore[arg-type]
    }


def _fake_reposets(workspace: str) -> dict:
    return {
        "rs": RepoSet(
            workspace_root=workspace,
            repos=[RepoRef(id="core", path=".", role="primary")],
        )
    }


def _workflow(
    tasks: list[TaskSpec],
    wf_id: str = "wf",
    defaults: WorkflowDefaults | None = None,
) -> WorkflowSpec:
    return WorkflowSpec(
        version="1.0",
        id=wf_id,
        repo_set="rs",
        tasks=tasks,
        defaults=defaults or WorkflowDefaults(),
    )


def _task(
    tid: str,
    depends_on: list[str] | None = None,
    inputs: list[str] | None = None,
    outputs: list[str] | None = None,
    output_manifest: str | None = None,
    retries: RetryPolicy | None = None,
    timeout_seconds: int | None = None,
) -> TaskSpec:
    return TaskSpec(
        id=tid,
        agent="ag",
        instruction="specs/examples/instructions/design.md",
        depends_on=depends_on or [],
        inputs=inputs or [],
        outputs=outputs or [],
        output_manifest=output_manifest,
        retries=retries,
        timeout_seconds=timeout_seconds,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestLinearSuccess:
    def test_abc_all_succeed(self, tmp_path) -> None:
        out_a = "output/a.txt"
        out_b = "output/b.txt"
        out_c = "output/c.txt"
        tasks = [
            _task("a", outputs=[out_a]),
            _task("b", depends_on=["a"], inputs=[out_a], outputs=[out_b]),
            _task("c", depends_on=["b"], inputs=[out_b], outputs=[out_c]),
        ]
        wf = _workflow(tasks)
        store, rs_store = _make_workspace(tmp_path)
        executor = FakeExecutor()
        orch = Orchestrator(executor, store, rs_store)

        state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents())

        assert state.status == "succeeded"
        for tid in ["a", "b", "c"]:
            assert state.tasks[tid].status == "succeeded"


class TestMissingInputFailure:
    def test_missing_input_causes_failed_state(self, tmp_path) -> None:
        tasks = [
            _task("a", inputs=["no/such/file.txt"]),
        ]
        wf = _workflow(tasks)
        store, rs_store = _make_workspace(tmp_path)
        executor = FakeExecutor()
        orch = Orchestrator(executor, store, rs_store)

        state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents())

        assert state.status == "failed"
        assert state.tasks["a"].status == "failed"


class TestOutputNotProduced:
    def test_executor_succeeded_but_outputs_absent(self, tmp_path) -> None:
        tasks = [_task("a", outputs=["output/out.txt"])]
        wf = _workflow(tasks)
        store, rs_store = _make_workspace(tmp_path)
        # FakeExecutor with write_outputs=False => executor returns "succeeded" but file is missing
        executor = FakeExecutor(write_outputs=False)
        orch = Orchestrator(executor, store, rs_store)

        state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents())

        assert state.status == "failed"
        assert state.tasks["a"].status == "failed"


class TestResume:
    def test_only_failed_task_reruns_on_resume(self, tmp_path) -> None:
        """A+B succeed, C fails. Resume -> only C runs."""
        out_a = "output/a.txt"
        out_b = "output/b.txt"
        out_c = "output/c.txt"
        tasks = [
            _task("a", outputs=[out_a]),
            _task("b", depends_on=["a"], inputs=[out_a], outputs=[out_b]),
            _task("c", depends_on=["b"], inputs=[out_b], outputs=[out_c]),
        ]
        wf = _workflow(tasks)

        store, rs_store = _make_workspace(tmp_path)
        executor = FakeExecutor(behaviors={"c": "fail"})
        orch = Orchestrator(executor, store, rs_store)

        # First run: a, b succeed; c fails
        state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents())
        assert state.status == "failed"
        assert state.tasks["a"].status == "succeeded"
        assert state.tasks["b"].status == "succeeded"
        assert state.tasks["c"].status == "failed"
        run_id = state.run_id

        # Now switch c to succeed
        executor2 = FakeExecutor()
        orch2 = Orchestrator(executor2, store, rs_store)
        existing = rs_store.load(run_id)
        existing = rs_store.prepare_resume(existing, wf)
        state2 = orch2.run(wf, _fake_reposets(str(tmp_path)), _fake_agents(), run_state=existing)

        assert state2.status == "succeeded"
        # a and b were skipped (outputs present), c re-ran
        assert state2.tasks["a"].status in ("succeeded", "skipped")
        assert state2.tasks["b"].status in ("succeeded", "skipped")
        assert state2.tasks["c"].status == "succeeded"


class TestNFR1Hygiene:
    def test_engine_never_passes_file_contents_in_context(self, tmp_path) -> None:
        """The engine must only pass paths to executor; never file contents."""

        class RecordingExecutor(Executor):
            def __init__(self):
                self.contexts: list[TaskContext] = []

            def execute(self, ctx: TaskContext) -> TaskResult:
                self.contexts.append(ctx)
                # Write the output so the engine thinks it succeeded
                for p in ctx.output_paths:
                    Path(p).parent.mkdir(parents=True, exist_ok=True)
                    Path(p).write_text("output")
                return TaskResult(task_id=ctx.task_id, status="succeeded", attempts=1)

        # Create instruction file with secret content
        instr = tmp_path / "specs" / "examples" / "instructions"
        instr.mkdir(parents=True)
        instr_file = instr / "design.md"
        instr_file.write_text("SECRET_INSTRUCTION_CONTENT_ABC")

        tasks = [_task("a", outputs=["output/a.txt"])]
        wf = _workflow(tasks)

        store, rs_store = _make_workspace(tmp_path)
        rec = RecordingExecutor()
        orch = Orchestrator(rec, store, rs_store)

        state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents())
        assert state.status == "succeeded"

        assert len(rec.contexts) == 1
        ctx_json = rec.contexts[0].model_dump_json()
        assert "SECRET_INSTRUCTION_CONTENT_ABC" not in ctx_json


class TestRetry:
    def test_task_fails_once_then_succeeds(self, tmp_path) -> None:
        """StatefulFakeExecutor fails on attempt 1, succeeds on attempt 2."""
        attempts: list[int] = []
        output_path = str(tmp_path / "output" / "a.txt")

        class StatefulExecutor(Executor):
            def execute(self, ctx: TaskContext) -> TaskResult:
                attempts.append(1)
                if len(attempts) < 2:
                    return TaskResult(
                        task_id=ctx.task_id, status="failed", attempts=1, error="transient"
                    )
                # Second attempt: succeed and write output
                Path(output_path).parent.mkdir(parents=True, exist_ok=True)
                Path(output_path).write_text("done")
                return TaskResult(task_id=ctx.task_id, status="succeeded", attempts=1)

        tasks = [_task("a", outputs=["output/a.txt"], retries=RetryPolicy(max_attempts=3))]
        wf = _workflow(tasks)
        store, rs_store = _make_workspace(tmp_path)
        orch = Orchestrator(StatefulExecutor(), store, rs_store, sleeper=lambda _: None)

        state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents())
        assert state.status == "succeeded"
        assert state.tasks["a"].status == "succeeded"
        assert len(attempts) == 2

    def test_all_retries_exhausted_leaves_failed(self, tmp_path) -> None:
        tasks = [_task("a", outputs=["output/a.txt"], retries=RetryPolicy(max_attempts=2))]
        wf = _workflow(tasks)
        store, rs_store = _make_workspace(tmp_path)
        executor = FakeExecutor(behaviors={"a": "fail"}, write_outputs=False)
        orch = Orchestrator(executor, store, rs_store, sleeper=lambda _: None)

        state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents())
        assert state.status == "failed"
        assert state.tasks["a"].status == "failed"

    def test_cumulative_usage_summed_across_failed_and_succeeding_attempts(self, tmp_path) -> None:
        """E-9h3m7k FR-2: a failed attempt's actual usage must not be discarded.

        Attempt 1 burns real tokens/cost before failing; attempt 2 succeeds. The task's
        final cumulative usage must be the SUM of both, not just attempt 2's numbers.
        """
        output_path = str(tmp_path / "output" / "a.txt")
        calls: list[int] = []

        class CostyExecutor(Executor):
            def execute(self, ctx: TaskContext) -> TaskResult:
                calls.append(1)
                if len(calls) < 2:
                    return TaskResult(
                        task_id=ctx.task_id,
                        status="failed",
                        attempts=1,
                        error="transient",
                        input_tokens=100,
                        output_tokens=50,
                        cost_usd=0.01,
                        actuals_available=True,
                    )
                Path(output_path).parent.mkdir(parents=True, exist_ok=True)
                Path(output_path).write_text("done")
                return TaskResult(
                    task_id=ctx.task_id,
                    status="succeeded",
                    attempts=1,
                    input_tokens=200,
                    output_tokens=80,
                    cost_usd=0.02,
                    actuals_available=True,
                )

        tasks = [_task("a", outputs=["output/a.txt"], retries=RetryPolicy(max_attempts=3))]
        wf = _workflow(tasks)
        store, rs_store = _make_workspace(tmp_path)
        orch = Orchestrator(CostyExecutor(), store, rs_store, sleeper=lambda _: None)

        state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents())

        ts = state.tasks["a"]
        assert ts.status == "succeeded"
        assert ts.cumulative_input_tokens == 300  # 100 + 200, NOT just 200
        assert ts.cumulative_output_tokens == 130  # 50 + 80
        assert ts.cumulative_cost_usd == pytest.approx(0.03)  # 0.01 + 0.02

        from agent_orchestrator.models import compute_run_usage_totals

        totals = compute_run_usage_totals(state)
        assert totals.cost_usd == pytest.approx(0.03)
        assert totals.input_tokens == 300

    def test_retry_attempts_get_distinct_output_dirs(self, tmp_path) -> None:
        """A failed attempt's capture dir must survive the next retry, not be clobbered."""
        calls: list[int] = []
        seen_output_dirs: list[str] = []

        class RecordingExecutor(Executor):
            def execute(self, ctx: TaskContext) -> TaskResult:
                calls.append(1)
                seen_output_dirs.append(ctx.output_dir)
                Path(ctx.output_dir).mkdir(parents=True, exist_ok=True)
                Path(ctx.output_dir, "marker.txt").write_text(f"attempt {len(calls)}")
                if len(calls) < 2:
                    return TaskResult(task_id=ctx.task_id, status="failed", attempts=1)
                return TaskResult(task_id=ctx.task_id, status="succeeded", attempts=1)

        tasks = [_task("a", retries=RetryPolicy(max_attempts=3))]
        wf = _workflow(tasks)
        store, rs_store = _make_workspace(tmp_path)
        orch = Orchestrator(RecordingExecutor(), store, rs_store, sleeper=lambda _: None)

        state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents())

        assert state.tasks["a"].status == "succeeded"
        assert len(seen_output_dirs) == 2
        assert seen_output_dirs[0] != seen_output_dirs[1]
        # Attempt 1's marker file must still exist after attempt 2 ran — proves the
        # retry didn't overwrite attempt 1's capture directory.
        assert Path(seen_output_dirs[0], "marker.txt").read_text() == "attempt 1"
        assert Path(seen_output_dirs[1], "marker.txt").read_text() == "attempt 2"


class TestOutputManifest:
    def test_manifest_dynamic_outputs_passed_to_downstream(self, tmp_path) -> None:
        """Task A writes a manifest; task B receives those paths as dynamic_input_paths."""
        manifest_path = "output/a-manifest.json"
        dynamic_files = ["src/foo.py", "tests/test_foo.py"]

        # Track what dynamic_input_paths task B receives
        received_dynamic: list[list[str]] = []

        class RecordingExecutor(Executor):
            def __init__(self, fake: FakeExecutor):
                self._fake = fake

            def execute(self, ctx: TaskContext) -> TaskResult:
                if ctx.task_id == "b":
                    received_dynamic.append(list(ctx.dynamic_input_paths))
                return self._fake.execute(ctx)

        tasks = [
            _task("a", outputs=["output/a.txt"], output_manifest=manifest_path),
            _task("b", depends_on=["a"], outputs=["output/b.txt"]),
        ]
        wf = _workflow(tasks)
        store, rs_store = _make_workspace(tmp_path)
        fake = FakeExecutor(manifest_payloads={"a": dynamic_files})
        executor = RecordingExecutor(fake)
        orch = Orchestrator(executor, store, rs_store)

        state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents())

        assert state.status == "succeeded"
        assert state.tasks["a"].dynamic_outputs == dynamic_files
        assert len(received_dynamic) == 1
        # Paths are resolved to absolute; check the basenames match
        received_basenames = [p.split("/")[-1] for p in received_dynamic[0]]
        assert received_basenames == ["foo.py", "test_foo.py"]

    def test_manifest_read_failure_fails_task(self, tmp_path) -> None:
        """If task declares output_manifest but the agent doesn't write it, task fails."""
        tasks = [
            _task("a", outputs=["output/a.txt"], output_manifest="output/missing-manifest.json"),
        ]
        wf = _workflow(tasks)
        store, rs_store = _make_workspace(tmp_path)
        # FakeExecutor with no manifest_payloads — manifest file won't be written
        executor = FakeExecutor()
        orch = Orchestrator(executor, store, rs_store)

        state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents())

        assert state.status == "failed"
        assert state.tasks["a"].status == "failed"

    def test_no_manifest_no_dynamic_inputs(self, tmp_path) -> None:
        """Tasks without output_manifest produce empty dynamic_outputs."""
        tasks = [
            _task("a", outputs=["output/a.txt"]),
            _task("b", depends_on=["a"], outputs=["output/b.txt"]),
        ]
        wf = _workflow(tasks)
        store, rs_store = _make_workspace(tmp_path)
        orch = Orchestrator(FakeExecutor(), store, rs_store)

        state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents())

        assert state.status == "succeeded"
        assert state.tasks["a"].dynamic_outputs == []
        assert state.tasks["b"].dynamic_outputs == []

    def test_manifest_persisted_across_resume(self, tmp_path) -> None:
        """Dynamic outputs are saved to run state and survive resume."""
        manifest_path = "output/a-manifest.json"
        dynamic_files = ["src/new.py"]
        tasks = [
            _task("a", outputs=["output/a.txt"], output_manifest=manifest_path),
            _task("b", depends_on=["a"], outputs=["output/b.txt"]),
        ]
        wf = _workflow(tasks)
        store, rs_store = _make_workspace(tmp_path)
        # First run: a succeeds with manifest, b fails
        fake = FakeExecutor(behaviors={"b": "fail"}, manifest_payloads={"a": dynamic_files})
        orch = Orchestrator(fake, store, rs_store)

        state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents())
        assert state.tasks["a"].status == "succeeded"
        assert state.tasks["a"].dynamic_outputs == dynamic_files
        run_id = state.run_id

        # Resume: load state; a is skipped; dynamic outputs should still flow to b
        received_dynamic: list[list[str]] = []

        class RecordingExecutor(Executor):
            def __init__(self, fake: FakeExecutor):
                self._fake = fake

            def execute(self, ctx: TaskContext) -> TaskResult:
                if ctx.task_id == "b":
                    received_dynamic.append(list(ctx.dynamic_input_paths))
                return self._fake.execute(ctx)

        fake2 = FakeExecutor()
        existing = rs_store.load(run_id)
        existing = rs_store.prepare_resume(existing, wf)
        orch2 = Orchestrator(RecordingExecutor(fake2), store, rs_store)
        state2 = orch2.run(wf, _fake_reposets(str(tmp_path)), _fake_agents(), run_state=existing)

        assert state2.status == "succeeded"
        assert len(received_dynamic) == 1
        received_basenames = [p.split("/")[-1] for p in received_dynamic[0]]
        assert received_basenames == ["new.py"]


class TestCancel:
    def test_cancel_fn_halts_execution(self, tmp_path) -> None:
        tasks = [
            _task("a", outputs=["output/a.txt"]),
            _task("b", depends_on=["a"], outputs=["output/b.txt"]),
        ]
        wf = _workflow(tasks)
        store, rs_store = _make_workspace(tmp_path)
        executor = FakeExecutor()

        # Cancel immediately
        orch = Orchestrator(executor, store, rs_store, cancel_fn=lambda: True)
        state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents())

        assert state.status == "cancelled"


class TestAgentCwd:
    """The engine resolves the agent working directory (its cwd) and threads it
    into the TaskContext so relative output paths land inside the workspace."""

    class _RecordingExecutor(Executor):
        def __init__(self) -> None:
            self.contexts: list[TaskContext] = []

        def execute(self, ctx: TaskContext) -> TaskResult:
            self.contexts.append(ctx)
            for p in ctx.output_paths:
                Path(p).parent.mkdir(parents=True, exist_ok=True)
                Path(p).write_text("output")
            return TaskResult(task_id=ctx.task_id, status="succeeded", attempts=1)

    def test_cwd_defaults_to_workspace_root(self, tmp_path) -> None:
        wf = _workflow([_task("a", outputs=["output/a.txt"])])
        store, rs_store = _make_workspace(tmp_path)
        rec = self._RecordingExecutor()
        orch = Orchestrator(rec, store, rs_store)

        state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents())
        assert state.status == "succeeded"
        assert rec.contexts[0].cwd == store.resolve(".")

    def test_working_dir_override_is_honored(self, tmp_path) -> None:
        (tmp_path / "sub").mkdir()
        wf = _workflow([_task("a", outputs=["output/a.txt"])])
        store, rs_store = _make_workspace(tmp_path)
        rec = self._RecordingExecutor()
        orch = Orchestrator(rec, store, rs_store)

        agents = {"ag": AgentSpec(executor="fake", working_dir="sub")}
        state = orch.run(wf, _fake_reposets(str(tmp_path)), agents)
        assert state.status == "succeeded"
        assert rec.contexts[0].cwd == store.resolve("sub")


# ---------------------------------------------------------------------------
# Claude quota exhaustion — engine integration tests
# ---------------------------------------------------------------------------


class TestQuotaExhaustion:
    """Verify engine wait/retry/fail behaviour on claude_quota_exhausted results."""

    def _orch(
        self, tmp_path, sleeps: list, *, quota_max_wait: float = 3600, quota_poll: float = 1
    ) -> tuple:
        store, rs_store = _make_workspace(tmp_path)
        sleeper_calls: list[float] = []

        def fake_sleeper(s: float) -> None:
            sleeps.append(s)
            sleeper_calls.append(s)

        orch = Orchestrator(
            FakeExecutor(),  # replaced per-test
            store,
            rs_store,
            sleeper=fake_sleeper,
            quota_max_wait_seconds=quota_max_wait,
            quota_poll_seconds=quota_poll,
        )
        return orch, store, rs_store, sleeps

    def test_quota_exhausted_once_then_succeeds(self, tmp_path) -> None:
        """Engine waits and re-runs task after one quota exhaustion; run succeeds."""
        wf = _workflow([_task("a", outputs=["output/a.txt"])])
        store, rs_store = _make_workspace(tmp_path)
        sleeps: list[float] = []

        executor = FakeExecutor(quota_exhausted_tasks={"a": 1})
        orch = Orchestrator(
            executor,
            store,
            rs_store,
            sleeper=sleeps.append,
            quota_max_wait_seconds=3600,
            quota_poll_seconds=10,
        )

        state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents())

        assert state.status == "succeeded"
        assert len(sleeps) == 1  # slept once during quota wait
        assert sleeps[0] == 10  # slept for quota_poll_seconds

    def test_quota_exhausted_multiple_times_then_succeeds(self, tmp_path) -> None:
        """Engine retries until quota lifts (N waits), then succeeds."""
        wf = _workflow([_task("a", outputs=["output/a.txt"])])
        store, rs_store = _make_workspace(tmp_path)
        sleeps: list[float] = []

        executor = FakeExecutor(quota_exhausted_tasks={"a": 3})
        orch = Orchestrator(
            executor,
            store,
            rs_store,
            sleeper=sleeps.append,
            quota_max_wait_seconds=3600,
            quota_poll_seconds=5,
        )

        state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents())

        assert state.status == "succeeded"
        assert len(sleeps) == 3

    def test_quota_max_wait_exceeded_fails_run(self, tmp_path) -> None:
        """When quota_max_wait expires the run fails instead of waiting forever."""
        import time

        wf = _workflow([_task("a", outputs=["output/a.txt"])])
        store, rs_store = _make_workspace(tmp_path)
        sleeps: list[float] = []

        # Use a wall clock that advances 1000 seconds on each call so the second
        # quota check always sees elapsed > max_wait.
        _calls = [0]
        _start = time.time()

        def _fast_clock():
            from datetime import UTC, datetime

            _calls[0] += 1
            return datetime.fromtimestamp(_start + _calls[0] * 1000, tz=UTC)

        executor = FakeExecutor(quota_exhausted_tasks={"a": 99})  # exhausted indefinitely
        orch = Orchestrator(
            executor,
            store,
            rs_store,
            sleeper=sleeps.append,
            clock=_fast_clock,
            quota_max_wait_seconds=500,  # expires after first 1000-second advance
            quota_poll_seconds=10,
        )

        state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents())

        assert state.status == "failed"

    def test_quota_timer_resets_on_task_success(self, tmp_path) -> None:
        """Quota timer resets after a successful task; second exhaustion gets a fresh window."""
        tasks = [
            _task("a", outputs=["output/a.txt"]),
            _task("b", depends_on=["a"], outputs=["output/b.txt"]),
        ]
        wf = _workflow(tasks)
        store, rs_store = _make_workspace(tmp_path)
        sleeps: list[float] = []

        # Task a exhausts quota once; task b exhausts quota once — each should get a full window.
        executor = FakeExecutor(quota_exhausted_tasks={"a": 1, "b": 1})
        orch = Orchestrator(
            executor,
            store,
            rs_store,
            sleeper=sleeps.append,
            quota_max_wait_seconds=3600,
            quota_poll_seconds=10,
        )

        state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents())

        assert state.status == "succeeded"
        assert len(sleeps) == 2  # one wait per task

    def test_quota_does_not_consume_retry_attempts(self, tmp_path) -> None:
        """Quota exhaustion bypasses the retry loop; max_attempts is preserved for real failures."""
        wf = _workflow([_task("a", outputs=["output/a.txt"], retries=RetryPolicy(max_attempts=1))])
        store, rs_store = _make_workspace(tmp_path)
        sleeps: list[float] = []

        executor = FakeExecutor(quota_exhausted_tasks={"a": 2})
        orch = Orchestrator(
            executor,
            store,
            rs_store,
            sleeper=sleeps.append,
            quota_max_wait_seconds=3600,
            quota_poll_seconds=10,
        )

        state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents())

        # Task should have succeeded after 2 quota waits despite max_attempts=1
        assert state.status == "succeeded"
        assert len(sleeps) == 2
