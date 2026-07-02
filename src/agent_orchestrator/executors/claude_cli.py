"""ClaudeCliExecutor — runs tasks by invoking the `claude` CLI subprocess."""

from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

from ..models import TaskContext, TaskResult
from .base import Executor

# Sentinel flag appended to the CLI command to request JSON output.
# Added idempotently — see _ensure_output_format_json().
_OUTPUT_FORMAT_FLAG = "--output-format"
_OUTPUT_FORMAT_VALUE = "json"


def _write_capture(output_dir: str, stdout: str, stderr: str) -> None:
    """Create output_dir and write stdout.txt / stderr.txt (empty files allowed)."""
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    Path(os.path.join(output_dir, "stdout.txt")).write_text(stdout, encoding="utf-8")
    Path(os.path.join(output_dir, "stderr.txt")).write_text(stderr, encoding="utf-8")


def _ensure_output_format_json(argv: list[str]) -> list[str]:
    """Return argv with ``--output-format json`` appended, unless already present.

    Checks for both ``--output-format json`` (two-arg form) and
    ``--output-format=json`` (single-arg form).
    """
    for arg in argv:
        if arg == _OUTPUT_FORMAT_FLAG or arg.startswith(f"{_OUTPUT_FORMAT_FLAG}="):
            # Already specified — honour whatever value the caller set.
            return argv
    return argv + [_OUTPUT_FORMAT_FLAG, _OUTPUT_FORMAT_VALUE]


def _parse_iso_to_epoch(value: str) -> float | None:
    """Parse an ISO 8601 datetime string to a Unix epoch float.  Returns None on error."""
    try:
        # Python 3.7+ fromisoformat doesn't handle trailing 'Z'; replace it.
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.timestamp()
    except (ValueError, TypeError):
        return None


def parse_usage_and_429(
    stdout: str,
    stderr: str,
    returncode: int,
    now_epoch: float,  # noqa: ARG001 — reserved for future relative retry_after
) -> dict:
    """Parse token usage and 429 signal from a ``claude`` CLI invocation.

    This is a pure function — no side effects, fully testable in isolation.

    Parameters
    ----------
    stdout:
        Raw stdout text from the subprocess.
    stderr:
        Raw stderr text from the subprocess.
    returncode:
        Process exit code.
    now_epoch:
        Current time as Unix epoch float (injected for determinism in tests).

    Returns
    -------
    dict with keys:
        actuals_available, provider_rate_limited, provider_retry_after_epoch,
        input_tokens, output_tokens, cache_creation_input_tokens,
        cache_read_input_tokens
    """
    result: dict = {
        "actuals_available": False,
        "provider_rate_limited": False,
        "provider_retry_after_epoch": None,
        "input_tokens": None,
        "output_tokens": None,
        "cache_creation_input_tokens": None,
        "cache_read_input_tokens": None,
    }

    # --- 429 detection from process-level signals (no JSON needed) ---
    combined_text = (stdout + "\n" + stderr).lower()
    if returncode != 0 and (
        "429" in combined_text or "rate_limit" in combined_text or "rate limit" in combined_text
    ):
        result["provider_rate_limited"] = True
        # Still attempt JSON parse below to extract retry_after.

    # --- JSON parse ---
    parsed: dict | None = None
    try:
        parsed = json.loads(stdout)
    except (json.JSONDecodeError, ValueError):
        # Stdout is not JSON (e.g. streaming mode, partial output, empty).
        # Keep actuals_available=False; return early if we already detected 429.
        return result

    if not isinstance(parsed, dict):
        return result

    # --- Usage extraction ---
    usage = parsed.get("usage")
    if isinstance(usage, dict):
        in_tok = usage.get("input_tokens")
        out_tok = usage.get("output_tokens")
        cache_create = usage.get("cache_creation_input_tokens")
        cache_read = usage.get("cache_read_input_tokens")

        if in_tok is not None or out_tok is not None:
            result["actuals_available"] = True
            result["input_tokens"] = in_tok
            result["output_tokens"] = out_tok
            result["cache_creation_input_tokens"] = cache_create
            result["cache_read_input_tokens"] = cache_read

    # --- 429 detection from JSON payload ---
    error_obj = parsed.get("error")
    if isinstance(error_obj, dict):
        err_type = str(error_obj.get("type", "")).lower()
        err_msg = str(error_obj.get("message", "")).lower()
        if "rate_limit" in err_type or ("rate" in err_msg and "limit" in err_msg):
            result["provider_rate_limited"] = True

        # Retry-after from JSON error: prefer retry_after (epoch float), fallback reset_at (ISO)
        retry_after_raw = error_obj.get("retry_after")
        if retry_after_raw is not None:
            try:
                result["provider_retry_after_epoch"] = float(retry_after_raw)
            except (ValueError, TypeError):
                pass

        if result["provider_retry_after_epoch"] is None:
            reset_at = error_obj.get("reset_at")
            if isinstance(reset_at, str):
                result["provider_retry_after_epoch"] = _parse_iso_to_epoch(reset_at)

    return result


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

    ``--output-format json`` is appended idempotently so that token usage
    can be extracted from the structured response (T-1m9744).
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

        # Inject --model if specified in agent spec and not already in argv.
        if ctx.agent.model and "--model" not in argv and "-m" not in argv:
            argv = argv + ["--model", ctx.agent.model]

        # Inject --max-turns from effort level if specified and not already in argv.
        if ctx.agent.effort:
            from ..models import EFFORT_MAX_TURNS

            if "--max-turns" not in argv:
                argv = argv + ["--max-turns", str(EFFORT_MAX_TURNS[ctx.agent.effort])]

        # Ensure JSON output so we can extract token usage (T-1m9744).
        argv = _ensure_output_format_json(argv)

        try:
            res = subprocess.run(
                argv,
                timeout=ctx.timeout_seconds,
                capture_output=True,
                text=True,
            )
            _write_capture(ctx.output_dir, res.stdout or "", res.stderr or "")

            usage_info = parse_usage_and_429(
                stdout=res.stdout or "",
                stderr=res.stderr or "",
                returncode=res.returncode,
                now_epoch=time.time(),
            )

            if res.returncode == 0:
                return TaskResult(
                    task_id=ctx.task_id,
                    status="succeeded",
                    attempts=1,
                    exit_code=0,
                    output_artifact_path=ctx.output_dir,
                    input_tokens=usage_info["input_tokens"],
                    output_tokens=usage_info["output_tokens"],
                    cache_creation_input_tokens=usage_info["cache_creation_input_tokens"],
                    cache_read_input_tokens=usage_info["cache_read_input_tokens"],
                    actuals_available=usage_info["actuals_available"],
                    provider_rate_limited=usage_info["provider_rate_limited"],
                    provider_retry_after_epoch=usage_info["provider_retry_after_epoch"],
                )
            err_tail = (res.stderr or res.stdout or "")[-500:]
            return TaskResult(
                task_id=ctx.task_id,
                status="failed",
                attempts=1,
                exit_code=res.returncode,
                error=err_tail,
                output_artifact_path=ctx.output_dir,
                input_tokens=usage_info["input_tokens"],
                output_tokens=usage_info["output_tokens"],
                cache_creation_input_tokens=usage_info["cache_creation_input_tokens"],
                cache_read_input_tokens=usage_info["cache_read_input_tokens"],
                actuals_available=usage_info["actuals_available"],
                provider_rate_limited=usage_info["provider_rate_limited"],
                provider_retry_after_epoch=usage_info["provider_retry_after_epoch"],
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
