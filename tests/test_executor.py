"""Tests for executor implementations and context-hygiene contract."""

from __future__ import annotations

import json
import os
from datetime import UTC
from pathlib import Path

from agent_orchestrator.executors.base import Executor
from agent_orchestrator.executors.claude_cli import (
    RECOMMENDED_HEADLESS_DISALLOWED_TOOLS,
    ClaudeCliExecutor,
    _apply_tool_policy,
    _ensure_disallowed_tools,
    _ensure_stream_capture_flags,
    extract_result_event,
    parse_transcript_events,
    parse_usage_and_429,
    render_transcript,
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

    def test_total_cost_usd_extracted(self) -> None:
        """E-9h3m7k FR-1: top-level total_cost_usd is extracted as cost_usd."""
        stdout = json.dumps(
            {
                "type": "result",
                "total_cost_usd": 0.4567,
                "usage": {"input_tokens": 100, "output_tokens": 50},
            }
        )
        r = parse_usage_and_429(stdout, "", 0, NOW)
        assert r["cost_usd"] == 0.4567

    def test_missing_total_cost_usd_is_none(self) -> None:
        """No top-level total_cost_usd → cost_usd stays None, no crash."""
        stdout = json.dumps({"type": "result", "usage": {"input_tokens": 5}})
        r = parse_usage_and_429(stdout, "", 0, NOW)
        assert r["cost_usd"] is None

    def test_fixture_total_cost_usd_matches_frozen_value(self) -> None:
        """Regression pin against the frozen real-CLI shape fixture."""
        fixture_path = Path(__file__).parent / "fixtures" / "claude_usage.json"
        stdout = fixture_path.read_text(encoding="utf-8")
        r = parse_usage_and_429(stdout, "", 0, NOW)
        assert r["cost_usd"] == 0.001

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
# _ensure_stream_capture_flags — full multi-turn capture flag injection
# ---------------------------------------------------------------------------


class TestEnsureStreamCaptureFlags:
    """stream-json + --verbose are injected so every turn is captured."""

    def test_appends_stream_json_and_verbose_when_absent(self) -> None:
        argv = ["claude", "-p", "hello"]
        result = _ensure_stream_capture_flags(argv)
        assert result[:3] == ["claude", "-p", "hello"]
        idx = result.index("--output-format")
        assert result[idx + 1] == "stream-json"
        assert "--verbose" in result

    def test_verbose_added_only_once_if_already_present(self) -> None:
        argv = ["claude", "-p", "hi", "--verbose"]
        result = _ensure_stream_capture_flags(argv)
        assert result.count("--verbose") == 1
        assert "stream-json" in result

    def test_honours_caller_output_format_two_arg(self) -> None:
        """Caller already set --output-format json → we don't override it."""
        argv = ["claude", "--output-format", "json", "-p", "hello"]
        result = _ensure_stream_capture_flags(argv)
        assert result.count("--output-format") == 1
        idx = result.index("--output-format")
        assert result[idx + 1] == "json"
        # json (not stream-json) → --verbose is NOT force-added.
        assert "--verbose" not in result

    def test_honours_caller_stream_json_eq_form_adds_verbose(self) -> None:
        argv = ["claude", "--output-format=stream-json", "-p", "hello"]
        result = _ensure_stream_capture_flags(argv)
        assert result.count("--output-format=stream-json") == 1
        assert "--verbose" in result

    def test_returns_new_list(self) -> None:
        """Original list is not mutated."""
        argv = ["claude", "-p", "hi"]
        result = _ensure_stream_capture_flags(argv)
        assert result is not argv
        assert argv == ["claude", "-p", "hi"]


# ---------------------------------------------------------------------------
# _ensure_disallowed_tools — opt-in per-agent tool disabling (allow-all default)
# ---------------------------------------------------------------------------


class TestEnsureDisallowedTools:
    """Default posture is allow-all; disabling is opt-in and override-safe."""

    def test_empty_tools_is_noop_allow_all_default(self) -> None:
        argv = ["claude", "-p", "hi"]
        result = _ensure_disallowed_tools(argv, ())
        assert result == ["claude", "-p", "hi"]
        assert "--disallowedTools" not in result

    def test_injects_opt_in_tools(self) -> None:
        argv = ["claude", "-p", "hi"]
        result = _ensure_disallowed_tools(argv, ("BashOutput", "WebSearch"))
        idx = result.index("--disallowedTools")
        assert result[idx + 1 : idx + 3] == ["BashOutput", "WebSearch"]

    def test_recommended_set_covers_both_kill_tool_names(self) -> None:
        # KillBash was renamed KillShell in Claude Code v2; both must be present
        # so the rule holds across CLI versions (v2.1.209 ships both literals).
        assert "KillShell" in RECOMMENDED_HEADLESS_DISALLOWED_TOOLS
        assert "KillBash" in RECOMMENDED_HEADLESS_DISALLOWED_TOOLS
        assert "BashOutput" in RECOMMENDED_HEADLESS_DISALLOWED_TOOLS

    def test_skips_when_caller_set_disallowed_tools(self) -> None:
        """An explicit --disallowedTools in argv wins; we don't double-inject."""
        argv = ["claude", "-p", "hi", "--disallowedTools", "Bash"]
        result = _ensure_disallowed_tools(argv, ("WebSearch",))
        assert result.count("--disallowedTools") == 1
        assert "WebSearch" not in result

    def test_skips_when_caller_set_allowed_tools(self) -> None:
        argv = ["claude", "-p", "hi", "--allowedTools", "Read"]
        result = _ensure_disallowed_tools(argv, ("WebSearch",))
        assert "--disallowedTools" not in result

    def test_skips_when_caller_set_tools_flag(self) -> None:
        argv = ["claude", "-p", "hi", "--tools", "default"]
        result = _ensure_disallowed_tools(argv, ("WebSearch",))
        assert "--disallowedTools" not in result

    def test_recognizes_hyphenated_and_eq_forms(self) -> None:
        assert (
            _ensure_disallowed_tools(
                ["claude", "--disallowed-tools", "Bash"], ("WebSearch",)
            ).count("--disallowedTools")
            == 0
        )
        assert "--disallowedTools" not in _ensure_disallowed_tools(
            ["claude", "--allowed-tools=Read"], ("WebSearch",)
        )

    def test_returns_new_list(self) -> None:
        argv = ["claude", "-p", "hi"]
        result = _ensure_disallowed_tools(argv, ("WebSearch",))
        assert result is not argv
        assert argv == ["claude", "-p", "hi"]

    def test_variadic_list_terminated_by_stream_flags(self) -> None:
        """execute() order: disallowed-tools BEFORE stream flags, so --output-format
        terminates the variadic list rather than being swallowed as a tool name."""
        argv = _ensure_disallowed_tools(["claude", "-p", "hi"], ("BashOutput", "Task"))
        argv = _ensure_stream_capture_flags(argv)
        di = argv.index("--disallowedTools")
        of = argv.index("--output-format")
        assert di < of
        assert argv[di + 1 : of] == ["BashOutput", "Task"]


class TestClaudeCliExecutorDisallowedToolsWiring:
    """execute() forwards AgentSpec.disallowed_tools into the real spawned argv."""

    def _capture_argv(self, monkeypatch, tmp_path, disallowed: tuple[str, ...]) -> list[str]:
        captured: dict[str, list[str]] = {}

        class _FakePopen:
            def __init__(self, argv: list[str], **kwargs: object) -> None:
                captured["argv"] = argv
                self.returncode = 0

            def wait(self, timeout: float | None = None) -> int:
                return 0

            def kill(self) -> None:
                pass

        monkeypatch.setattr("agent_orchestrator.executors.claude_cli.subprocess.Popen", _FakePopen)
        agent = AgentSpec(executor="claude_cli", disallowed_tools=list(disallowed))
        ctx = TaskContext(
            run_id="r1",
            task_id="t1",
            agent=agent,
            instruction_path="/i.md",
            input_paths=[],
            output_paths=[],
            repo_paths={},
            timeout_seconds=60,
            output_dir=str(tmp_path),
        )
        result = ClaudeCliExecutor().execute(ctx)
        assert result.status == "succeeded"
        return captured["argv"]

    def test_field_injected_before_stream_flags(self, monkeypatch, tmp_path) -> None:
        argv = self._capture_argv(monkeypatch, tmp_path, ("BashOutput", "KillShell", "KillBash"))
        di = argv.index("--disallowedTools")
        of = argv.index("--output-format")
        assert argv[di + 1 : of] == ["BashOutput", "KillShell", "KillBash"]

    def test_empty_field_is_allow_all(self, monkeypatch, tmp_path) -> None:
        argv = self._capture_argv(monkeypatch, tmp_path, ())
        assert "--disallowedTools" not in argv


# ---------------------------------------------------------------------------
# _apply_tool_policy — the engine's NON-overridable forced denial set (E-Wk9Tz3
# as-built security review C-1). `disallowed_tools` keeps "explicit flag wins";
# `forced_disallowed_tools` is appended regardless.
# ---------------------------------------------------------------------------


class TestApplyToolPolicy:
    def test_forced_survives_every_tool_policy_spelling(self) -> None:
        for flag_argv in (
            ["--allowedTools", "Read,Bash"],
            ["--allowed-tools", "Read,Bash"],
            ["--disallowedTools", "Glob"],
            ["--disallowed-tools", "Glob"],
            ["--tools", "default"],
            ["--allowed-tools=Read,Bash"],
        ):
            argv = _apply_tool_policy(["claude", "-p", *flag_argv], ("WebSearch",), ("Bash",))
            assert argv[-2:] == ["--disallowedTools", "Bash"], flag_argv
            # The agent's own opt-in list is still skipped — that rule is unchanged.
            assert "WebSearch" not in argv, flag_argv

    def test_merges_into_one_flag_when_the_agent_declares_no_policy(self) -> None:
        argv = _apply_tool_policy(["claude", "-p"], ("WebSearch",), ("Bash", "WebSearch"))
        assert argv.count("--disallowedTools") == 1
        assert argv[argv.index("--disallowedTools") + 1 :] == ["WebSearch", "Bash"]

    def test_no_forced_and_no_opt_in_is_a_noop(self) -> None:
        assert _apply_tool_policy(["claude", "-p"], (), ()) == ["claude", "-p"]

    def test_returns_a_new_list(self) -> None:
        argv = ["claude", "-p"]
        assert _apply_tool_policy(argv, (), ("Bash",)) is not argv
        assert argv == ["claude", "-p"]

    def test_variadic_forced_list_is_terminated_by_the_stream_flags(self) -> None:
        argv = _apply_tool_policy(["claude", "-p", "--allowedTools", "Read"], (), ("Bash", "Task"))
        argv = _ensure_stream_capture_flags(argv)
        di = argv.index("--disallowedTools")
        of = argv.index("--output-format")
        assert di < of
        assert argv[di + 1 : of] == ["Bash", "Task"]


class TestClaudeCliExecutorForcedDisallowedToolsWiring:
    """execute() forwards AgentSpec.forced_disallowed_tools into the real spawned argv
    even when the agent's own extra_args carry a competing tool policy."""

    def _capture_argv(self, monkeypatch, tmp_path, agent: AgentSpec) -> list[str]:
        captured: dict[str, list[str]] = {}

        class _FakePopen:
            def __init__(self, argv: list[str], **kwargs: object) -> None:
                captured["argv"] = argv
                self.returncode = 0

            def wait(self, timeout: float | None = None) -> int:
                return 0

            def kill(self) -> None:
                pass

        monkeypatch.setattr("agent_orchestrator.executors.claude_cli.subprocess.Popen", _FakePopen)
        ctx = TaskContext(
            run_id="r1",
            task_id="t1",
            agent=agent,
            instruction_path="/i.md",
            input_paths=[],
            output_paths=[],
            repo_paths={},
            timeout_seconds=60,
            output_dir=str(tmp_path),
        )
        assert ClaudeCliExecutor().execute(ctx).status == "succeeded"
        return captured["argv"]

    def test_forced_set_reaches_argv_despite_an_agent_allowlist(self, monkeypatch, tmp_path):
        agent = AgentSpec(
            executor="claude_cli",
            extra_args=["--allowedTools", "Read,Edit,Bash"],
            forced_disallowed_tools=["Bash", "Task"],
        )
        argv = self._capture_argv(monkeypatch, tmp_path, agent)
        di = argv.index("--disallowedTools")
        of = argv.index("--output-format")
        assert argv[di + 1 : of] == ["Bash", "Task"]
        assert di > argv.index("--allowedTools")

    def test_default_agent_has_no_forced_set(self, monkeypatch, tmp_path) -> None:
        argv = self._capture_argv(monkeypatch, tmp_path, AgentSpec(executor="claude_cli"))
        assert "--disallowedTools" not in argv


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
# ClaudeCliExecutor — model/effort argv injection tests
# ---------------------------------------------------------------------------


def _claude_agent(**kwargs) -> AgentSpec:
    return AgentSpec(executor="claude_cli", **kwargs)


def _make_ctx(agent: AgentSpec, tmp_path) -> TaskContext:
    return TaskContext(
        run_id="run1",
        task_id="t1",
        agent=agent,
        instruction_path="/path/instr.md",
        input_paths=[],
        output_paths=[],
        repo_paths={},
        timeout_seconds=60,
        output_dir=str(tmp_path / "out"),
    )


def _popen_factory(
    *,
    stdout_text: str = "{}\n",
    stderr_text: str = "",
    returncode: int = 0,
    timeout: bool = False,
):
    """Build a drop-in replacement for ``subprocess.Popen``.

    Returns ``(factory, captured)``. The factory writes the given text to the
    stdout/stderr file handles the executor passes in (mirroring the OS-level
    redirect a real child does), records argv/cwd in ``captured``, and returns a
    MagicMock process. When ``timeout`` is True the first ``wait()`` raises
    ``TimeoutExpired`` (the executor then kills + re-waits).
    """
    import subprocess as _sp
    from unittest.mock import MagicMock

    captured: dict = {}

    def factory(argv, cwd=None, stdin=None, stdout=None, stderr=None, **kwargs):
        captured["argv"] = argv
        captured["cwd"] = cwd
        if stdout is not None and stdout_text:
            stdout.write(stdout_text)
        if stderr is not None and stderr_text:
            stderr.write(stderr_text)
        proc = MagicMock()
        proc.returncode = -9 if timeout else returncode
        if timeout:
            proc.wait.side_effect = [_sp.TimeoutExpired(cmd=argv, timeout=1), None]
        else:
            proc.wait.return_value = returncode
        return proc

    return factory, captured


class TestClaudeCliArgBuilding:
    """Verify --model and --max-turns are injected correctly without running subprocess."""

    def _execute_capturing_argv(self, ctx, **popen_kwargs):
        from unittest.mock import patch

        from agent_orchestrator.executors.claude_cli import ClaudeCliExecutor

        factory, captured = _popen_factory(**popen_kwargs)
        with patch("subprocess.Popen", new=factory):
            ClaudeCliExecutor().execute(ctx)
        return captured

    def test_stream_json_and_verbose_always_injected(self, tmp_path) -> None:
        agent = _claude_agent()
        ctx = _make_ctx(agent, tmp_path)
        argv = self._execute_capturing_argv(ctx)["argv"]
        idx = argv.index("--output-format")
        assert argv[idx + 1] == "stream-json"
        assert "--verbose" in argv

    def test_model_injected_when_set(self, tmp_path) -> None:
        agent = _claude_agent(model="claude-haiku-4-5-20251001")
        ctx = _make_ctx(agent, tmp_path)
        argv = self._execute_capturing_argv(ctx)["argv"]
        assert "--model" in argv
        idx = argv.index("--model")
        assert argv[idx + 1] == "claude-haiku-4-5-20251001"

    def test_model_not_injected_when_absent(self, tmp_path) -> None:
        agent = _claude_agent()
        ctx = _make_ctx(agent, tmp_path)
        argv = self._execute_capturing_argv(ctx)["argv"]
        assert "--model" not in argv

    def test_effort_medium_injects_max_turns_from_constant(self, tmp_path) -> None:
        from agent_orchestrator.models import EFFORT_MAX_TURNS

        agent = _claude_agent(effort="medium")
        ctx = _make_ctx(agent, tmp_path)
        argv = self._execute_capturing_argv(ctx)["argv"]
        assert "--max-turns" in argv
        idx = argv.index("--max-turns")
        assert argv[idx + 1] == str(EFFORT_MAX_TURNS["medium"])

    def test_effort_xhigh_injects_max_turns_from_constant(self, tmp_path) -> None:
        from agent_orchestrator.models import EFFORT_MAX_TURNS

        agent = _claude_agent(effort="xhigh")
        ctx = _make_ctx(agent, tmp_path)
        argv = self._execute_capturing_argv(ctx)["argv"]
        assert "--max-turns" in argv
        idx = argv.index("--max-turns")
        assert argv[idx + 1] == str(EFFORT_MAX_TURNS["xhigh"])

    def test_resolved_effective_agent_reaches_argv(self, tmp_path) -> None:
        """task > agent precedence (ADR-0003 decision 2), exercised through the SAME merge
        the engine performs (`resolve_effective_agent`) and then the real argv-building
        code path -- proves the executor needs no per-task-aware logic of its own."""
        from agent_orchestrator.models import TaskSpec, resolve_effective_agent

        agent = _claude_agent(model="agent-model", effort="low")
        task = TaskSpec(id="t1", agent="ag", instruction="i.md", model="task-model", effort="xhigh")
        effective = resolve_effective_agent(task, agent)
        ctx = _make_ctx(effective, tmp_path)
        argv = self._execute_capturing_argv(ctx)["argv"]
        assert argv[argv.index("--model") + 1] == "task-model"
        assert argv[argv.index("--max-turns") + 1] == "120"

    def test_explicit_max_turns_overrides_effort(self, tmp_path) -> None:
        # Explicit max_turns wins over the effort-derived value.
        agent = _claude_agent(effort="medium", max_turns=42)
        ctx = _make_ctx(agent, tmp_path)
        argv = self._execute_capturing_argv(ctx)["argv"]
        idx = argv.index("--max-turns")
        assert argv[idx + 1] == "42"
        assert argv.count("--max-turns") == 1

    def test_max_turns_not_injected_when_no_effort_or_override(self, tmp_path) -> None:
        agent = _claude_agent()  # no effort, no max_turns
        ctx = _make_ctx(agent, tmp_path)
        argv = self._execute_capturing_argv(ctx)["argv"]
        assert "--max-turns" not in argv

    def test_cwd_passed_to_subprocess(self, tmp_path) -> None:
        agent = _claude_agent()
        ctx = _make_ctx(agent, tmp_path)
        ctx.cwd = str(tmp_path)
        captured = self._execute_capturing_argv(ctx)
        assert captured["cwd"] == str(tmp_path)

    def test_cwd_none_when_unset(self, tmp_path) -> None:
        agent = _claude_agent()
        ctx = _make_ctx(agent, tmp_path)  # cwd defaults to ""
        captured = self._execute_capturing_argv(ctx)
        assert captured["cwd"] is None

    def test_model_not_duplicated_if_in_command_template(self, tmp_path) -> None:
        # command_template already specifies --model
        agent = _claude_agent(
            command_template=["claude", "-p", "{prompt}", "--model", "somemodel"],
            model="claude-haiku-4-5-20251001",
        )
        ctx = _make_ctx(agent, tmp_path)
        argv = self._execute_capturing_argv(ctx)["argv"]
        # --model should appear exactly once (from command_template, not injected again)
        assert argv.count("--model") == 1
        idx = argv.index("--model")
        assert argv[idx + 1] == "somemodel"


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


# ---------------------------------------------------------------------------
# parse_usage_and_429 — quota exhaustion detection tests
# ---------------------------------------------------------------------------


class TestQuotaExhaustionDetection:
    """Tests for _CLAUDE_QUOTA_PATTERN detection inside parse_usage_and_429."""

    def test_daily_limit_in_stdout(self) -> None:
        """Canonical daily-limit message in stdout → claude_quota_exhausted."""
        r = parse_usage_and_429("You've hit your daily limit", "", 1, NOW)
        assert r["claude_quota_exhausted"] is True
        assert r["provider_rate_limited"] is False

    def test_hourly_limit_in_stdout(self) -> None:
        r = parse_usage_and_429("You've hit your hourly limit", "", 1, NOW)
        assert r["claude_quota_exhausted"] is True

    def test_weekly_limit_in_stdout(self) -> None:
        r = parse_usage_and_429("You've hit your weekly limit", "", 1, NOW)
        assert r["claude_quota_exhausted"] is True

    def test_quota_in_stderr(self) -> None:
        """Message in stderr is also detected."""
        r = parse_usage_and_429("", "You've hit your daily limit", 1, NOW)
        assert r["claude_quota_exhausted"] is True

    def test_quota_case_insensitive(self) -> None:
        """Pattern is case-insensitive."""
        r = parse_usage_and_429("YOU'VE HIT YOUR DAILY LIMIT", "", 1, NOW)
        assert r["claude_quota_exhausted"] is True

    def test_quota_contraction_variants(self) -> None:
        """Matches both 'you've' and 'you hit' (without contraction)."""
        r = parse_usage_and_429("you hit your monthly limit", "", 1, NOW)
        assert r["claude_quota_exhausted"] is True

    def test_quota_not_triggered_by_rate_limit(self) -> None:
        """Provider 429 text does NOT trigger quota exhaustion."""
        r = parse_usage_and_429("", "Error 429: Too Many Requests", 1, NOW)
        assert r["claude_quota_exhausted"] is False
        assert r["provider_rate_limited"] is True

    def test_quota_early_return_skips_json_and_429(self) -> None:
        """When quota detected: actuals_available=False, provider_rate_limited=False."""
        # stdout has both a quota message AND would-be-valid JSON usage
        stdout = "You've hit your daily limit\n" + json.dumps(
            {"usage": {"input_tokens": 100, "output_tokens": 50}}
        )
        r = parse_usage_and_429(stdout, "", 1, NOW)
        assert r["claude_quota_exhausted"] is True
        assert r["actuals_available"] is False
        assert r["provider_rate_limited"] is False

    def test_normal_output_no_quota_flag(self) -> None:
        """Normal success output does not set quota flag."""
        stdout = json.dumps({"usage": {"input_tokens": 100, "output_tokens": 50}})
        r = parse_usage_and_429(stdout, "", 0, NOW)
        assert r["claude_quota_exhausted"] is False


# ---------------------------------------------------------------------------
# FakeExecutor — quota exhaustion simulation tests
# ---------------------------------------------------------------------------


class TestFakeExecutorQuota:
    """Tests for FakeExecutor.quota_exhausted_tasks parameter."""

    def test_quota_exhausted_once_then_succeeds(self) -> None:
        ex = FakeExecutor(quota_exhausted_tasks={"t1": 1})
        r1 = ex.execute(_ctx(task_id="t1"))
        assert r1.claude_quota_exhausted is True
        assert r1.status == "failed"
        r2 = ex.execute(_ctx(task_id="t1"))
        assert r2.claude_quota_exhausted is False
        assert r2.status == "succeeded"

    def test_quota_exhausted_multiple_times(self) -> None:
        ex = FakeExecutor(quota_exhausted_tasks={"t1": 3})
        for _ in range(3):
            r = ex.execute(_ctx(task_id="t1"))
            assert r.claude_quota_exhausted is True
        r = ex.execute(_ctx(task_id="t1"))
        assert r.claude_quota_exhausted is False
        assert r.status == "succeeded"

    def test_quota_does_not_affect_other_tasks(self) -> None:
        ex = FakeExecutor(quota_exhausted_tasks={"t1": 1})
        r = ex.execute(_ctx(task_id="t2"))
        assert r.claude_quota_exhausted is False
        assert r.status == "succeeded"


# ---------------------------------------------------------------------------
# Full multi-turn capture — a realistic stream-json transcript
# ---------------------------------------------------------------------------

# One event per line, exactly as `claude --output-format stream-json` emits.
_STREAM_LINES = [
    json.dumps({"type": "system", "subtype": "init", "model": "claude-x", "session_id": "s1"}),
    json.dumps(
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "Reading input"}]}}
    ),
    json.dumps(
        {
            "type": "assistant",
            "message": {
                "content": [{"type": "tool_use", "name": "Read", "input": {"file_path": "/x"}}]
            },
        }
    ),
    json.dumps(
        {
            "type": "user",
            "message": {"content": [{"type": "tool_result", "content": "line1\nline2 of file"}]},
        }
    ),
    json.dumps(
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "All done here"}]}}
    ),
    json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "Task complete",
            "usage": {
                "input_tokens": 120,
                "output_tokens": 40,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 5,
            },
        }
    ),
]
_STREAM_TEXT = "\n".join(_STREAM_LINES) + "\n"


