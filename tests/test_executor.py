"""Tests for executor implementations and context-hygiene contract."""

from __future__ import annotations

import json
import os
from datetime import UTC
from pathlib import Path

from agent_orchestrator.executors.base import Executor
from agent_orchestrator.executors.claude_cli import (
    _ensure_output_format_json,
    parse_usage_and_429,
)
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


# ---------------------------------------------------------------------------
# parse_usage_and_429 — pure-function unit tests (T-1m9744, AC-6)
# ---------------------------------------------------------------------------

NOW = 1_700_000_000.0  # fixed epoch for determinism


class TestParseUsageAnd429:
    """Unit tests for the pure parse_usage_and_429 helper."""

    def _make_stdout(self, **kwargs) -> str:
        base = {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "usage": {
                "input_tokens": 100,
                "output_tokens": 50,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 10,
            },
        }
        base.update(kwargs)
        return json.dumps(base)

    def test_valid_usage_extracted(self) -> None:
        """AC-1/AC-6: full usage block is parsed and actuals_available=True."""
        stdout = json.dumps(
            {
                "usage": {
                    "input_tokens": 120,
                    "output_tokens": 80,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 10,
                }
            }
        )
        r = parse_usage_and_429(stdout, "", 0, NOW)
        assert r["actuals_available"] is True
        assert r["input_tokens"] == 120
        assert r["output_tokens"] == 80
        assert r["cache_creation_input_tokens"] == 0
        assert r["cache_read_input_tokens"] == 10
        assert r["provider_rate_limited"] is False
        assert r["provider_retry_after_epoch"] is None

    def test_missing_usage_field(self) -> None:
        """AC-2: JSON but no 'usage' → actuals_available=False, no crash."""
        stdout = json.dumps({"type": "result", "subtype": "success"})
        r = parse_usage_and_429(stdout, "", 0, NOW)
        assert r["actuals_available"] is False
        assert r["input_tokens"] is None
        assert r["output_tokens"] is None

    def test_non_json_stdout(self) -> None:
        """AC-2: non-JSON stdout → graceful fallback, all fields None/False."""
        r = parse_usage_and_429("plain text response from agent", "", 0, NOW)
        assert r["actuals_available"] is False
        assert r["provider_rate_limited"] is False
        assert r["input_tokens"] is None

    def test_empty_stdout(self) -> None:
        """Empty stdout is a parse error — no crash, all defaults."""
        r = parse_usage_and_429("", "", 0, NOW)
        assert r["actuals_available"] is False
        assert r["input_tokens"] is None

    def test_malformed_json(self) -> None:
        """AC-6: malformed JSON → actuals_available=False, no crash."""
        r = parse_usage_and_429("{usage: bad json}", "", 0, NOW)
        assert r["actuals_available"] is False
        assert r["provider_rate_limited"] is False

    def test_only_input_tokens_sets_actuals_available(self) -> None:
        """actuals_available=True if at least input_tokens present."""
        stdout = json.dumps({"usage": {"input_tokens": 5}})
        r = parse_usage_and_429(stdout, "", 0, NOW)
        assert r["actuals_available"] is True
        assert r["input_tokens"] == 5
        assert r["output_tokens"] is None

    def test_only_output_tokens_sets_actuals_available(self) -> None:
        """actuals_available=True if at least output_tokens present."""
        stdout = json.dumps({"usage": {"output_tokens": 7}})
        r = parse_usage_and_429(stdout, "", 0, NOW)
        assert r["actuals_available"] is True
        assert r["output_tokens"] == 7

    # --- 429 detection ---

    def test_429_from_json_error_type_rate_limit(self) -> None:
        """AC-4/AC-6: JSON error object with type='rate_limit_error' → provider_rate_limited."""
        stdout = json.dumps({"error": {"type": "rate_limit_error", "message": "Too many requests"}})
        r = parse_usage_and_429(stdout, "", 1, NOW)
        assert r["provider_rate_limited"] is True
        assert r["provider_retry_after_epoch"] is None

    def test_429_from_json_error_message(self) -> None:
        """AC-4/AC-6: JSON error with 'rate limit' in message → provider_rate_limited."""
        stdout = json.dumps({"error": {"type": "api_error", "message": "You hit the rate limit"}})
        r = parse_usage_and_429(stdout, "", 1, NOW)
        assert r["provider_rate_limited"] is True

    def test_429_from_stderr_text(self) -> None:
        """AC-4/AC-6: non-zero exit + '429' in stderr → provider_rate_limited."""
        r = parse_usage_and_429("", "Error 429: Too Many Requests", 1, NOW)
        assert r["provider_rate_limited"] is True
        assert r["provider_retry_after_epoch"] is None

    def test_429_from_rate_limit_in_stdout_text(self) -> None:
        """Non-zero exit + 'rate limit' in stdout (non-JSON) → provider_rate_limited."""
        r = parse_usage_and_429("rate limit exceeded", "", 1, NOW)
        assert r["provider_rate_limited"] is True

    def test_429_no_rate_limit_on_zero_returncode(self) -> None:
        """Non-JSON text + exit 0 → no 429, no crash.

        Rate-limit detection from text requires a nonzero exit code.
        """
        r = parse_usage_and_429("rate limit mention in text", "", 0, NOW)
        assert r["provider_rate_limited"] is False

    def test_retry_after_epoch_float(self) -> None:
        """AC-4/AC-6: error.retry_after is a float epoch → passed through."""
        retry_epoch = 1_700_000_060.0
        stdout = json.dumps(
            {"error": {"type": "rate_limit_error", "message": "rl", "retry_after": retry_epoch}}
        )
        r = parse_usage_and_429(stdout, "", 1, NOW)
        assert r["provider_rate_limited"] is True
        assert r["provider_retry_after_epoch"] == retry_epoch

    def test_retry_after_from_reset_at_iso(self) -> None:
        """AC-4/AC-6: error.reset_at is ISO datetime string → converted to epoch."""
        stdout = json.dumps(
            {
                "error": {
                    "type": "rate_limit_error",
                    "message": "rl",
                    "reset_at": "2023-11-14T22:13:20+00:00",
                }
            }
        )
        r = parse_usage_and_429(stdout, "", 1, NOW)
        assert r["provider_rate_limited"] is True
        # 2023-11-14T22:13:20+00:00 → epoch
        from datetime import datetime

        expected = datetime(2023, 11, 14, 22, 13, 20, tzinfo=UTC).timestamp()
        assert r["provider_retry_after_epoch"] == expected

    def test_429_without_retry_after(self) -> None:
        """AC-4/AC-6: 429 with no retry_after/reset_at → retry_after_epoch=None."""
        stdout = json.dumps({"error": {"type": "rate_limit_error", "message": "rl"}})
        r = parse_usage_and_429(stdout, "", 1, NOW)
        assert r["provider_rate_limited"] is True
        assert r["provider_retry_after_epoch"] is None

    def test_usage_and_429_together(self) -> None:
        """Rare: usage present even in a 429 response → both extracted."""
        stdout = json.dumps(
            {
                "usage": {"input_tokens": 10, "output_tokens": 0},
                "error": {"type": "rate_limit_error", "message": "rl"},
            }
        )
        r = parse_usage_and_429(stdout, "", 1, NOW)
        assert r["actuals_available"] is True
        assert r["input_tokens"] == 10
        assert r["provider_rate_limited"] is True


