"""Edge case and error path tests for bench module (coverage for error paths + corner cases).

Complements the existing deterministic tests with additional coverage for:
- CLI error cases (_discover_latest_run_dirs edge cases, probe failures)
- Grader corner cases (malformed commands, OSError when reading files)
- Spec validation edge cases (task not found, model instantiation errors)
- Runner probe error cases (claude/git probes failing)
- Results edge cases (empty candidates, derive_subject_id edge cases)
- Subject error cases (OSError in spawning commands, missing solution dirs)
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest import mock

import pytest

from agent_orchestrator.bench.cli import _discover_latest_run_dirs
from agent_orchestrator.bench.errors import BenchError, GraderError, SpecValidationError
from agent_orchestrator.bench.graders import (
    CommandGrader,
    FileAssertionGrader,
    GradeResult,
    PytestGrader,
    _run_command,
)
from agent_orchestrator.bench.results import _best, _derive_subject_id
from agent_orchestrator.bench.runner import (
    _git_sha,
    _load_existing_record,
    _probe_claude_version,
)
from agent_orchestrator.bench.spec import Assertion, BenchSuite, BenchTask, GraderConfig
from agent_orchestrator.bench.subjects import (
    ClaudeCliSubject,
    SubjectError,
    _apply_scripted_effect,
)

# ---------------------------------------------------------------------------
# CLI Edge Cases
# ---------------------------------------------------------------------------


def test_discover_latest_run_dirs_empty_results_root(tmp_path: Path) -> None:
    """When results_root does not exist, return empty list (line 218)."""
    missing_root = tmp_path / "missing"
    assert _discover_latest_run_dirs(missing_root, "suite-id") == []


def test_discover_latest_run_dirs_filters_non_directories(tmp_path: Path) -> None:
    """Skip non-directory entries in results_root (line 223)."""
    results_root = tmp_path
    # Create a file, not a directory
    (results_root / "2026-07-22-suite-id-subj-id.txt").write_text("not a dir")
    # Create a valid directory
    valid_dir = results_root / "2026-07-22-suite-id-subj-id"
    valid_dir.mkdir()

    found = _discover_latest_run_dirs(results_root, "suite-id")
    assert len(found) == 1
    assert found[0].name == "2026-07-22-suite-id-subj-id"


def test_discover_latest_run_dirs_filters_non_matching_names(tmp_path: Path) -> None:
    """Skip entries that don't match the date-suite-subject pattern (line 226)."""
    results_root = tmp_path
    # Create directories with non-matching names
    (results_root / "invalid-name").mkdir()
    (results_root / "no-date-here").mkdir()
    # Create a valid directory
    valid_dir = results_root / "2026-07-22-suite-id-subj-id"
    valid_dir.mkdir()

    found = _discover_latest_run_dirs(results_root, "suite-id")
    assert len(found) == 1
    assert found[0].name == "2026-07-22-suite-id-subj-id"


def test_discover_latest_run_dirs_filters_compare_suffix(tmp_path: Path) -> None:
    """Skip directories with COMPARE_DIR_SUFFIX (line 229)."""
    results_root = tmp_path
    # Create a valid result directory
    valid_dir = results_root / "2026-07-22-suite-id-subj-a"
    valid_dir.mkdir()
    # Create a compare directory (should be filtered)
    compare_dir = results_root / "2026-07-22-suite-id-compare"
    compare_dir.mkdir()

    found = _discover_latest_run_dirs(results_root, "suite-id")
    assert len(found) == 1
    assert found[0].name == "2026-07-22-suite-id-subj-a"


def test_discover_latest_run_dirs_latest_per_subject_wins(tmp_path: Path) -> None:
    """When multiple dates exist for same subject, latest (lexicographically) wins."""
    results_root = tmp_path
    # Create older and newer runs for the same subject
    (results_root / "2026-07-20-suite-id-subj-a").mkdir()
    newer = results_root / "2026-07-22-suite-id-subj-a"
    newer.mkdir()

    found = _discover_latest_run_dirs(results_root, "suite-id")
    assert len(found) == 1
    assert found[0].name == "2026-07-22-suite-id-subj-a"


