"""CliRunner E2E tests for `ao-bench validate` (AC 1-3 of T-Sc4Hm2).

Invokes the Typer app directly (outer boundary, per CLAUDE.md's e2e testing rule) --
no `[project.scripts]` entry exists yet (that's T-Cli8Nf), so tests import the `app`
object and drive it with `typer.testing.CliRunner`, exactly like `tests/test_cli.py`
does for the core `ao` app.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from agent_orchestrator.bench.cli import app

from .conftest import SubjectFactory, SuiteFactory

runner = CliRunner()


# ---------------------------------------------------------------------------
# Happy path (AC1)
# ---------------------------------------------------------------------------


def test_validate_suite_ok(suite_factory: SuiteFactory) -> None:
    suite_path = suite_factory()
    result = runner.invoke(app, ["validate", "--suite", str(suite_path)])
    assert result.exit_code == 0, result.output
    assert "OK" in result.output


def test_validate_subject_ok_fake(subject_factory: SubjectFactory) -> None:
    subject_path = subject_factory(type_="fake")
    result = runner.invoke(app, ["validate", "--subject", str(subject_path)])
    assert result.exit_code == 0, result.output
    assert "OK" in result.output


def test_validate_suite_and_subject_ok(
    suite_factory: SuiteFactory, subject_factory: SubjectFactory
) -> None:
    suite_path = suite_factory()
    subject_path = subject_factory(type_="fake")
    result = runner.invoke(
        app, ["validate", "--suite", str(suite_path), "--subject", str(subject_path)]
    )
    assert result.exit_code == 0, result.output
    assert "OK" in result.output


def test_validate_no_args_errors(tmp_path: Path) -> None:
    result = runner.invoke(app, ["validate"])
    assert result.exit_code != 0
    assert "--suite" in result.output or "--subject" in result.output


# ---------------------------------------------------------------------------
# Reject paths (AC2, AC3) -- exit non-zero with a structured, offender-naming error
# ---------------------------------------------------------------------------


def test_validate_suite_duplicate_task_id_fails(suite_factory: SuiteFactory) -> None:
    tasks = [
        {
            "id": "bugfix-a",
            "category": "bugfix",
            "instruction": "tasks/bugfix-a/instruction.md",
            "fixture": "tasks/bugfix-a/fixture",
            "grader": {"type": "pytest"},
        },
        {
            "id": "bugfix-a",
            "category": "feature",
            "instruction": "tasks/bugfix-a-2/instruction.md",
            "fixture": "tasks/bugfix-a-2/fixture",
            "grader": {"type": "pytest"},
        },
    ]
    suite_path = suite_factory(tasks=tasks)
    result = runner.invoke(app, ["validate", "--suite", str(suite_path)])
    assert result.exit_code != 0
    assert "bugfix-a" in result.output


def test_validate_suite_unknown_grader_type_fails(suite_factory: SuiteFactory) -> None:
    task = {
        "id": "bugfix-a",
        "category": "bugfix",
        "instruction": "tasks/bugfix-a/instruction.md",
        "fixture": "tasks/bugfix-a/fixture",
        "grader": {"type": "llm_judge"},
    }
    suite_path = suite_factory(tasks=[task])
    result = runner.invoke(app, ["validate", "--suite", str(suite_path)])
    assert result.exit_code != 0
    assert "llm_judge" in result.output


def test_validate_suite_missing_fixture_fails(suite_factory: SuiteFactory) -> None:
    suite_path = suite_factory(materialize=False)
    (suite_path.parent / "tasks" / "bugfix-off-by-one").mkdir(parents=True)
    (suite_path.parent / "tasks" / "bugfix-off-by-one" / "instruction.md").write_text("# fix\n")
    result = runner.invoke(app, ["validate", "--suite", str(suite_path)])
    assert result.exit_code != 0
    assert "fixture" in result.output


def test_validate_suite_missing_version_fails(suite_factory: SuiteFactory) -> None:
    suite_path = suite_factory(omit_version=True)
    result = runner.invoke(app, ["validate", "--suite", str(suite_path)])
    assert result.exit_code != 0


def test_validate_suite_uppercase_id_fails(suite_factory: SuiteFactory) -> None:
    suite_path = suite_factory(suite_id="Dev-Core")
    result = runner.invoke(app, ["validate", "--suite", str(suite_path)])
    assert result.exit_code != 0


def test_validate_subject_unknown_type_lists_known_types(subject_factory: SubjectFactory) -> None:
    subject_path = subject_factory(type_="http_api")
    result = runner.invoke(app, ["validate", "--subject", str(subject_path)])
    assert result.exit_code != 0
    assert "http_api" in result.output
    # AC3: message must list the known subject types.
    for known in ("claude_cli", "ao_workflow", "fake"):
        assert known in result.output


# ---------------------------------------------------------------------------
# claude --version probe (ASSUMPTION A1) -- mocked, never a real subprocess call
# ---------------------------------------------------------------------------


def test_validate_claude_cli_subject_probes_claude_version(
    subject_factory: SubjectFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, returncode=0, stdout="2.1.0\n", stderr="")

    monkeypatch.setattr("agent_orchestrator.bench.cli.subprocess.run", fake_run)
    subject_path = subject_factory(type_="claude_cli", extra={"model": "claude-haiku-4-5"})
    result = runner.invoke(app, ["validate", "--subject", str(subject_path)])
    assert result.exit_code == 0, result.output
    assert calls == [["claude", "--version"]]
    assert "OK" in result.output


def test_validate_claude_cli_subject_probe_missing_binary_warns_not_fails(
    subject_factory: SubjectFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        raise FileNotFoundError("claude not on PATH")

    monkeypatch.setattr("agent_orchestrator.bench.cli.subprocess.run", fake_run)
    subject_path = subject_factory(type_="claude_cli")
    result = runner.invoke(app, ["validate", "--subject", str(subject_path)])
    # A missing `claude` binary is a WARNING, not a validate failure (spec correctness
    # is orthogonal to whether the local environment has `claude` on PATH).
    assert result.exit_code == 0, result.output
    assert "WARNING" in result.output
    assert "OK" in result.output


def test_validate_claude_cli_subject_probe_nonzero_exit_warns(
    subject_factory: SubjectFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When claude --version exits non-zero, it should warn but still pass validation (line 88)."""

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(argv, returncode=1, stdout="", stderr="error")

    monkeypatch.setattr("agent_orchestrator.bench.cli.subprocess.run", fake_run)
    subject_path = subject_factory(type_="claude_cli")
    result = runner.invoke(app, ["validate", "--subject", str(subject_path)])
    # A non-zero exit code from `claude --version` is a WARNING, not a validate failure
    assert result.exit_code == 0, result.output
    assert "WARNING" in result.output
    assert "OK" in result.output


def test_validate_fake_subject_does_not_probe_claude(
    subject_factory: SubjectFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        raise AssertionError("claude --version should not be probed for a fake subject")

    monkeypatch.setattr("agent_orchestrator.bench.cli.subprocess.run", fake_run)
    subject_path = subject_factory(type_="fake")
    result = runner.invoke(app, ["validate", "--subject", str(subject_path)])
    assert result.exit_code == 0, result.output