class TestParseTranscriptEvents:
    def test_multiline_jsonl_parsed_in_order(self) -> None:
        events = parse_transcript_events(_STREAM_TEXT)
        assert len(events) == 6
        assert events[0]["type"] == "system"
        assert events[-1]["type"] == "result"

    def test_single_json_object_returned_as_one_event(self) -> None:
        """Backward-compat: a single JSON blob (legacy json format) → [obj]."""
        events = parse_transcript_events(json.dumps({"type": "result", "usage": {}}))
        assert len(events) == 1
        assert events[0]["type"] == "result"

    def test_blank_and_malformed_lines_skipped(self) -> None:
        text = _STREAM_LINES[0] + "\n\nnot json at all\n" + _STREAM_LINES[-1] + "\n"
        events = parse_transcript_events(text)
        # system + result parsed; blank + junk skipped.
        assert [e["type"] for e in events] == ["system", "result"]

    def test_empty_text_returns_empty(self) -> None:
        assert parse_transcript_events("") == []
        assert parse_transcript_events("   \n  ") == []


class TestExtractResultEvent:
    def test_prefers_result_event_even_if_not_last(self) -> None:
        text = _STREAM_TEXT + json.dumps({"type": "assistant", "message": {}}) + "\n"
        ev = extract_result_event(text)
        assert ev is not None and ev["type"] == "result"
        assert ev["usage"]["input_tokens"] == 120

    def test_falls_back_to_last_dict_when_no_result(self) -> None:
        text = _STREAM_LINES[0] + "\n" + _STREAM_LINES[1] + "\n"
        ev = extract_result_event(text)
        assert ev is not None and ev["type"] == "assistant"

    def test_empty_returns_none(self) -> None:
        assert extract_result_event("") is None