# ---------------------------------------------------------------------------
# Grader Edge Cases
# ---------------------------------------------------------------------------


def test_run_command_malformed_shlex_split(tmp_path: Path) -> None:
    """When shlex.split raises ValueError, returns _CommandRun with missing=True.

    Tests line 108-109.
    """
    cmd = 'echo "unclosed quote'  # unbalanced quotes
    result = _run_command(cmd, tmp_path, timeout_seconds=5)
    assert result.missing is True
    assert result.returncode == 127  # _COMMAND_NOT_FOUND_RETURNCODE


def test_run_command_empty_command(tmp_path: Path) -> None:
    """When command is empty (after shlex.split), returns _CommandRun with missing=True.

    Tests line 115.
    """
    cmd = ""
    result = _run_command(cmd, tmp_path, timeout_seconds=5)
    assert result.missing is True
    assert result.returncode == 127


def test_pytest_grader_missing_command_detail(tmp_path: Path) -> None:
    """When pytest command times out or is missing, detail includes appropriate flags.

    Tests line 199, 201, 205.
    """
    cfg = GraderConfig(type="pytest", command="missing-pytest-binary")
    ctx_dir = tmp_path / "repo"
    ctx_dir.mkdir()

    class Ctx:
        repo_dir: str = str(ctx_dir)

    grader = PytestGrader()
    result = grader._grade(cfg, Ctx())
    # The grader will try to run 'missing-pytest-binary' which doesn't exist
    assert result.solved is False
    assert "missing_command" in result.detail or result.score == 0.0


def test_file_assertion_grader_empty_golden(tmp_path: Path) -> None:
    """When assertion.golden is None/empty, _resolve_golden_path returns None (line 260)."""
    from agent_orchestrator.bench.graders import _resolve_golden_path

    assertion = Assertion(type="equals_file", path="target", golden=None)
    assert _resolve_golden_path(assertion) is None

    assertion_empty = Assertion(type="equals_file", path="target", golden="")
    assert _resolve_golden_path(assertion_empty) is None


def test_file_assertion_grader_oserror_on_read_target(tmp_path: Path) -> None:
    """When target file read raises OSError, assertion fails gracefully (line 275-276)."""
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    target_file = repo_dir / "target.txt"
    target_file.write_text("content")

    cfg = GraderConfig(
        type="file_assertion",
        assertions=[Assertion(type="contains", path="target.txt", substring="x")],
    )

    repo_dir_str = str(repo_dir)

    class Ctx:
        repo_dir: str = repo_dir_str

    grader = FileAssertionGrader()
    grader._grade(cfg, Ctx())
    # Deleting the file after writing to trigger OSError on read
    target_file.unlink()
    # Re-run after deletion (simulate race condition)
    result2 = grader._grade(cfg, Ctx())
    assert result2.solved is False


def test_file_assertion_grader_oserror_on_read_golden(tmp_path: Path) -> None:
    """When golden file read raises OSError, equals_file check fails gracefully (line 285-286)."""
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    golden_file = tmp_path / "golden.txt"
    golden_file.write_text("golden content")
    target_file = repo_dir / "target.txt"
    target_file.write_text("golden content")

    cfg = GraderConfig(
        type="file_assertion",
        assertions=[Assertion(type="equals_file", path="target.txt", golden=str(golden_file))],
    )

    repo_dir_str = str(repo_dir)

    class Ctx:
        repo_dir: str = repo_dir_str

    grader = FileAssertionGrader()
    grader._grade(cfg, Ctx())
    # Delete the golden file after the first run
    golden_file.unlink()
    result2 = grader._grade(cfg, Ctx())
    assert result2.solved is False


def test_command_grader_missing_command_detail(tmp_path: Path) -> None:
    """When command grader command is missing, detail includes missing_command=True."""
    cfg = GraderConfig(type="command", command="missing-binary")
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()

    repo_dir_str = str(repo_dir)

    class Ctx:
        repo_dir: str = repo_dir_str

    grader = CommandGrader()
    result = grader._grade(cfg, Ctx())
    assert result.solved is False
    assert result.detail.get("missing_command") is True


