"""ClaudeCliExecutor — runs tasks by invoking the `claude` CLI subprocess."""

from __future__ import annotations

import subprocess

from ..models import TaskContext, TaskResult
from .base import Executor


class ClaudeCliExecutor(Executor):
    """Executes tasks by spawning a `claude` CLI subprocess.

    The prompt is assembled from paths only (NFR-1 invariant):
    - instruction_path: path to the instruction markdown file
    - input_paths: paths to input artifacts
    - output_paths: paths the agent should write outputs to
    - repo_paths: id=path mappings for repos in the RepoSet
    """

    def execute(self, ctx: TaskContext) -> TaskResult:
        prompt = ctx.agent.prompt_template.format(
            instruction=ctx.instruction_path,
            inputs=" ".join(ctx.input_paths),
            outputs=" ".join(ctx.output_paths),
            repos=" ".join(f"{k}={v}" for k, v in ctx.repo_paths.items()),
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
            if res.returncode == 0:
                return TaskResult(
                    task_id=ctx.task_id,
                    status="succeeded",
                    attempts=1,
                    exit_code=0,
                )
            err_tail = (res.stderr or res.stdout or "")[-500:]
            return TaskResult(
                task_id=ctx.task_id,
                status="failed",
                attempts=1,
                exit_code=res.returncode,
                error=err_tail,
            )
        except subprocess.TimeoutExpired:
            return TaskResult(
                task_id=ctx.task_id,
                status="timed_out",
                attempts=1,
                error="timeout",
            )
