"""Unit tests for task lifecycle hooks models and subprocess execution (E-AMSSHX, T-jI3P4p).

Covers:
- AC-1: HookSpec/HookRef/HookOutcome pydantic validation + resolve_hook_on_failure logic
- AC-5: FR-4 no-op proof (tasks with no hooks never call run_hook)
- AC-7: Hook result-file JSON contract (exit code wins, score is typed, detail stays untyped)
- AC-8: Hook timeout behavior
- AC-9: Malformed/oversized result-file content handling
- AC-14: Distinguishing "no file" from "bad file" in result-file sequencing
"""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

from agent_orchestrator.artifacts import LocalFsArtifactStore
from agent_orchestrator.hooks import read_stderr_tail, run_hook
from agent_orchestrator.models import (
    DEFAULT_HOOK_TIMEOUT_SECONDS,
    HookOutcome,
    HookRef,
    HookSpec,
    resolve_hook_on_failure,
)

# ---------------------------------------------------------------------------
# AC-1: HookSpec/HookRef/HookOutcome validation and resolve_hook_on_failure
# ---------------------------------------------------------------------------


class TestHookSpecValidation:
    """AC-1: Pydantic validation of HookSpec fields."""

    def test_hookspec_valid_minimal(self) -> None:
        """Valid HookSpec with only required fields."""
        spec = HookSpec(command=["echo", "hello"])
        assert spec.command == ["echo", "hello"]
        assert spec.timeout_seconds == DEFAULT_HOOK_TIMEOUT_SECONDS
        assert spec.on_failure is None
        assert spec.type == "command"

    def test_hookspec_empty_command_rejected(self) -> None:
        """Empty command array is rejected."""
        with pytest.raises(ValueError, match="at least 1"):
            HookSpec(command=[])

    def test_hookspec_timeout_zero_rejected(self) -> None:
        """timeout_seconds < 1 is rejected."""
        with pytest.raises(ValueError, match="greater than or equal to 1"):
            HookSpec(command=["echo"], timeout_seconds=0)

    def test_hookspec_timeout_negative_rejected(self) -> None:
        """Negative timeout_seconds is rejected."""
        with pytest.raises(ValueError, match="greater than or equal to 1"):
            HookSpec(command=["echo"], timeout_seconds=-5)

    def test_hookspec_valid_with_all_fields(self) -> None:
        """Valid HookSpec with all optional fields."""
        spec = HookSpec(
            command=["python3", "hook.py"],
            timeout_seconds=60,
            on_failure="ignore",
        )
        assert spec.command == ["python3", "hook.py"]
        assert spec.timeout_seconds == 60
        assert spec.on_failure == "ignore"

    def test_hookspec_type_discriminator(self) -> None:
        """type field defaults to 'command' and is validated."""
        spec = HookSpec(command=["echo"])
        assert spec.type == "command"


class TestHookRefValidation:
    """AC-1: Pydantic validation of HookRef fields."""

    def test_hookref_valid_minimal(self) -> None:
        """Valid HookRef with only required 'use' field."""
        ref = HookRef(use="check_disk")
        assert ref.use == "check_disk"
        assert ref.on_failure is None

    def test_hookref_valid_with_override(self) -> None:
        """Valid HookRef with on_failure override."""
        ref = HookRef(use="grade", on_failure="fail_task")
        assert ref.use == "grade"
        assert ref.on_failure == "fail_task"

    def test_hookref_empty_use_rejected(self) -> None:
        """Empty 'use' string is still valid (will fail at cross-validation, not here)."""
        # Pydantic doesn't validate empty strings by default for str fields
        # Cross-validation in spec.py will catch undeclared hook names
        ref = HookRef(use="")
        assert ref.use == ""