# ---------------------------------------------------------------------------
# Spec Edge Cases
# ---------------------------------------------------------------------------


def test_bench_suite_task_not_found(tmp_path: Path) -> None:
    """When BenchSuite.task() is called with non-existent task_id, raises KeyError (line 103)."""
    task_data = BenchTask(
        id="task-1",
        category="bugfix",
        instruction=str(tmp_path / "task-1.md"),
        fixture=str(tmp_path / "task-1-fixture"),
        grader=GraderConfig(type="fake"),
    )
    suite = BenchSuite(
        version="1.0",
        id="test-suite",
        domain="software",
        tasks=[task_data],
    )
    with pytest.raises(KeyError):
        suite.task("nonexistent-task")


def test_spec_validation_error_on_suite_model_instantiation(tmp_path: Path) -> None:
    """When BenchSuite model instantiation fails, raises SpecValidationError.

    Tests line 196-197.
    """
    from agent_orchestrator.bench.spec import load_suite

    # Create an invalid suite file (missing required 'tasks' field)
    suite_file = tmp_path / "bad-suite.json"
    suite_file.write_text(json.dumps({"version": "1.0", "id": "test"}))

    with pytest.raises(SpecValidationError):
        load_suite(str(suite_file))


def test_spec_validation_error_on_subject_model_instantiation(tmp_path: Path) -> None:
    """When SubjectSpec model instantiation fails, raises SpecValidationError.

    Tests line 279-280.
    """
    from agent_orchestrator.bench.spec import load_subject

    # Create an invalid subject file (missing required 'type' field)
    subject_file = tmp_path / "bad-subject.json"
    subject_file.write_text(json.dumps({"version": "1.0", "id": "test"}))

    with pytest.raises(SpecValidationError):
        load_subject(str(subject_file))


# ---------------------------------------------------------------------------
# Runner Edge Cases
# ---------------------------------------------------------------------------


def test_claude_version_probe_claude_not_found() -> None:
    """When shutil.which('claude') returns None, _probe_claude_version returns None (line 181)."""
    with mock.patch("shutil.which", return_value=None):
        result = _probe_claude_version()
        assert result is None


def test_claude_version_probe_oserror() -> None:
    """When probing claude raises OSError, returns None (line 190-191)."""
    patch_target = "agent_orchestrator.bench.runner.subprocess.run"
    with mock.patch(patch_target, side_effect=OSError("test error")):
        result = _probe_claude_version()
        assert result is None


def test_claude_version_probe_timeout() -> None:
    """When probing claude times out, returns None (line 190-191)."""
    patch_target = "agent_orchestrator.bench.runner.subprocess.run"
    with mock.patch(patch_target, side_effect=subprocess.TimeoutExpired("claude", 5)):
        result = _probe_claude_version()
        assert result is None


def test_claude_version_probe_nonzero_exit() -> None:
    """When claude --version exits non-zero, returns None (line 192)."""
    mock_result = mock.Mock()
    mock_result.returncode = 1
    mock_result.stdout = "some error"
    patch_target = "agent_orchestrator.bench.runner.subprocess.run"
    with mock.patch(patch_target, return_value=mock_result):
        result = _probe_claude_version()
        assert result is None


def test_git_sha_probe_oserror() -> None:
    """When probing git raises OSError, returns None (line 208-209)."""
    patch_target = "agent_orchestrator.bench.runner.subprocess.run"
    with mock.patch(patch_target, side_effect=OSError("test error")):
        result = _git_sha()
        assert result is None


def test_git_sha_probe_timeout() -> None:
    """When probing git times out, returns None (line 208-209)."""
    patch_target = "agent_orchestrator.bench.runner.subprocess.run"
    with mock.patch(patch_target, side_effect=subprocess.TimeoutExpired("git", 5)):
        result = _git_sha()
        assert result is None


