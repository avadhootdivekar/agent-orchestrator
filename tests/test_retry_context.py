"""Retry context: prior-attempts.md on retries and the outcome.json retry=false marker."""

from __future__ import annotations

import json
from pathlib import Path

from agent_orchestrator.engine import Orchestrator
from agent_orchestrator.executors.base import Executor
from agent_orchestrator.executors.prompt import build_prompt
from agent_orchestrator.models import RetryPolicy, TaskContext, TaskResult
from agent_orchestrator.retry_context import (
    MAX_PRIOR_ATTEMPTS_BYTES,
    OUTCOME_FILENAME,
    PRIOR_ATTEMPTS_FILENAME,
)
from tests.test_engine import (
    _fake_agents,
    _fake_reposets,
    _make_workspace,
    _task,
    _workflow,
)


class _Recorder(Executor):
    def __init__(self, fail_times: int, marker: str | None = None, error: str = "transient"):
        self.ctxs: list[TaskContext] = []
        self.fail_times = fail_times
        self.marker = marker
        self.error = error

    def execute(self, ctx: TaskContext) -> TaskResult:
        self.ctxs.append(ctx)
        if len(self.ctxs) <= self.fail_times:
            Path(ctx.output_dir).mkdir(parents=True, exist_ok=True)
            if self.marker is not None:
                Path(ctx.output_dir, OUTCOME_FILENAME).write_text(self.marker)
            return TaskResult(
                task_id=ctx.task_id, status="failed", attempts=1, exit_code=1, error=self.error
            )
        for p in ctx.output_paths:
            Path(p).parent.mkdir(parents=True, exist_ok=True)
            Path(p).write_text("ok")
        return TaskResult(task_id=ctx.task_id, status="succeeded", attempts=1)


def _run(tmp_path, executor, attempts=3, honor=False):
    tasks = [
        _task(
            "a",
            outputs=["output/a.txt"],
            retries=RetryPolicy(max_attempts=attempts, honor_marker=honor),
        )
    ]
    store, rs_store = _make_workspace(tmp_path)
    sleeps: list[float] = []
    orch = Orchestrator(executor, store, rs_store, sleeper=sleeps.append)
    state = orch.run(_workflow(tasks), _fake_reposets(str(tmp_path)), _fake_agents())
    return state, sleeps


def test_retry_gets_bounded_prior_attempts_context(tmp_path) -> None:
    ex = _Recorder(fail_times=1, error="boom " * 1000)
    state, _ = _run(tmp_path, ex)
    assert state.status == "succeeded"
    assert ex.ctxs[0].prior_attempts_path is None
    assert PRIOR_ATTEMPTS_FILENAME not in build_prompt(ex.ctxs[0])
    path = ex.ctxs[1].prior_attempts_path
    assert path and Path(path).exists()
    text = Path(path).read_text()
    assert len(text.encode()) <= MAX_PRIOR_ATTEMPTS_BYTES
    assert "attempt 1" in text and "stderr.txt" in text and "attempt-1" in text
    assert path in build_prompt(ex.ctxs[1])


def test_marker_short_circuits_retries(tmp_path) -> None:
    marker = json.dumps({"schema": "ao.task.outcome/v1", "retry": False, "reason": "needs human"})
    ex = _Recorder(fail_times=5, marker=marker)
    state, sleeps = _run(tmp_path, ex)
    assert state.tasks["a"].status == "failed"
    assert len(ex.ctxs) == 1
    assert sleeps == []


def test_malformed_or_true_marker_still_retries(tmp_path) -> None:
    for marker in ("{not json", json.dumps({"schema": "ao.task.outcome/v1", "retry": True})):
        ex = _Recorder(fail_times=5, marker=marker)
        _run(tmp_path / str(abs(hash(marker))), ex)
        assert len(ex.ctxs) == 3


def test_default_transient_failure_retries_and_marker_clause_gated(tmp_path) -> None:
    ex = _Recorder(fail_times=2)
    state, _ = _run(tmp_path, ex)
    assert state.status == "succeeded"
    assert len(ex.ctxs) == 3
    assert all(c.outcome_marker_path is None for c in ex.ctxs)
    ex2 = _Recorder(fail_times=0)
    _run(tmp_path / "h", ex2, honor=True)
    assert OUTCOME_FILENAME in build_prompt(ex2.ctxs[0])
