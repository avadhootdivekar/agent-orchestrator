"""Tests for the engine-owned live run summary (summarizer.py).

A local stub `Executor` stands in for the Haiku call: it writes the summary output file and
reports a fixed cost, so no subprocess or network is involved and everything is deterministic.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from agent_orchestrator.engine import Orchestrator
from agent_orchestrator.executors.base import Executor
from agent_orchestrator.executors.fake import FakeExecutor
from agent_orchestrator.models import RunState, TaskContext, TaskResult, TaskRunState
from agent_orchestrator.summarizer import (
    SUMMARY_COST_THRESHOLD_USD,
    SUMMARY_FILE,
    SUMMARY_MAX_COST_FRACTION,
    SUMMARY_META_FILE,
    SUMMARY_MODEL,
    RunSummarizer,
    build_summary_agent,
    read_summary,
)


class StubSummaryExecutor(Executor):
    """Writes a fixed digest to the requested output path; records every call."""

    def __init__(self, cost: float = 0.01, status: str = "succeeded", write: bool = True) -> None:
        self.cost = cost
        self.status = status
        self.write = write
        self.contexts: list[TaskContext] = []

    def execute(self, ctx: TaskContext) -> TaskResult:
        self.contexts.append(ctx)
        if self.write:
            Path(ctx.output_paths[0]).write_text(f"digest #{len(self.contexts)}", encoding="utf-8")
        return TaskResult(
            task_id=ctx.task_id,
            status=self.status,  # type: ignore[arg-type]
            attempts=1,
            cost_usd=self.cost,
        )


def _state(settled: int, cost_each: float, pending: int = 0) -> RunState:
    tasks = {
        f"t{i}": TaskRunState(status="succeeded", cumulative_cost_usd=cost_each)
        for i in range(settled)
    }
    tasks.update({f"p{i}": TaskRunState(status="pending") for i in range(pending)})
    return RunState(
        run_id="r1",
        workflow_id="wf",
        repo_set="rs",
        started_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
        tasks=tasks,
    )


def _wait(s: RunSummarizer) -> None:
    s._join()


class TestPolicy:
    def test_threshold_is_five_dollars_and_model_is_haiku_alias(self) -> None:
        assert SUMMARY_COST_THRESHOLD_USD == 5.0
        assert SUMMARY_MODEL == "haiku"
        agent = build_summary_agent()
        assert agent.model == "haiku"
        assert "Bash" in agent.forced_disallowed_tools

    def test_not_user_configurable(self) -> None:
        from agent_orchestrator.models import WorkflowSpec
        from agent_orchestrator.project_config import ProjectConfig

        for model in (ProjectConfig, WorkflowSpec):
            assert not [f for f in model.model_fields if "summar" in f.lower()]


class TestRefreshCadence:
    def test_below_threshold_never_calls_model(self, tmp_path: Path) -> None:
        ex = StubSummaryExecutor()
        s = RunSummarizer(ex, refresh_every=1)
        s.on_task_settled(_state(10, 0.4), str(tmp_path), [])  # $4.00 < $5
        _wait(s)
        s.finalize(_state(10, 0.4), str(tmp_path), [])
        assert ex.contexts == []
        assert not (tmp_path / "summary").exists()

    def test_above_threshold_writes_summary_and_meta(self, tmp_path: Path) -> None:
        ex = StubSummaryExecutor()
        s = RunSummarizer(ex, refresh_every=1)
        s.on_task_settled(_state(10, 1.0, pending=3), str(tmp_path), ["p0", "p1", "p2"])
        _wait(s)
        got = read_summary(tmp_path)
        assert got["available"] and got["text"] == "digest #1"
        assert got["meta"]["final"] is False
        assert got["meta"]["run_cost_usd"] == 10.0
        ctx = json.loads(Path(ex.contexts[0].input_paths[0]).read_text())
        assert ctx["pending"] == ["p0", "p1", "p2"] and ctx["run_cost_usd"] == 10.0
        assert ex.contexts[0].agent.model == "haiku"

    def test_refreshes_only_every_n_settled_tasks(self, tmp_path: Path) -> None:
        ex = StubSummaryExecutor(cost=0.0)
        s = RunSummarizer(ex, refresh_every=5)
        s.on_task_settled(_state(6, 1.0), str(tmp_path), [])
        _wait(s)
        s.on_task_settled(_state(8, 1.0), str(tmp_path), [])  # only +2 settled
        _wait(s)
        assert len(ex.contexts) == 1
        s.on_task_settled(_state(11, 1.0), str(tmp_path), [])  # +5
        _wait(s)
        assert len(ex.contexts) == 2
        # Second refresh got the previous digest as an input artifact (model-side continuity).
        assert any(p.endswith("previous.md") for p in ex.contexts[1].input_paths)

    def test_interim_refresh_stops_at_cost_cap_but_final_still_runs(self, tmp_path: Path) -> None:
        # $10 run; each summary call costs $1 (10% > 2% cap) -> second interim is skipped.
        ex = StubSummaryExecutor(cost=1.0)
        s = RunSummarizer(ex, refresh_every=1)
        s.on_task_settled(_state(10, 1.0), str(tmp_path), [])
        _wait(s)
        s.on_task_settled(_state(12, 1.0), str(tmp_path), [])
        _wait(s)
        assert len(ex.contexts) == 1
        assert s._summary_cost_usd >= SUMMARY_MAX_COST_FRACTION * 10
        s.finalize(_state(12, 1.0), str(tmp_path), [])
        assert len(ex.contexts) == 2
        assert read_summary(tmp_path)["meta"]["final"] is True

    def test_finalize_is_idempotent_once_final(self, tmp_path: Path) -> None:
        ex = StubSummaryExecutor()
        s = RunSummarizer(ex)
        st = _state(10, 1.0)
        s.finalize(st, str(tmp_path), [])
        s.finalize(st, str(tmp_path), [])
        assert len(ex.contexts) == 1

    def test_existing_summary_latches_expensive_across_resume(self, tmp_path: Path) -> None:
        (tmp_path / "summary").mkdir()
        (tmp_path / "summary" / SUMMARY_FILE).write_text("old")
        ex = StubSummaryExecutor()
        s = RunSummarizer(ex)
        s.finalize(_state(2, 0.1), str(tmp_path), [])  # $0.20 now, but a summary exists
        assert len(ex.contexts) == 1


class TestFailureIsolation:
    def test_failed_or_missing_output_keeps_previous_summary(self, tmp_path: Path) -> None:
        summary_dir = tmp_path / "summary"
        summary_dir.mkdir()
        (summary_dir / SUMMARY_FILE).write_text("keep me")
        for ex in (StubSummaryExecutor(status="failed"), StubSummaryExecutor(write=False)):
            RunSummarizer(ex).finalize(_state(10, 1.0), str(tmp_path), [])
            assert (summary_dir / SUMMARY_FILE).read_text() == "keep me"
        assert not (summary_dir / SUMMARY_META_FILE).exists()

    def test_executor_exception_never_propagates(self, tmp_path: Path) -> None:
        class Boom(Executor):
            def execute(self, ctx: TaskContext) -> TaskResult:
                raise RuntimeError("haiku down")

        RunSummarizer(Boom()).finalize(_state(10, 1.0), str(tmp_path), [])  # must not raise

    def test_read_summary_missing(self, tmp_path: Path) -> None:
        assert read_summary(tmp_path) == {"available": False, "text": "", "meta": None}


class _CostlyFake(FakeExecutor):
    """FakeExecutor whose every task reports a fixed actual cost."""

    def __init__(self, cost: float) -> None:
        super().__init__()
        self._cost = cost

    def execute(self, ctx: TaskContext) -> TaskResult:
        return (
            super()
            .execute(ctx)
            .model_copy(update={"cost_usd": self._cost, "actuals_available": True})
        )


class TestEngineIntegration:
    def test_expensive_run_gets_live_and_final_summary(
        self, workspace: Path, make_workflow: Callable, make_orchestrator: Callable
    ) -> None:
        orch, reposets, agents = make_orchestrator(_CostlyFake(2.0))
        stub = StubSummaryExecutor()
        orch = Orchestrator(
            orch._executor,
            orch._store,
            orch._runstate,
            summarizer=RunSummarizer(stub, refresh_every=2),
        )
        wf = make_workflow(
            [
                {"id": "a", "outputs": ["o/a"]},
                {"id": "b", "depends_on": ["a"], "outputs": ["o/b"]},
                {"id": "c", "depends_on": ["b"], "outputs": ["o/c"]},
                {"id": "d", "depends_on": ["c"], "outputs": ["o/d"]},
            ]
        )
        state = orch.run(wf, reposets, agents)
        assert state.status == "succeeded"
        run_dir = workspace / ".orchestrator" / "runs" / state.run_id
        got = read_summary(run_dir)
        assert got["available"] and got["meta"]["final"] is True
        assert got["meta"]["run_cost_usd"] == 8.0
        assert len(stub.contexts) >= 2  # at least one live refresh + the final one

    def test_cheap_run_makes_no_summary_call(
        self, workspace: Path, make_workflow: Callable, make_orchestrator: Callable
    ) -> None:
        orch, reposets, agents = make_orchestrator(_CostlyFake(0.1))
        stub = StubSummaryExecutor()
        orch = Orchestrator(
            orch._executor, orch._store, orch._runstate, summarizer=RunSummarizer(stub)
        )
        wf = make_workflow([{"id": "a", "outputs": ["o/a"]}, {"id": "b", "outputs": ["o/b"]}])
        state = orch.run(wf, reposets, agents)
        assert state.status == "succeeded"
        assert stub.contexts == []
        assert not (workspace / ".orchestrator" / "runs" / state.run_id / "summary").exists()


class TestOuterBoundaries:
    """CLI (`ao summary`) and dashboard API read the same files the engine wrote."""

    def _seed(self, ws: Path, run_id: str = "r-1") -> Path:
        run_dir = ws / ".orchestrator" / "runs" / run_id
        RunSummarizer(StubSummaryExecutor()).finalize(_state(10, 1.0), str(run_dir), [])
        return run_dir

    def test_cli_summary_shows_digest_and_json(self, tmp_path: Path) -> None:
        from typer.testing import CliRunner

        from agent_orchestrator.cli import app

        self._seed(tmp_path)
        runner = CliRunner()
        out = runner.invoke(app, ["summary", "--run-id", "r-1", "--workspace", str(tmp_path)])
        assert out.exit_code == 0, out.output
        assert "digest #1" in out.output and "final summary" in out.output
        js = runner.invoke(
            app, ["summary", "--run-id", "r-1", "--workspace", str(tmp_path), "--json"]
        )
        assert json.loads(js.output)["meta"]["final"] is True

    def test_cli_summary_absent_exits_nonzero(self, tmp_path: Path) -> None:
        from typer.testing import CliRunner

        from agent_orchestrator.cli import app

        (tmp_path / ".orchestrator" / "runs" / "cheap").mkdir(parents=True)
        res = CliRunner().invoke(
            app, ["summary", "--run-id", "cheap", "--workspace", str(tmp_path)]
        )
        assert res.exit_code == 1 and "No summary" in res.output

    def test_dashboard_endpoint(self, tmp_path: Path) -> None:
        from unittest.mock import MagicMock

        import pytest

        pytest.importorskip("fastapi")
        from fastapi.testclient import TestClient

        from agent_orchestrator.project_config import ProjectConfig
        from agent_orchestrator.ui.app import API_PREFIX, create_app
        from agent_orchestrator.ui.service import DashboardService

        self._seed(tmp_path)
        (tmp_path / ".orchestrator" / "runs" / "cheap").mkdir()
        svc = DashboardService(
            str(tmp_path), supervisor=MagicMock(), project_config=ProjectConfig()
        )
        with TestClient(create_app(svc), base_url="http://127.0.0.1") as c:
            ok = c.get(f"{API_PREFIX}/runs/r-1/summary").json()
            none = c.get(f"{API_PREFIX}/runs/cheap/summary").json()
            bad = c.get(f"{API_PREFIX}/runs/..%2F..%2Fetc/summary")
        assert ok["available"] and ok["text"] == "digest #1"
        assert none["available"] is False
        assert bad.status_code == 404
