"""Deterministic (no LLM, no network) tests for the committed MVP dev-core suite and
its subject configs (T-Fx6Dp0 acceptance criteria 1/2/5, epic FR-8).

Every test here either loads committed spec files or runs a small, bounded local
subprocess (`sys.executable -m pytest` / a fixture's own committed grader script) --
never `claude`, never a real `uv run` subprocess -- so this file runs unconditionally
in the default (`not real_llm`) test tier, same as the rest of `tests/bench/`.

Covers:
  - the committed suite + all six committed subject files load/validate cleanly
    (`load_suite`/`load_subject`);
  - every task's own instruction/fixture (and, for the refactor/test tasks, grader
    script) paths exist on disk;
  - each bugfix/feature/refactor task's fixture tests/grader FAIL on the unmodified
    fixture and PASS once its `.bench-solution/` reference overlay is applied (SWE-bench
    shape, AC2);
  - the test-writing task's grader rejects a no-op test (the mutation/negative check,
    AC2's own callout) and accepts the real reference test;
  - a full `FakeSubject` run over the whole suite completes and writes a valid
    `run.json` + `summary.md` to a scratch (never committed) out-dir (AC2 "no LLM
    needed" / prerequisite for AC3's real-LLM smoke).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agent_orchestrator.bench.results import load_run, write_summary_md
from agent_orchestrator.bench.runner import run_suite
from agent_orchestrator.bench.spec import BenchSuite, load_subject, load_suite

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SUITE_PATH = _REPO_ROOT / "benchmarks" / "suites" / "dev-core" / "suite.json"
_SUBJECTS_DIR = _REPO_ROOT / "benchmarks" / "subjects"

_FIXED_NOW = datetime(2026, 7, 22, 12, 0, 0, tzinfo=UTC)


def _fixed_clock() -> datetime:
    return _FIXED_NOW


def _run_pytest(repo_dir: Path) -> int:
    """Bare, no-target pytest invocation -- mirrors the suite's own default `pytest`
    grader command (`uv run pytest -q --tb=no`), swapping `uv run` for the current
    interpreter so this stays network/subprocess-overhead-free in the default tier
    (same substitution `tests/bench/test_runner.py`'s happy-path test makes).
    """
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--tb=no"], cwd=repo_dir, check=False
    ).returncode


def _copy_fixture(task_id: str, dest: Path) -> Path:
    fixture_src = _SUITE_PATH.parent / "tasks" / task_id / "fixture"
    repo_dir = dest / "repo"
    shutil.copytree(fixture_src, repo_dir)
    return repo_dir


def _apply_solution(repo_dir: Path) -> None:
    """Overlay-copy `.bench-solution/**` onto `repo_dir` then remove the marker dir --
    exactly what `FakeSubject(scripted_effect="copy-solution")` does (bench/subjects.py)."""
    solution_dir = repo_dir / ".bench-solution"
    for src in solution_dir.rglob("*"):
        if src.is_file():
            target = repo_dir / src.relative_to(solution_dir)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, target)
    shutil.rmtree(solution_dir)


# ---------------------------------------------------------------------------
# Suite + subject spec loading (AC1)
# ---------------------------------------------------------------------------


def test_suite_loads_and_validates() -> None:
    suite = load_suite(_SUITE_PATH)
    assert suite.id == "dev-core"
    assert 4 <= len(suite.tasks) <= 8


def test_suite_spans_all_four_categories_per_task_md() -> None:
    """TASK.md's own suite curation ask: 2 bugfix, 2 feature, 1 refactor, 1 test."""
    suite = load_suite(_SUITE_PATH)
    by_category: dict[str, int] = {}
    for t in suite.tasks:
        by_category[t.category] = by_category.get(t.category, 0) + 1
    assert by_category == {"bugfix": 2, "feature": 2, "refactor": 1, "test": 1}


def test_suite_task_ids_unique_and_lowercase() -> None:
    suite = load_suite(_SUITE_PATH)
    ids = [t.id for t in suite.tasks]
    assert len(ids) == len(set(ids))
    assert all(i == i.lower() for i in ids)


@pytest.mark.parametrize(
    "subject_file",
    [
        "fake-pass.json",
        "claude-haiku.json",
        "claude-sonnet.json",
        "claude-opus.json",
        "ao-epic-haiku.json",
        "ao-epic-sonnet.json",
    ],
)
def test_all_six_subject_files_load(subject_file: str) -> None:
    subject = load_subject(_SUBJECTS_DIR / subject_file)
    assert subject.id == subject_file.removesuffix(".json")


def test_claude_cli_subjects_use_bypass_permissions_and_pinned_model_ids() -> None:
    """R1 (learnings §21) + Q2 (design doc §18): committed claude_cli configs must set
    `bypassPermissions` (these tasks run tests) and pin a FULL model id, not a bare alias.
    """
    expected_models = {
        "claude-haiku": "claude-haiku-4-5-20251001",
        "claude-sonnet": "claude-sonnet-5",
        "claude-opus": "claude-opus-4-8",
    }
    for name, expected_model in expected_models.items():
        subject = load_subject(_SUBJECTS_DIR / f"{name}.json")
        assert subject.type == "claude_cli"
        assert subject.permission_mode == "bypassPermissions"
        assert subject.model == expected_model


def test_ao_epic_subjects_pin_model_via_subject_not_per_agent() -> None:
    """Q1 resolution: every agent in the ao-epic workflow uses the SAME model with NO
    per-agent pin, so the `--model`/`AO_MODEL` global-override clobber defect (learnings
    §"model override clobbers per-agent") can never silently diverge from what the
    subject config says -- there is nothing to clobber.
    """
    import json

    agents_data = json.loads((_SUBJECTS_DIR / "ao-epic" / "agents.json").read_text())
    for agent_name, agent_spec in agents_data["agents"].items():
        assert "model" not in agent_spec, (
            f"agents.json agent {agent_name!r} must not pin its own model "
            "(uniform-model clobber safety, Q1)"
        )

    haiku = load_subject(_SUBJECTS_DIR / "ao-epic-haiku.json")
    sonnet = load_subject(_SUBJECTS_DIR / "ao-epic-sonnet.json")
    assert haiku.type == sonnet.type == "ao_workflow"
    assert haiku.model == "claude-haiku-4-5-20251001"
    assert sonnet.model == "claude-sonnet-5"
    # Both variants must point at the SAME committed workflow template (they differ
    # only by model).
    assert haiku.workflow == sonnet.workflow
    assert haiku.agents == sonnet.agents


def test_ao_epic_workflow_assets_are_valid(tmp_path: Path) -> None:
    """The ao-epic subject's own workflow/reposet/agents template cross-validates via
    core `ao validate` semantics (spec.cross_validate) -- exercised directly here
    (network-free) rather than shelling to `uv run ao validate`.
    """
    from agent_orchestrator.config import load_agents, load_reposets
    from agent_orchestrator.spec import cross_validate, load_workflow

    ao_epic_dir = _SUBJECTS_DIR / "ao-epic"
    wf = load_workflow(ao_epic_dir / "workflow.json")
    reposet_map = load_reposets(ao_epic_dir / "reposet.json")
    agent_map = load_agents(ao_epic_dir / "agents.json")
    cross_validate(wf, reposet_map, agent_map)  # raises SpecValidationError on failure

    assert wf.id == wf.id.lower()
    task_ids = [t.id for t in wf.tasks]
    assert task_ids == ["implement", "verify"]
    # Both tasks must read the bench task's OWN materialized instruction (resolved
    # against the ephemeral per-run workspace, not this committed directory -- see
    # instructions/implement.md's module docstring for why).
    assert all(t.instruction == "repo/INSTRUCTION.md" for t in wf.tasks)
    assert all(agent_map[t.agent].working_dir == "repo" for t in wf.tasks)


# ---------------------------------------------------------------------------
# Per-task fixture behaviour: fails before, passes after (AC2)
# ---------------------------------------------------------------------------

_PYTEST_GRADED_TASKS = [
    "bugfix-off-by-one",
    "bugfix-grade-boundary",
    "feature-count-vowels",
    "feature-is-leap-year",
]


@pytest.mark.parametrize("task_id", _PYTEST_GRADED_TASKS)
def test_pytest_graded_task_fails_before_and_passes_after_solution(
    task_id: str, tmp_path: Path
) -> None:
    repo_dir = _copy_fixture(task_id, tmp_path)
    assert _run_pytest(repo_dir) != 0, (
        f"{task_id}: fixture's own tests must FAIL on the unmodified fixture"
    )

    _apply_solution(repo_dir)
    assert _run_pytest(repo_dir) == 0, (
        f"{task_id}: fixture's own tests must PASS once the reference solution is applied"
    )


def test_refactor_task_grader_fails_before_and_passes_after_solution(
    tmp_path: Path,
) -> None:
    repo_dir = _copy_fixture("refactor-extract-validation", tmp_path)

    # The refactor fixture's OWN tests already pass unmodified (behavior is correct);
    # only the DRY invariant is unmet -- distinguishes this task from the pytest-graded
    # ones above (TASK.md: "restructure a function while keeping its passing tests
    # green").
    assert _run_pytest(repo_dir) == 0

    check_rc = subprocess.run(
        [sys.executable, str(repo_dir / "check.py")], cwd=repo_dir, check=False
    ).returncode
    assert check_rc != 0, "check.py must reject the unmodified (duplicated) fixture"

    _apply_solution(repo_dir)
    assert _run_pytest(repo_dir) == 0
    check_rc = subprocess.run(
        [sys.executable, str(repo_dir / "check.py")], cwd=repo_dir, check=False
    ).returncode
    assert check_rc == 0, "check.py must accept the fixture once duplication is removed"


def test_test_writing_task_grader_rejects_missing_test_and_accepts_solution(
    tmp_path: Path,
) -> None:
    repo_dir = _copy_fixture("test-write-clamp", tmp_path)

    # No test written yet -- grader must reject.
    rc = subprocess.run(
        [sys.executable, str(repo_dir / "check_tests.py")], cwd=repo_dir, check=False
    ).returncode
    assert rc != 0

    _apply_solution(repo_dir)
    rc = subprocess.run(
        [sys.executable, str(repo_dir / "check_tests.py")], cwd=repo_dir, check=False
    ).returncode
    assert rc == 0, "check_tests.py must accept the real reference test"


def test_test_writing_task_grader_rejects_a_noop_test(tmp_path: Path) -> None:
    """The negative/mutation check (AC2, design §4.3): a test that would pass no matter
    what `clamp` does must NOT satisfy this task.
    """
    repo_dir = _copy_fixture("test-write-clamp", tmp_path)
    (repo_dir / "tests").mkdir()
    (repo_dir / "tests" / "test_mathutils.py").write_text("def test_noop():\n    assert True\n")
    rc = subprocess.run(
        [sys.executable, str(repo_dir / "check_tests.py")], cwd=repo_dir, check=False
    ).returncode
    assert rc != 0, "an empty/no-op test must be rejected by the mutation check"


# ---------------------------------------------------------------------------
# Full-suite FakeSubject run (AC2/AC3 prerequisite: harness plumbing works, no LLM)
# ---------------------------------------------------------------------------


def test_fake_subject_full_suite_run_produces_valid_run_json_and_summary(
    tmp_path: Path,
) -> None:
    record = run_suite(
        _SUITE_PATH,
        _SUBJECTS_DIR / "fake-pass.json",
        out_dir=tmp_path,
        clock=_fixed_clock,
    )

    assert record.suite_id == "dev-core"
    assert record.subject.id == "fake-pass"
    assert record.aggregate.total == len(load_suite(_SUITE_PATH).tasks)
    assert record.aggregate.solved == record.aggregate.total
    assert record.aggregate.solve_rate == 1.0
    assert all(t.solved for t in record.tasks)
    assert all(t.subject_status == "succeeded" for t in record.tasks)

    result_dir = tmp_path / record.bench_run_id
    summary_path = write_summary_md(record, result_dir)
    assert summary_path.is_file()
    assert "dev-core" in summary_path.read_text()

    reloaded = load_run(result_dir)
    assert reloaded.bench_run_id == record.bench_run_id
    assert reloaded.aggregate.solved == record.aggregate.solved


def test_committed_suite_task_fixture_never_mutated_by_a_fake_run(tmp_path: Path) -> None:
    """AC5: fixtures are never mutated by a run -- the runner always operates on a
    fresh workspace COPY (bench/workspace.py's `materialize_workspace`)."""
    committed_fixture = _SUITE_PATH.parent / "tasks" / "bugfix-off-by-one" / "fixture" / "stats.py"
    before = committed_fixture.read_text()

    run_suite(
        _SUITE_PATH,
        _SUBJECTS_DIR / "fake-pass.json",
        out_dir=tmp_path,
        clock=_fixed_clock,
    )

    assert committed_fixture.read_text() == before
    # The .bench-solution marker is consumed from the WORKSPACE copy only; the
    # committed fixture's own marker dir must still be there for the next run.
    assert (
        _SUITE_PATH.parent
        / "tasks"
        / "bugfix-off-by-one"
        / "fixture"
        / ".bench-solution"
        / "stats.py"
    ).is_file()


def test_suite_tasks_reference_only_existing_instruction_and_fixture_paths() -> None:
    suite: BenchSuite = load_suite(_SUITE_PATH)
    base = _SUITE_PATH.parent
    for task in suite.tasks:
        assert (base / task.instruction).is_file()
        assert (base / task.fixture).is_dir()