class TestRenderTranscript:
    def test_render_includes_all_turns(self) -> None:
        rendered = render_transcript(parse_transcript_events(_STREAM_TEXT))
        assert "Reading input" in rendered  # first assistant turn
        assert "[tool_use] Read" in rendered  # mid-turn tool call
        assert "line2 of file" in rendered  # tool result body
        assert "All done here" in rendered  # later assistant turn
        assert "[result] Task complete" in rendered

    def test_thinking_block_rendered(self) -> None:
        events = [
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "thinking", "thinking": "let me reason", "signature": "sig"}
                    ]
                },
            }
        ]
        assert "[thinking] let me reason" in render_transcript(events)

    def test_tool_error_flagged(self) -> None:
        events = [
            {
                "type": "user",
                "message": {
                    "content": [{"type": "tool_result", "content": "boom", "is_error": True}]
                },
            }
        ]
        assert "[tool_error] boom" in render_transcript(events)

    def test_long_content_truncated_with_marker(self) -> None:
        big = "x" * 5000
        events = [{"type": "assistant", "message": {"content": [{"type": "text", "text": big}]}}]
        rendered = render_transcript(events)
        assert "transcript.jsonl]" in rendered
        assert len(rendered) < 5000

    def test_never_raises_on_weird_shapes(self) -> None:
        events = [
            {"type": "assistant", "message": {"content": "not-a-list"}},
            {"type": "unknown"},
            {"garbage": True},
        ]
        # Should not raise.
        render_transcript(events)

    def test_empty_events_returns_empty_string(self) -> None:
        assert render_transcript([]) == ""