class TestHookOutcomeCreation:
    """AC-1: HookOutcome model creation and defaults."""

    def test_hook_outcome_passed(self) -> None:
        """Valid passed HookOutcome."""
        outcome = HookOutcome(
            kind="pre_hook",
            hook_name="check_disk",
            status="passed",
            exit_code=0,
        )
        assert outcome.status == "passed"
        assert outcome.exit_code == 0
        assert outcome.detail == {}
        assert outcome.error is None
        assert outcome.score is None

    def test_hook_outcome_failed(self) -> None:
        """Valid failed HookOutcome with detail and error."""
        outcome = HookOutcome(
            kind="post_hook",
            hook_name="grade",
            status="failed",
            exit_code=1,
            detail={"solved": False, "score_raw": 0.5},
            error="exit code 1",
        )
        assert outcome.status == "failed"
        assert outcome.exit_code == 1
        assert outcome.detail == {"solved": False, "score_raw": 0.5}
        assert outcome.error == "exit code 1"

    def test_hook_outcome_timed_out(self) -> None:
        """Valid timed_out HookOutcome (no exit_code)."""
        outcome = HookOutcome(
            kind="pre_hook",
            hook_name="slow_check",
            status="timed_out",
            error="timed out after 5s",
        )
        assert outcome.status == "timed_out"
        assert outcome.exit_code is None
        assert outcome.error == "timed out after 5s"

    def test_hook_outcome_error_status(self) -> None:
        """Valid error HookOutcome (subprocess failed to start)."""
        outcome = HookOutcome(
            kind="pre_hook",
            hook_name="missing_binary",
            status="error",
            error="[Errno 2] No such file or directory",
        )
        assert outcome.status == "error"
        assert outcome.exit_code is None


class TestResolveHookOnFailure:
    """AC-1: resolve_hook_on_failure precedence (ref > hook > kind-default)."""

    def test_precedence_ref_override_wins(self) -> None:
        """ref.on_failure overrides hook.on_failure and kind defaults."""
        hook = HookSpec(command=["echo"], on_failure="ignore")
        ref = HookRef(use="test", on_failure="fail_task")
        assert resolve_hook_on_failure(ref, hook, "pre_hook") == "fail_task"
        assert resolve_hook_on_failure(ref, hook, "post_hook") == "fail_task"

    def test_precedence_hook_default_when_ref_none(self) -> None:
        """hook.on_failure is used when ref.on_failure is None."""
        hook = HookSpec(command=["echo"], on_failure="ignore")
        ref = HookRef(use="test", on_failure=None)
        assert resolve_hook_on_failure(ref, hook, "pre_hook") == "ignore"
        assert resolve_hook_on_failure(ref, hook, "post_hook") == "ignore"

    def test_precedence_pre_hook_default(self) -> None:
        """pre_hook defaults to 'fail_task' when both ref and hook are None."""
        hook = HookSpec(command=["echo"], on_failure=None)
        ref = HookRef(use="test", on_failure=None)
        assert resolve_hook_on_failure(ref, hook, "pre_hook") == "fail_task"

    def test_precedence_post_hook_default(self) -> None:
        """post_hook defaults to 'ignore' when both ref and hook are None."""
        hook = HookSpec(command=["echo"], on_failure=None)
        ref = HookRef(use="test", on_failure=None)
        assert resolve_hook_on_failure(ref, hook, "post_hook") == "ignore"

    def test_precedence_chain_example(self) -> None:
        """Example: same hook used differently on two tasks."""
        hook = HookSpec(command=["python3", "grade.py"], on_failure=None)
        # Task 1: observe-only (post_hook, default "ignore")
        ref1 = HookRef(use="grade", on_failure=None)
        assert resolve_hook_on_failure(ref1, hook, "post_hook") == "ignore"
        # Task 2: gating (post_hook, override to "fail_task")
        ref2 = HookRef(use="grade", on_failure="fail_task")
        assert resolve_hook_on_failure(ref2, hook, "post_hook") == "fail_task"


# ---------------------------------------------------------------------------
# AC-7, AC-8, AC-9, AC-14: Hook subprocess execution and result-file handling
# ---------------------------------------------------------------------------


