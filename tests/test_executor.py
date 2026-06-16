"""Tests for executor implementations and context-hygiene contract."""

from __future__ import annotations

import os

from agent_orchestrator.executors.base import Executor
from agent_orchestrator.executors.fake import FakeExecutor
from agent_orchestrator.models import AgentSpec, TaskContext, TaskResult


def _agent(executor: str = "fake") -> AgentSpec:
    return AgentSpec(executor=executor)  # type: ignore[arg-type]


def _ctx(
    task_id: str = "t1",
    output_paths: list[str] | None = None,
    input_paths: list[str] | None = None,
    instruction_path: str = "/path/to/instr.md",
) -> TaskContext:
    return TaskContext(
        run_id="run1",
        task_id=task_id,
        agent=_agent(),
        instruction_path=instruction_path,
        input_paths=input_paths or [],
        output_paths=output_paths or [],
        repo_paths={},
        timeout_seconds=60,
    )


class TestFakeExecutor:
    def test_succeed_returns_succeeded(self) -> None:
        ex = FakeExecutor()
        result = ex.execute(_ctx())
        assert result.status == "succeeded"
        assert result.task_id == "t1"

    def test_succeed_writes_output_files(self, tmp_path) -> None:
        out = str(tmp_path / "subdir" / "out.txt")
        ex = FakeExecutor(write_outputs=True)
        result = ex.execute(_ctx(output_paths=[out]))
        assert result.status == "succeeded"
        assert os.path.exists(out)
        assert "fake output for t1" in open(out).read()

    def test_succeed_without_write_outputs(self, tmp_path) -> None:
        out = str(tmp_path / "out.txt")
        ex = FakeExecutor(write_outputs=False)
        result = ex.execute(_ctx(output_paths=[out]))
        assert result.status == "succeeded"
        assert not os.path.exists(out)

    def test_fail_behavior(self) -> None:
        ex = FakeExecutor(behaviors={"t1": "fail"})
        result = ex.execute(_ctx())
        assert result.status == "failed"
        assert result.exit_code == 1
        assert result.error == "fake failure"

    def test_timeout_behavior(self) -> None:
        ex = FakeExecutor(behaviors={"t1": "timeout"})
        result = ex.execute(_ctx())
        assert result.status == "timed_out"
        assert result.error == "fake timeout"

    def test_default_behavior_is_succeed(self) -> None:
        ex = FakeExecutor(behaviors={"other": "fail"})
        result = ex.execute(_ctx(task_id="t1"))  # t1 not in behaviors
        assert result.status == "succeeded"

    def test_creates_parent_dirs_for_output(self, tmp_path) -> None:
        out = str(tmp_path / "a" / "b" / "c" / "out.txt")
        ex = FakeExecutor()
        ex.execute(_ctx(output_paths=[out]))
        assert os.path.exists(out)


class RecordingExecutor(Executor):
    """Test double that records the last TaskContext it received."""

    def __init__(self) -> None:
        self.last_ctx: TaskContext | None = None

    def execute(self, ctx: TaskContext) -> TaskResult:
        self.last_ctx = ctx
        return TaskResult(task_id=ctx.task_id, status="succeeded", attempts=1)


class TestContextHygiene:
    def test_task_context_contains_no_file_contents(self, tmp_path) -> None:
        """NFR-1: TaskContext must not contain artifact file contents."""
        artifact = tmp_path / "secret.txt"
        artifact.write_text("SECRET_CONTENT_12345")

        ctx = TaskContext(
            run_id="r1",
            task_id="t1",
            agent=_agent(),
            instruction_path=str(artifact),
            input_paths=[str(artifact)],
            output_paths=[],
            repo_paths={},
            timeout_seconds=60,
        )

        ctx_json = ctx.model_dump_json()
        assert "SECRET_CONTENT_12345" not in ctx_json

    def test_recording_executor_receives_only_paths(self, tmp_path) -> None:
        """Executor receives TaskContext with paths only (no content)."""
        artifact = tmp_path / "data.txt"
        artifact.write_text("SENSITIVE_DATA_XYZ")

        rec = RecordingExecutor()
        ctx = TaskContext(
            run_id="r1",
            task_id="t1",
            agent=_agent(),
            instruction_path=str(artifact),
            input_paths=[str(artifact)],
            output_paths=[],
            repo_paths={},
            timeout_seconds=60,
        )
        rec.execute(ctx)

        assert rec.last_ctx is not None
        ctx_json = rec.last_ctx.model_dump_json()
        assert "SENSITIVE_DATA_XYZ" not in ctx_json
        # Path itself should be present
        assert str(artifact) in ctx_json
