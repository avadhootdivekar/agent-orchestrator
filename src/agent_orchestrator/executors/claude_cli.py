"""ClaudeCliExecutor — runs tasks by invoking the `claude` CLI subprocess."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from ..models import TaskContext, TaskResult
from .base import Executor


def _write_capture(output_dir: str, stdout: str, stderr: str) -> None:
    """Create output_dir and write stdout.txt / stderr.txt (empty files allowed)."""
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    Path(os.path.join(output_dir, "stdout.txt")).write_text(stdout, encoding="utf-8")
    Path(os.path.join(output_dir, "stderr.txt")).write_text(stderr, encoding="utf-8")


class ClaudeCliExecutor(Executor):
    """Executes tasks by spawning a `claude` CLI subprocess.

    The prompt is assembled from paths only (NFR-1 invariant):
    - instruction_path: path to the instruction markdown file
    - input_paths: paths to input artifacts
    - output_paths: paths the agent should write outputs to
    - repo_paths: id=path mappings for repos in the RepoSet

    Captured stdout/stderr are written to ctx.output_dir so downstream
    agents/auditors can read them by path (FR-4).  The result carries
    output_artifact_path pointing to that directory (FR-5).
    """

    def execute(self, ctx: TaskContext) -> TaskResult:
        prompt = ctx.agent.prompt_template.format(
            instruction=ctx.instruction_path,
            inputs=" ".join(ctx.input_paths),
            outputs=" ".join(ctx.output_paths),
            repos=" ".join(f"{k}={v}" for k, v in ctx.repo_paths.items()),
            dynamic_inputs=" ".join(ctx.dynamic_input_paths),
            output_manifest=ctx.output_manifest_path or "",
        )
        argv = [
            (arg.replace("{prompt}", prompt) if "{prompt}" in arg else arg)
            for arg in ctx.agent.command_template
        ] + ctx.agent.extra_args

        try:
            res = subprocess.run(
                argv,
                timeout=ctx.timeout_seconds,
                capture_output=True,
                text=True,
            )
            _write_capture(ctx.output_dir, res.stdout or "", res.stderr or "")
            if res.returncode == 0:
                return TaskResult(
                    task_id=ctx.task_id,
                    status="succeeded",
                    attempts=1,
                    exit_code=0,
                    output_artifact_path=ctx.output_dir,
                )
            err_tail = (res.stderr or res.stdout or "")[-500:]
            return TaskResult(
                task_id=ctx.task_id,
                status="failed",
                attempts=1,
                exit_code=res.returncode,
                error=err_tail,
                output_artifact_path=ctx.output_dir,
            )
        except subprocess.TimeoutExpired:
            # Write whatever partial capture is available (empty if none)
            _write_capture(ctx.output_dir, "", "")
            return TaskResult(
                task_id=ctx.task_id,
                status="timed_out",
                attempts=1,
                error="timeout",
                output_artifact_path=ctx.output_dir,
            )