def test_load_existing_record_pydantic_validation_error(tmp_path: Path) -> None:
    """When BenchRunRecord validation fails, raises BenchError (line 287-288)."""
    record_file = tmp_path / "run.json"
    # Write invalid JSON that doesn't match BenchRunRecord schema
    record_file.write_text(json.dumps({"invalid": "schema"}))

    with pytest.raises(BenchError):
        _load_existing_record(record_file)


# ---------------------------------------------------------------------------
# Results Edge Cases
# ---------------------------------------------------------------------------


def test_best_with_empty_candidates() -> None:
    """When _best() is called with empty candidates list, returns None (line 412)."""
    result = _best([], minimize=True)
    assert result is None

    result = _best([], minimize=False)
    assert result is None


def test_derive_subject_id_no_match_returns_rest(tmp_path: Path) -> None:
    """When dir name doesn't match the date pattern, return the original name (line 275)."""
    # This tests the edge case where the directory name is malformed
    result = _derive_subject_id(Path("2026-07-22-suite-id-subj-a"), known_suite_id="suite-id")
    assert result == "subj-a"

    result = _derive_subject_id(Path("invalid-name"), known_suite_id="suite-id")
    assert result == "invalid-name"


# ---------------------------------------------------------------------------
# Subject Edge Cases
# ---------------------------------------------------------------------------


def test_fake_subject_scripted_effect_solution_dir_missing(tmp_path: Path) -> None:
    """When solution dir doesn't exist in copy-solution effect, returns gracefully (line 538)."""
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()

    # Call with copy-solution but no .solution_marker directory
    _apply_scripted_effect("copy-solution", repo_dir)
    # Should not raise, just return


def test_fake_subject_scripted_effect_noop() -> None:
    """When effect is None or 'noop', do nothing (line 533-534)."""
    repo_dir = Path("/tmp/test")
    # Should not raise even with non-existent path
    _apply_scripted_effect(None, repo_dir)
    _apply_scripted_effect("noop", repo_dir)


def test_fake_subject_scripted_effect_unknown_raises() -> None:
    """When effect is unknown, raises SubjectError (line 546)."""
    repo_dir = Path("/tmp/test")
    with pytest.raises(SubjectError):
        _apply_scripted_effect("unknown-effect", repo_dir)


def test_claude_cli_subject_claude_not_found(tmp_path: Path) -> None:
    """When 'claude' binary is not found, returns SubjectResult with error status."""
    from agent_orchestrator.bench.spec import SubjectSpec
    from agent_orchestrator.bench.subjects import RunContext

    spec = SubjectSpec(
        version="1.0",
        id="claude-cli-test",
        type="claude_cli",
        model="claude-3-5-haiku",
    )
    subject = ClaudeCliSubject(spec)

    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    ctx = RunContext(
        repo_dir=str(repo_dir),
        instruction_path=str(tmp_path / "instruction.md"),
        workspace=str(tmp_path / "workspace"),
        capture_dir=str(tmp_path / "capture"),
        timeout_seconds=30,
        budget_total=None,
        max_turns=None,
    )

    with mock.patch("shutil.which", return_value=None):
        result = subject.run(
            BenchTask(
                id="test-task",
                category="bugfix",
                instruction="instruction.md",
                fixture="fixture",
                grader=GraderConfig(type="fake"),
            ),
            ctx,
        )
        assert result.status == "error"
        assert result.raw_error and "`claude` CLI not found" in result.raw_error


# ---------------------------------------------------------------------------
# Grader Registry and Boundary Tests
# ---------------------------------------------------------------------------


def test_grader_boundary_wraps_unexpected_error_in_grader_error() -> None:
    """When _grade raises non-GraderError exception, grade() wraps it (line 176-178)."""

    class BuggyGrader(PytestGrader):
        def _grade(self, cfg: GraderConfig, ctx) -> GradeResult:
            raise RuntimeError("unexpected bug")

    cfg = GraderConfig(type="pytest")

    class Ctx:
        repo_dir: str = "/tmp"

    grader = BuggyGrader()
    with pytest.raises(GraderError):
        grader.grade(cfg, Ctx())