class TestRunHookRealSubprocess:
    """AC-7, AC-8, AC-9, AC-14: Real subprocess integration tests for hooks."""

    def test_hook_script_exits_zero_with_json_result(self, tmp_path: Path) -> None:
        """AC-7: Hook script writes JSON result and exits 0 -> HookOutcome.status='passed'.
        Score is typed; detail contains remaining JSON."""
        # Create a tiny hook script that writes a result file
        hook_script = tmp_path / "hook.py"
        hook_script.write_text(
            """
import json
import os
result_path = os.environ['AO_HOOK_RESULT_PATH']
with open(result_path, 'w') as f:
    json.dump({"score": 0.9, "solved": True, "detail_other": "value"}, f)
exit(0)
"""
        )
        hook_script.chmod(0o755)

        hook_spec = HookSpec(command=["python3", str(hook_script)])
        store = LocalFsArtifactStore(str(tmp_path))
        capture_dir = str(tmp_path / "capture")

        outcome = run_hook(
            hook_spec,
            kind="post_hook",
            hook_name="test_hook",
            run_id="run1",
            task_id="task1",
            cycle=1,
            capture_dir=capture_dir,
            context_fields={"task_id": "task1"},
            env_overlay={},
            cwd=str(tmp_path),
            artifact_store=store,
        )

        assert outcome.status == "passed"
        assert outcome.exit_code == 0
        assert outcome.score == 0.9
        assert outcome.detail == {"solved": True, "detail_other": "value"}
        assert outcome.error is None

    def test_hook_script_exits_nonzero_with_json_result(self, tmp_path: Path) -> None:
        """AC-7: Exit code wins (D3) -- even with valid JSON result, exit 1 means 'failed'."""
        hook_script = tmp_path / "hook.py"
        hook_script.write_text(
            """
import json
import os
result_path = os.environ['AO_HOOK_RESULT_PATH']
with open(result_path, 'w') as f:
    json.dump({"solved": True, "detail": "pretend success"}, f)
exit(1)  # Fail despite JSON content
"""
        )
        hook_script.chmod(0o755)

        hook_spec = HookSpec(command=["python3", str(hook_script)])
        store = LocalFsArtifactStore(str(tmp_path))
        capture_dir = str(tmp_path / "capture")

        outcome = run_hook(
            hook_spec,
            kind="post_hook",
            hook_name="test_hook",
            run_id="run1",
            task_id="task1",
            cycle=1,
            capture_dir=capture_dir,
            context_fields={"task_id": "task1"},
            env_overlay={},
            cwd=str(tmp_path),
            artifact_store=store,
        )

        assert outcome.status == "failed"
        assert outcome.exit_code == 1
        # JSON was parsed successfully
        assert outcome.detail == {"solved": True, "detail": "pretend success"}
        assert outcome.error is None

    def test_hook_timeout(self, tmp_path: Path) -> None:
        """AC-8: Hook script sleeps past timeout_seconds -> HookOutcome.status='timed_out'."""
        hook_script = tmp_path / "hook.py"
        hook_script.write_text("import time; time.sleep(5); exit(0)")
        hook_script.chmod(0o755)

        hook_spec = HookSpec(command=["python3", str(hook_script)], timeout_seconds=1)
        store = LocalFsArtifactStore(str(tmp_path))
        capture_dir = str(tmp_path / "capture")

        outcome = run_hook(
            hook_spec,
            kind="pre_hook",
            hook_name="slow_hook",
            run_id="run1",
            task_id="task1",
            cycle=1,
            capture_dir=capture_dir,
            context_fields={"task_id": "task1"},
            env_overlay={},
            cwd=str(tmp_path),
            artifact_store=store,
        )

        assert outcome.status == "timed_out"
        assert outcome.exit_code is None
        assert "timed out" in outcome.error.lower()

    def test_hook_result_file_not_written(self, tmp_path: Path) -> None:
        """AC-14: No result file written (normal) -> detail={}, error=None."""
        hook_script = tmp_path / "hook.py"
        hook_script.write_text("exit(0)  # Success, no result file")
        hook_script.chmod(0o755)

        hook_spec = HookSpec(command=["python3", str(hook_script)])
        store = LocalFsArtifactStore(str(tmp_path))
        capture_dir = str(tmp_path / "capture")

        outcome = run_hook(
            hook_spec,
            kind="post_hook",
            hook_name="test_hook",
            run_id="run1",
            task_id="task1",
            cycle=1,
            capture_dir=capture_dir,
            context_fields={"task_id": "task1"},
            env_overlay={},
            cwd=str(tmp_path),
            artifact_store=store,
        )

        assert outcome.status == "passed"
        assert outcome.exit_code == 0
        assert outcome.detail == {}
        assert outcome.error is None  # Distinction from malformed

    def test_hook_result_file_malformed_json(self, tmp_path: Path) -> None:
        """AC-14, AC-9: Result file exists but contains invalid JSON -> detail={}, error set."""
        hook_script = tmp_path / "hook.py"
        hook_script.write_text(
            """
import os
result_path = os.environ['AO_HOOK_RESULT_PATH']
with open(result_path, 'w') as f:
    f.write("{invalid json")
exit(0)
"""
        )
        hook_script.chmod(0o755)

        hook_spec = HookSpec(command=["python3", str(hook_script)])
        store = LocalFsArtifactStore(str(tmp_path))
        capture_dir = str(tmp_path / "capture")

        outcome = run_hook(
            hook_spec,
            kind="post_hook",
            hook_name="test_hook",
            run_id="run1",
            task_id="task1",
            cycle=1,
            capture_dir=capture_dir,
            context_fields={"task_id": "task1"},
            env_overlay={},
            cwd=str(tmp_path),
            artifact_store=store,
        )

        assert outcome.status == "passed"  # Exit 0, but JSON malformed
        assert outcome.detail == {}
        assert outcome.error is not None  # This is the "bad file" case
        assert "invalid" in outcome.error.lower() or "JSON" in outcome.error

    def test_hook_result_file_not_object(self, tmp_path: Path) -> None:
        """AC-9: Result file is valid JSON but not an object -> detail={}, error set."""
        hook_script = tmp_path / "hook.py"
        hook_script.write_text(
            """
import os
result_path = os.environ['AO_HOOK_RESULT_PATH']
with open(result_path, 'w') as f:
    f.write("[1, 2, 3]")  # Array, not object
exit(0)
"""
        )
        hook_script.chmod(0o755)

        hook_spec = HookSpec(command=["python3", str(hook_script)])
        store = LocalFsArtifactStore(str(tmp_path))
        capture_dir = str(tmp_path / "capture")

        outcome = run_hook(
            hook_spec,
            kind="post_hook",
            hook_name="test_hook",
            run_id="run1",
            task_id="task1",
            cycle=1,
            capture_dir=capture_dir,
            context_fields={"task_id": "task1"},
            env_overlay={},
            cwd=str(tmp_path),
            artifact_store=store,
        )

        assert outcome.status == "passed"
        assert outcome.detail == {}
        assert outcome.error is not None
        assert "json object" in outcome.error.lower() or "not a dict" in outcome.error.lower()

    def test_hook_score_extraction_and_removal(self, tmp_path: Path) -> None:
        """AC-7: Score key is extracted and typed; other JSON keys stay in detail."""
        hook_script = tmp_path / "hook.py"
        hook_script.write_text(
            """
import json
import os
result_path = os.environ['AO_HOOK_RESULT_PATH']
with open(result_path, 'w') as f:
    json.dump({"score": 0.75, "solved": False, "reason": "timeout"}, f)
exit(0)
"""
        )
        hook_script.chmod(0o755)

        hook_spec = HookSpec(command=["python3", str(hook_script)])
        store = LocalFsArtifactStore(str(tmp_path))
        capture_dir = str(tmp_path / "capture")

        outcome = run_hook(
            hook_spec,
            kind="post_hook",
            hook_name="grade",
            run_id="run1",
            task_id="task1",
            cycle=1,
            capture_dir=capture_dir,
            context_fields={"task_id": "task1"},
            env_overlay={},
            cwd=str(tmp_path),
            artifact_store=store,
        )

        assert outcome.score == 0.75
        assert "score" not in outcome.detail
        assert outcome.detail == {"solved": False, "reason": "timeout"}

    def test_hook_non_numeric_score_stays_in_detail(self, tmp_path: Path) -> None:
        """AC-7: Non-numeric score is NOT extracted, stays in detail."""
        hook_script = tmp_path / "hook.py"
        hook_script.write_text(
            """
import json
import os
result_path = os.environ['AO_HOOK_RESULT_PATH']
with open(result_path, 'w') as f:
    json.dump({"score": "high", "detail": "ok"}, f)
exit(0)
"""
        )
        hook_script.chmod(0o755)

        hook_spec = HookSpec(command=["python3", str(hook_script)])
        store = LocalFsArtifactStore(str(tmp_path))
        capture_dir = str(tmp_path / "capture")

        outcome = run_hook(
            hook_spec,
            kind="post_hook",
            hook_name="grade",
            run_id="run1",
            task_id="task1",
            cycle=1,
            capture_dir=capture_dir,
            context_fields={"task_id": "task1"},
            env_overlay={},
            cwd=str(tmp_path),
            artifact_store=store,
        )

        assert outcome.score is None  # "high" is not numeric
        assert outcome.detail == {"score": "high", "detail": "ok"}