# ---------------------------------------------------------------------------
# _ensure_output_format_json — idempotency tests (T-1m9744, AC-5)
# ---------------------------------------------------------------------------


class TestEnsureOutputFormatJson:
    """AC-5: --output-format json is appended exactly once."""

    def test_appends_when_absent(self) -> None:
        argv = ["claude", "-p", "hello"]
        result = _ensure_output_format_json(argv)
        assert result == ["claude", "-p", "hello", "--output-format", "json"]

    def test_no_duplicate_two_arg_form(self) -> None:
        """Already has --output-format json (two-arg) → unchanged."""
        argv = ["claude", "--output-format", "json", "-p", "hello"]
        result = _ensure_output_format_json(argv)
        assert result == argv

    def test_no_duplicate_eq_form(self) -> None:
        """Already has --output-format=json (eq-form) → unchanged."""
        argv = ["claude", "--output-format=stream-json", "-p", "hello"]
        result = _ensure_output_format_json(argv)
        # The flag is present (any value), so we don't add another.
        assert "--output-format" not in result[len(argv) :]

    def test_no_duplicate_eq_json_form(self) -> None:
        """Already has --output-format=json eq-form → unchanged."""
        argv = ["claude", "--output-format=json", "-p", "hello"]
        result = _ensure_output_format_json(argv)
        assert result == argv

    def test_returns_new_list(self) -> None:
        """Original list is not mutated."""
        argv = ["claude", "-p", "hi"]
        result = _ensure_output_format_json(argv)
        assert result is not argv


