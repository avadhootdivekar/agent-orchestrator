"""FakeExecutor — deterministic test executor that never spawns subprocesses."""

from __future__ import annotations

import json
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
    manifest_payloads:
        Mapping of task_id -> list of artifact paths to write into the task's
        output_manifest_path (if declared). Only used when behavior is "succeed"
        and write_outputs is True.
    """

    def __init__(
        self,
        behaviors: dict[str, Behavior] | None = None,
        write_outputs: bool = True,
        manifest_payloads: dict[str, list[str]] | None = None,
    ) -> None:
        self._behaviors: dict[str, Behavior] = behaviors or {}
        self._write_outputs = write_outputs
        self._manifest_payloads: dict[str, list[str]] = manifest_payloads or {}

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

            # Write output manifest if declared and a payload is configured
            if ctx.output_manifest_path and ctx.task_id in self._manifest_payloads:
                artifacts = self._manifest_payloads[ctx.task_id]
                parent = os.path.dirname(ctx.output_manifest_path)
                if parent:
                    os.makedirs(parent, exist_ok=True)
                with open(ctx.output_manifest_path, "w") as f:
                    json.dump({"artifacts": artifacts}, f)

        return TaskResult(
            task_id=ctx.task_id,
            status="succeeded",
            attempts=1,
            exit_code=0,
        )
