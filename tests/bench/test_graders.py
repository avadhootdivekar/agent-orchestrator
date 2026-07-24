"""Unit tests for bench/graders.py (T-Grd7Vx AC 1-4, 6 + edge paths).

Pytest/Command graders spawn REAL, tiny, network-free subprocesses on `tmp_path`
fixtures (the tests themselves are still fully deterministic) per this task's brief;
FileAssertion/Fake graders are pure (no subprocess). Uses `sys.executable -m pytest`
rather than the class default `uv run pytest` so the mini pytest projects below don't
depend on `uv` project-root discovery from an arbitrary tmp_path.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from agent_orchestrator.bench.errors import GraderError
from agent_orchestrator.bench.graders import (
    CommandGrader,
    FakeGrader,
    FileAssertionGrader,
    GradeResult,
    PytestGrader,
    _parse_pytest_summary,
)
from agent_orchestrator.bench.registries import GRADER_REGISTRY
from agent_orchestrator.bench.spec import Assertion, GraderConfig

_PYTEST_MODULE_COMMAND = f"{sys.executable} -m pytest -q --tb=no ."


@dataclass
class _Ctx:
    """Minimal stand-in for `RunContext` -- satisfies `GraderContext` structurally."""

    repo_dir: str


def _ctx(path: Path) -> _Ctx:
    return _Ctx(repo_dir=str(path))


def _write_mixed_pytest_project(base: Path) -> None:
    """3 passing + 1 failing test (matches AC1's "1 of 4 failing -> score=0.75")."""
    base.joinpath("test_mixed.py").write_text(
        "def test_one():\n    assert True\n"
        "def test_two():\n    assert True\n"
        "def test_three():\n    assert True\n"
        "def test_four():\n    assert False\n"
    )


def _write_all_pass_pytest_project(base: Path) -> None:
    base.joinpath("test_all_pass.py").write_text(
        "def test_a():\n    assert True\ndef test_b():\n    assert True\n"
    )


def _write_no_tests_pytest_project(base: Path) -> None:
    # No `test_*` function -- pytest collects zero items ("no tests ran").
    base.joinpath("helpers.py").write_text("def not_a_test():\n    return 1\n")


# ---------------------------------------------------------------------------
# PytestGrader (AC1, AC2)
# ---------------------------------------------------------------------------


def test_pytest_grader_all_pass(tmp_path: Path) -> None:
    _write_all_pass_pytest_project(tmp_path)
    cfg = GraderConfig(type="pytest", command=_PYTEST_MODULE_COMMAND, timeout_seconds=30)
    result = PytestGrader().grade(cfg, _ctx(tmp_path))
    assert result.solved is True
    assert result.score == 1.0
    assert result.detail["passed"] == 2
    assert result.detail["failed"] == 0
    assert result.detail["returncode"] == 0


def test_pytest_grader_partial_fail(tmp_path: Path) -> None:
    _write_mixed_pytest_project(tmp_path)
    cfg = GraderConfig(type="pytest", command=_PYTEST_MODULE_COMMAND, timeout_seconds=30)
    result = PytestGrader().grade(cfg, _ctx(tmp_path))
    assert result.solved is False
    assert result.score == pytest.approx(0.75)
    assert result.detail == {"returncode": 1, "passed": 3, "failed": 1, "total": 4}


def test_pytest_grader_zero_tests_collected(tmp_path: Path) -> None:
    _write_no_tests_pytest_project(tmp_path)
    cfg = GraderConfig(type="pytest", command=_PYTEST_MODULE_COMMAND, timeout_seconds=30)
    result = PytestGrader().grade(cfg, _ctx(tmp_path))
    assert result.solved is False
    assert result.detail["total"] == 0


def test_pytest_grader_pass_threshold_below_score_solves(tmp_path: Path) -> None:
    _write_mixed_pytest_project(tmp_path)  # score = 0.75
    cfg = GraderConfig(
        type="pytest", command=_PYTEST_MODULE_COMMAND, timeout_seconds=30, pass_threshold=0.5
    )
    result = PytestGrader().grade(cfg, _ctx(tmp_path))
    assert result.score == pytest.approx(0.75)
    assert result.solved is True


def test_pytest_grader_pass_threshold_above_score_fails(tmp_path: Path) -> None:
    _write_mixed_pytest_project(tmp_path)  # score = 0.75
    cfg = GraderConfig(
        type="pytest", command=_PYTEST_MODULE_COMMAND, timeout_seconds=30, pass_threshold=0.9
    )
    result = PytestGrader().grade(cfg, _ctx(tmp_path))
    assert result.score == pytest.approx(0.75)
    assert result.solved is False


def test_pytest_grader_malformed_summary_exit_code_still_rules(tmp_path: Path) -> None:
    """rc==0 with unparseable/absent summary output ("true" prints nothing) must NOT
    be reported solved -- `total==0` keeps `solved=False` even though `score` (the
    enrichment-only signal) still reflects the successful exit code (ASSUMPTION A3).
    """
    cfg = GraderConfig(type="pytest", command="true", timeout_seconds=5)
    result = PytestGrader().grade(cfg, _ctx(tmp_path))
    assert result.detail["total"] == 0
    assert result.solved is False
    assert result.score == 1.0


def test_parse_pytest_summary_direct() -> None:
    assert _parse_pytest_summary("1 failed, 3 passed in 0.05s") == (3, 1, 4)
    assert _parse_pytest_summary("4 passed in 0.02s") == (4, 0, 4)
    assert _parse_pytest_summary("no tests ran in 0.00s") == (0, 0, 0)
    assert _parse_pytest_summary("garbled nonsense output") == (0, 0, 0)


# ---------------------------------------------------------------------------
# CommandGrader (AC3 + timeout/missing edge paths)
# ---------------------------------------------------------------------------


def test_command_grader_true_solves(tmp_path: Path) -> None:
    cfg = GraderConfig(type="command", command="true", timeout_seconds=5)
    result = CommandGrader().grade(cfg, _ctx(tmp_path))
    assert result.solved is True
    assert result.score == 1.0


def test_command_grader_false_does_not_solve(tmp_path: Path) -> None:
    cfg = GraderConfig(type="command", command="false", timeout_seconds=5)
    result = CommandGrader().grade(cfg, _ctx(tmp_path))
    assert result.solved is False
    assert result.score == 0.0


def test_command_grader_timeout(tmp_path: Path) -> None:
    cfg = GraderConfig(type="command", command="sleep 2", timeout_seconds=1)
    result = CommandGrader().grade(cfg, _ctx(tmp_path))
    assert result.solved is False
    assert result.score == 0.0
    assert result.detail["timed_out"] is True


def test_command_grader_missing_binary(tmp_path: Path) -> None:
    cfg = GraderConfig(
        type="command", command="definitely-not-a-real-binary-xyz", timeout_seconds=5
    )
    result = CommandGrader().grade(cfg, _ctx(tmp_path))
    assert result.solved is False
    assert result.score == 0.0
    assert result.detail["missing_command"] is True


def test_command_grader_no_command_configured(tmp_path: Path) -> None:
    cfg = GraderConfig(type="command", command=None, timeout_seconds=5)
    result = CommandGrader().grade(cfg, _ctx(tmp_path))
    assert result.solved is False
    assert result.score == 0.0


# ---------------------------------------------------------------------------
# FileAssertionGrader (AC4 + missing golden edge path)
# ---------------------------------------------------------------------------


def test_file_assertion_grader_mixed_exists_and_contains(tmp_path: Path) -> None:
    (tmp_path / "present.txt").write_text("hello world\n")
    cfg = GraderConfig(
        type="file_assertion",
        assertions=[
            Assertion(type="exists", path="present.txt"),
            Assertion(type="contains", path="present.txt", substring="hello"),
            Assertion(type="exists", path="missing.txt"),
        ],
    )
    result = FileAssertionGrader().grade(cfg, _ctx(tmp_path))
    assert result.solved is False
    assert result.score == pytest.approx(2 / 3)
    assert [c["passed"] for c in result.detail["per_assertion"]] == [True, True, False]


def test_file_assertion_grader_all_pass_solves(tmp_path: Path) -> None:
    (tmp_path / "present.txt").write_text("hello world\n")
    cfg = GraderConfig(
        type="file_assertion",
        assertions=[
            Assertion(type="exists", path="present.txt"),
            Assertion(type="contains", path="present.txt", substring="hello"),
        ],
    )
    result = FileAssertionGrader().grade(cfg, _ctx(tmp_path))
    assert result.solved is True
    assert result.score == 1.0


def test_file_assertion_grader_equals_file_matches_absolute_golden(tmp_path: Path) -> None:
    golden = tmp_path / "golden.txt"
    golden.write_text("expected content\n")
    target = tmp_path / "repo" / "output.txt"
    target.parent.mkdir()
    target.write_text("expected content\n")
    cfg = GraderConfig(
        type="file_assertion",
        assertions=[Assertion(type="equals_file", path="output.txt", golden=str(golden))],
    )
    result = FileAssertionGrader().grade(cfg, _ctx(tmp_path / "repo"))
    assert result.solved is True


def test_file_assertion_grader_equals_file_missing_golden_no_exception(tmp_path: Path) -> None:
    target = tmp_path / "repo" / "output.txt"
    target.parent.mkdir()
    target.write_text("some content\n")
    missing_golden = tmp_path / "does-not-exist.txt"
    cfg = GraderConfig(
        type="file_assertion",
        assertions=[Assertion(type="equals_file", path="output.txt", golden=str(missing_golden))],
    )
    result = FileAssertionGrader().grade(cfg, _ctx(tmp_path / "repo"))
    assert result.solved is False
    assert result.detail["per_assertion"][0]["passed"] is False


def test_file_assertion_grader_no_assertions_configured(tmp_path: Path) -> None:
    cfg = GraderConfig(type="file_assertion", assertions=[])
    result = FileAssertionGrader().grade(cfg, _ctx(tmp_path))
    assert result.solved is False
    assert result.score == 0.0


# ---------------------------------------------------------------------------
# FakeGrader (test-only, deterministic)
# ---------------------------------------------------------------------------


def test_fake_grader_default_solves() -> None:
    cfg = GraderConfig(type="fake")
    result = FakeGrader().grade(cfg, _ctx(Path("/unused")))
    assert result.solved is True
    assert result.score == 1.0


def test_fake_grader_command_false_does_not_solve() -> None:
    cfg = GraderConfig(type="fake", command="false")
    result = FakeGrader().grade(cfg, _ctx(Path("/unused")))
    assert result.solved is False
    assert result.score == 0.0


def test_fake_grader_scripted_score_via_pass_threshold() -> None:
    cfg = GraderConfig(type="fake", pass_threshold=0.42)
    result = FakeGrader().grade(cfg, _ctx(Path("/unused")))
    assert result.solved is True
    assert result.score == pytest.approx(0.42)


# ---------------------------------------------------------------------------
# GRADER_REGISTRY wiring (AC6)
# ---------------------------------------------------------------------------


def test_grader_registry_has_all_mvp_types() -> None:
    assert {"pytest", "command", "file_assertion", "fake", "swebench"} <= set(GRADER_REGISTRY)
    assert GRADER_REGISTRY["pytest"] is PytestGrader
    assert GRADER_REGISTRY["command"] is CommandGrader
    assert GRADER_REGISTRY["file_assertion"] is FileAssertionGrader
    assert GRADER_REGISTRY["fake"] is FakeGrader


def test_unknown_grader_type_via_registry_lookup_raises_keyerror() -> None:
    with pytest.raises(KeyError):
        _ = GRADER_REGISTRY["totally-unknown-grader-type"]


# ---------------------------------------------------------------------------
# Grader.grade boundary wraps unexpected errors as GraderError
# ---------------------------------------------------------------------------


def test_grade_boundary_wraps_unexpected_error_as_grader_error(tmp_path: Path) -> None:
    class _BoomGrader(PytestGrader):
        def _grade(self, cfg: GraderConfig, ctx: object) -> GradeResult:
            raise RuntimeError("boom")

    with pytest.raises(GraderError, match="boom"):
        _BoomGrader().grade(GraderConfig(type="pytest"), _ctx(tmp_path))