# ---------------------------------------------------------------------------
# FakeExecutor — token_outputs and rate_limit_tasks (T-1m9744, AC-7)
# ---------------------------------------------------------------------------


class TestFakeExecutorTokensAnd429:
    def test_token_outputs_populated_on_success(self) -> None:
        """Token fields appear in TaskResult when token_outputs is set."""
        ex = FakeExecutor(
            token_outputs={
                "t1": {
                    "input_tokens": 200,
                    "output_tokens": 80,
                    "cache_creation_input_tokens": 5,
                    "cache_read_input_tokens": 3,
                }
            }
        )
        result = ex.execute(_ctx(task_id="t1"))
        assert result.status == "succeeded"
        assert result.actuals_available is True
        assert result.input_tokens == 200
        assert result.output_tokens == 80
        assert result.cache_creation_input_tokens == 5
        assert result.cache_read_input_tokens == 3

    def test_no_token_outputs_stays_defaults(self) -> None:
        """Without token_outputs, token fields remain None/False (defaults)."""
        ex = FakeExecutor()
        result = ex.execute(_ctx(task_id="t1"))
        assert result.actuals_available is False
        assert result.input_tokens is None

    def test_rate_limit_first_call_fails_with_429(self) -> None:
        """First call for a rate-limited task returns provider_rate_limited=True."""
        retry_epoch = 1_700_000_099.0
        ex = FakeExecutor(rate_limit_tasks={"t1": retry_epoch})
        result = ex.execute(_ctx(task_id="t1"))
        assert result.status == "failed"
        assert result.provider_rate_limited is True
        assert result.provider_retry_after_epoch == retry_epoch
        assert result.error == "fake 429"

    def test_rate_limit_second_call_succeeds(self) -> None:
        """Second call for the same task (after 429) succeeds normally."""
        ex = FakeExecutor(rate_limit_tasks={"t1": None})
        # First call → 429
        r1 = ex.execute(_ctx(task_id="t1"))
        assert r1.provider_rate_limited is True
        # Second call → succeed
        r2 = ex.execute(_ctx(task_id="t1"))
        assert r2.status == "succeeded"
        assert r2.provider_rate_limited is False

    def test_rate_limit_none_retry_after(self) -> None:
        """retry_after=None is surfaced correctly."""
        ex = FakeExecutor(rate_limit_tasks={"t1": None})
        result = ex.execute(_ctx(task_id="t1"))
        assert result.provider_rate_limited is True
        assert result.provider_retry_after_epoch is None

    def test_rate_limit_only_affects_target_task(self) -> None:
        """Other tasks are not affected by rate_limit_tasks."""
        ex = FakeExecutor(rate_limit_tasks={"t2": None})
        result = ex.execute(_ctx(task_id="t1"))
        assert result.status == "succeeded"
        assert result.provider_rate_limited is False

    def test_token_outputs_for_unlisted_task_not_populated(self) -> None:
        """token_outputs for t2 doesn't affect t1."""
        ex = FakeExecutor(token_outputs={"t2": {"input_tokens": 99}})
        result = ex.execute(_ctx(task_id="t1"))
        assert result.actuals_available is False
        assert result.input_tokens is None


# ---------------------------------------------------------------------------
# Fixture file sanity check (T-1m9744 AC-6)
# ---------------------------------------------------------------------------


class TestFixture:
    def test_fixture_file_is_valid_json_with_usage(self) -> None:
        """Fixture file tests/fixtures/claude_usage.json exists and has usage fields."""
        fixture_path = Path(__file__).parent / "fixtures" / "claude_usage.json"
        assert fixture_path.exists(), f"Fixture not found: {fixture_path}"
        data = json.loads(fixture_path.read_text())
        assert "usage" in data
        usage = data["usage"]
        assert "input_tokens" in usage
        assert "output_tokens" in usage