class TestParseUsageJsonlStream:
    """parse_usage_and_429 over a JSONL stream (not a single blob)."""

    def test_usage_extracted_from_result_event_in_stream(self) -> None:
        r = parse_usage_and_429(_STREAM_TEXT, "", 0, NOW)
        assert r["actuals_available"] is True
        assert r["input_tokens"] == 120
        assert r["output_tokens"] == 40
        assert r["cache_read_input_tokens"] == 5

    def test_quota_message_mid_stream_detected(self) -> None:
        text = _STREAM_LINES[0] + "\nYou've hit your weekly limit\n"
        r = parse_usage_and_429(text, "", 1, NOW)
        assert r["claude_quota_exhausted"] is True

    def test_429_from_result_event_error(self) -> None:
        text = (
            _STREAM_LINES[0]
            + "\n"
            + json.dumps({"type": "result", "error": {"type": "rate_limit_error", "message": "rl"}})
            + "\n"
        )
        r = parse_usage_and_429(text, "", 1, NOW)
        assert r["provider_rate_limited"] is True


class TestClaudeCliCapture:
    """End-to-end capture-file behaviour with a patched Popen (real files)."""

    def _run(self, tmp_path, **popen_kwargs):
        from unittest.mock import patch

        from agent_orchestrator.executors.claude_cli import ClaudeCliExecutor

        agent = _claude_agent()
        ctx = _make_ctx(agent, tmp_path)
        factory, _ = _popen_factory(**popen_kwargs)
        with patch("subprocess.Popen", new=factory):
            result = ClaudeCliExecutor().execute(ctx)
        return result, Path(ctx.output_dir)

    def test_success_writes_all_capture_files(self, tmp_path) -> None:
        result, out = self._run(tmp_path, stdout_text=_STREAM_TEXT)
        assert result.status == "succeeded"
        # transcript.jsonl holds the FULL raw stream (all turns), byte-for-byte.
        assert (out / "transcript.jsonl").read_text() == _STREAM_TEXT
        # stdout.txt is the human-readable render across turns.
        rendered = (out / "stdout.txt").read_text()
        assert "Reading input" in rendered and "[tool_use] Read" in rendered
        # result.json is the terminal result event.
        result_json = json.loads((out / "result.json").read_text())
        assert result_json["type"] == "result"
        assert result_json["usage"]["input_tokens"] == 120
        assert (out / "stderr.txt").exists()

    def test_success_populates_token_usage(self, tmp_path) -> None:
        result, _ = self._run(tmp_path, stdout_text=_STREAM_TEXT)
        assert result.actuals_available is True
        assert result.input_tokens == 120
        assert result.output_tokens == 40

    def test_success_populates_cost_usd(self, tmp_path) -> None:
        """E-9h3m7k FR-1: TaskResult.cost_usd is populated end-to-end from execute()."""
        stream_with_cost = (
            "\n".join(
                [
                    *_STREAM_LINES[:-1],
                    json.dumps(
                        {
                            "type": "result",
                            "subtype": "success",
                            "is_error": False,
                            "result": "Task complete",
                            "total_cost_usd": 0.1234,
                            "usage": {"input_tokens": 120, "output_tokens": 40},
                        }
                    ),
                ]
            )
            + "\n"
        )
        result, _ = self._run(tmp_path, stdout_text=stream_with_cost)
        assert result.cost_usd == 0.1234

    def test_failure_returns_failed_with_stderr_tail(self, tmp_path) -> None:
        result, out = self._run(
            tmp_path, stdout_text=_STREAM_LINES[0] + "\n", stderr_text="fatal boom", returncode=2
        )
        assert result.status == "failed"
        assert result.exit_code == 2
        assert "fatal boom" in (result.error or "")
        # Even on failure the partial transcript is captured.
        assert (out / "transcript.jsonl").read_text().strip() != ""

    def test_timeout_preserves_partial_transcript(self, tmp_path) -> None:
        # The child writes 3 turns before being killed on timeout.
        partial = "\n".join(_STREAM_LINES[:3]) + "\n"
        result, out = self._run(tmp_path, stdout_text=partial, timeout=True)
        assert result.status == "timed_out"
        assert result.error == "timeout"
        # Partial output is NOT lost (the key improvement over the old json path).
        captured = (out / "transcript.jsonl").read_text()
        assert "Reading input" in captured
        assert len(parse_transcript_events(captured)) == 3

    def test_quota_message_sets_flag(self, tmp_path) -> None:
        result, _ = self._run(tmp_path, stdout_text="You've hit your daily limit\n", returncode=1)
        assert result.claude_quota_exhausted is True
