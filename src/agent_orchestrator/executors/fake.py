"""FakeExecutor — deterministic test executor that never spawns subprocesses."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal

from ..models import TaskContext, TaskResult
from .base import Executor

Behavior = Literal["succeed", "fail", "timeout"]


def _write_capture_stubs(output_dir: str, task_id: str) -> None:
    """Create output_dir and write stub stdout.txt / stderr.txt (FR-4 contract)."""
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    Path(os.path.join(output_dir, "stdout.txt")).write_text(
        f"fake stdout for {task_id}\n", encoding="utf-8"
    )
    Path(os.path.join(output_dir, "stderr.txt")).write_text("", encoding="utf-8")


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
    emit_payloads:
        Mapping of task_id -> dict to write as a task manifest
        (``{"tasks": [...]}`` JSON) at the task's ``task_manifest_path`` on
        success.  Drives Area-2 emit_tasks tests (T-5isej3).
    gate_payloads:
        Mapping of task_id -> list[bool]. On the K-th invocation of a gate task
        (1-indexed), writes ``{"continue": <bool>}`` to the task's
        ``gate_output_path`` for that iteration, letting a test script
        "continue, continue, stop".  The path is the LoopSpec.gate_output_path
        suffixed per iteration — the caller must pass in a dict that maps the
        suffixed task id to the gate bool for *that* invocation.  For simplicity,
        the mapping uses the base gate_task_id and a positional list of bools;
        the executor writes the correct path based on invocation count.
    """

    def __init__(
        self,
        behaviors: dict[str, Behavior] | None = None,
        write_outputs: bool = True,
        manifest_payloads: dict[str, list[str]] | None = None,
        emit_payloads: dict[str, dict] | None = None,
        gate_payloads: dict[str, list[bool]] | None = None,
    ) -> None:
        self._behaviors: dict[str, Behavior] = behaviors or {}
        self._write_outputs = write_outputs
        self._manifest_payloads: dict[str, list[str]] = manifest_payloads or {}
        self._emit_payloads: dict[str, dict] = emit_payloads or {}
        self._gate_payloads: dict[str, list[bool]] = gate_payloads or {}
        # invocation counters for gate tasks: base_id -> count (0-indexed)
        self._gate_invocations: dict[str, int] = {}

    def execute(self, ctx: TaskContext) -> TaskResult:
        behavior = self._behaviors.get(ctx.task_id, "succeed")

        # Always write capture stubs when output_dir is set (FR-4 contract).
        # This mirrors ClaudeCliExecutor behaviour so integration tests exercise
        # the same path regardless of which executor is used.
        if ctx.output_dir:
            _write_capture_stubs(ctx.output_dir, ctx.task_id)

        if behavior == "timeout":
            return TaskResult(
                task_id=ctx.task_id,
                status="timed_out",
                attempts=1,
                error="fake timeout",
                output_artifact_path=ctx.output_dir or None,
            )

        if behavior == "fail":
            return TaskResult(
                task_id=ctx.task_id,
                status="failed",
                attempts=1,
                exit_code=1,
                error="fake failure",
                output_artifact_path=ctx.output_dir or None,
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

            # Write task manifest (emit_payloads) for emit_tasks tasks (Area 2).
            # The engine passes the resolved task_manifest_path via ctx.task_manifest_path.
            if ctx.task_id in self._emit_payloads and ctx.task_manifest_path:
                payload = self._emit_payloads[ctx.task_id]
                parent = os.path.dirname(ctx.task_manifest_path)
                if parent:
                    os.makedirs(parent, exist_ok=True)
                with open(ctx.task_manifest_path, "w") as f:
                    json.dump(payload, f)

        # Write gate verdict file (gate_payloads) on the K-th invocation of a gate task.
        # The base task id (without __iterN suffix) is used as the key in gate_payloads.
        # The engine passes the iteration-suffixed, resolved gate path via ctx.gate_output_path.
        base_id = ctx.task_id.split("__iter")[0]
        if base_id in self._gate_payloads and ctx.gate_output_path:
            invoc = self._gate_invocations.get(base_id, 0)
            verdicts = self._gate_payloads[base_id]
            if invoc < len(verdicts):
                parent = os.path.dirname(ctx.gate_output_path)
                if parent:
                    os.makedirs(parent, exist_ok=True)
                with open(ctx.gate_output_path, "w") as f:
                    json.dump({"continue": verdicts[invoc]}, f)
            self._gate_invocations[base_id] = invoc + 1

        return TaskResult(
            task_id=ctx.task_id,
            status="succeeded",
            attempts=1,
            exit_code=0,
            output_artifact_path=ctx.output_dir or None,
        )