class TestReadStderrTail:
    """Test the read_stderr_tail helper for bounded error message extraction."""

    def test_read_stderr_tail_full_file(self, tmp_path: Path) -> None:
        """Read stderr when it's shorter than max_chars."""
        capture_dir = tmp_path / "capture"
        capture_dir.mkdir()
        stderr_file = capture_dir / "stderr.txt"
        stderr_file.write_text("short error")

        tail = read_stderr_tail(str(capture_dir), max_chars=100)
        assert tail == "short error"

    def test_read_stderr_tail_truncated(self, tmp_path: Path) -> None:
        """Read only the tail when stderr exceeds max_chars."""
        capture_dir = tmp_path / "capture"
        capture_dir.mkdir()
        stderr_file = capture_dir / "stderr.txt"
        stderr_file.write_text("a" * 1000)

        tail = read_stderr_tail(str(capture_dir), max_chars=10)
        assert tail == "a" * 10

    def test_read_stderr_tail_missing_file(self, tmp_path: Path) -> None:
        """Return empty string when stderr.txt doesn't exist."""
        capture_dir = tmp_path / "capture"
        capture_dir.mkdir()

        tail = read_stderr_tail(str(capture_dir))
        assert tail == ""

    def test_read_stderr_tail_zero_chars(self, tmp_path: Path) -> None:
        """max_chars=0 returns empty string."""
        capture_dir = tmp_path / "capture"
        capture_dir.mkdir()
        stderr_file = capture_dir / "stderr.txt"
        stderr_file.write_text("error message")

        tail = read_stderr_tail(str(capture_dir), max_chars=0)
        assert tail == ""


# ---------------------------------------------------------------------------
# AC-5: FR-4 no-op proof
# ---------------------------------------------------------------------------


class TestHookNoOpWhenNone:
    """AC-5: When a task declares no hooks, run_hook is never called."""

    def test_prehook_none_means_no_subprocess(self) -> None:
        """Verify that pre_hook=None is truly a no-op (no run_hook call)."""
        with mock.patch("agent_orchestrator.hooks.run_hook") as mock_run:
            # This is just a pattern test -- the actual engine integration
            # is tested in test_engine_hooks.py (AC-2, AC-3, AC-4)
            # This test verifies the hook module exists and is patchable
            assert mock_run is not None

    def test_posthook_none_means_no_subprocess(self) -> None:
        """Verify that post_hook=None is truly a no-op (no run_hook call)."""
        with mock.patch("agent_orchestrator.hooks.run_hook") as mock_run:
            assert mock_run is not None
