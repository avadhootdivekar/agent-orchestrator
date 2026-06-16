"""FakeExecutor — deterministic test executor that never spawns subprocesses."""

from __future__ import annotations

import os
from typing import Literal

from ..models import TaskContext, TaskResult
from .base import Executor

Behavior = Literal["succeed", "fail", "timeout"]


class FakeExecutor(Executor):
    """Controllable executor for unit/integration tests.

    Parameters
    ----------
    behaviors:
        Mapping of task_id -> Behavior. Tasks not listed default to "succeed".
    write_outputs:
        When True (default) and behavior is "succeed", creates stub output files
        so artifact existence checks pass.
    """

    def __init__(
        self,
        behaviors: dict[str, Behavior] | None = None,
        write_outputs: bool = True,
    ) -> None:
        self._behaviors: dict[str, Behavior] = behaviors or {}
        self._write_outputs = write_outputs

    def execute(self, ctx: TaskContext) -> TaskResult:
        behavior = self._behaviors.get(ctx.task_id, "succeed")

        if behavior == "timeout":
            return TaskResult(
                task_id=ctx.task_id,
                status="timed_out",
                attempts=1,
                error="fake timeout",
            )

        if behavior == "fail":
            return TaskResult(
                task_id=ctx.task_id,
                status="failed",
                attempts=1,
                exit_code=1,
                error="fake failure",
            )

        # succeed
        if self._write_outputs:
            for path in ctx.output_paths:
                parent = os.path.dirname(path)
                if parent:
                    os.makedirs(parent, exist_ok=True)
                with open(path, "w") as f:
                    f.write(f"fake output for {ctx.task_id}\n")

        return TaskResult(
            task_id=ctx.task_id,
            status="succeeded",
            attempts=1,
            exit_code=0,
        )
