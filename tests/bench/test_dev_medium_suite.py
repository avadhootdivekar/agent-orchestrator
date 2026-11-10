"""Deterministic (no LLM, no network) tests for the committed medium-tier `dev-medium`
suite and its fixtures (T-Md7Vc3 acceptance criteria 1/2/3/5/6, epic FR-7).

Mirrors `tests/bench/test_dev_core_suite.py`'s approach and conventions, scaled to
`dev-medium`'s held-out grading harness: every task here uses the `command` grader pointed
at `grading/run.py` (copied from the task's grader-owned `tasks/<id>/grading/` dir into the
run's agent-hidden `grading_dir` -- migrated out of the agent-readable `fixture/.grading/`
on 2026-10-23, HLD §8 "Hidden-asset rule"). `run.py --repo <repo>` re-runs the visible
`tests/` suite plus the held-out `grading/tests/`, or, for the test-writing task, a
multi-mutant mutation check -- not the plain `pytest` grader `dev-core` uses. Every
test here either loads committed spec files or runs a small, bounded local subprocess
(`sys.executable`, never `claude`; the single full-suite FakeSubject run goes through the
real `uv run python ...` grader command), so this file runs unconditionally in the
default (`not real_llm`) test tier, same as the rest of `tests/bench/`.

Covers:
  - the committed suite loads/validates cleanly, `tier == "medium"`, exactly 6 tasks
    spanning the ticket's required categories, unique lowercase ids, every task
    path-references an existing instruction/fixture (AC1, AC5);
  - for every task: the FULL grading harness (`grading/run.py`) FAILS on the
    unmodified fixture and PASSES once the task's own `.bench-solution/` reference
    overlay is applied (AC2, exercised here via a plain file copy -- exactly what
    `FakeSubject(scripted_effect="copy-solution")` does at bench-run time, so this is
    "no LLM needed" per the design doc);
  - the held-out `grading/` layer is what actually gates `solved`, not the visible
    `tests/` suite alone (AC3): for the refactor task, whose visible suite already
    passes unmodified, the full grader still fails on the unmodified fixture, proving
    the held-out tests are doing real, additional work;
  - a full `FakeSubject` run over the whole suite (`copy-solution` scripted) completes
    and writes a valid `run.json` + `summary.md` to a scratch (never committed) out-dir,
    and never mutates the committed fixtures;
  - nothing grader-owned is visible to the agent any more: no `.grading` dir under any
    fixture, no instruction or fixture file mentions it, the grading harness is copied into
    `grading_dir` (never `repo_dir`) at materialization;
  - no fixture/instruction/grading file under `benchmarks/suites/dev-medium/` embeds
    this checkout's own absolute path.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agent_orchestrator.bench.graders import expand_command
from agent_orchestrator.bench.results import load_run, write_summary_md
from agent_orchestrator.bench.runner import run_suite
from agent_orchestrator.bench.spec import BenchSuite, load_suite
from agent_orchestrator.bench.workspace import RunContext, materialize_workspace

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SUITE_PATH = _REPO_ROOT / "benchmarks" / "suites" / "dev-medium" / "suite.json"
_SUBJECTS_DIR = _REPO_ROOT / "benchmarks" / "subjects"

_FIXED_NOW = datetime(2026, 7, 22, 12, 0, 0, tzinfo=UTC)

# Tasks whose VISIBLE tests/ suite already passes unmodified (the bug/gap this task
# targets is invisible to the visible suite alone) -- used by
# test_held_out_layer_is_required_beyond_the_visible_suite below to prove the held-out
# `grading/` layer is doing real work, not just re-checking tests/ (AC3). The other 5
# tasks each have at least one VISIBLE test that already fails unmodified (the
# feature/bugfix/test-writing tasks' whole point is a visibly-failing test/missing
# test file) -- refactor-money-cents is the one task designed so behavior "looks"
# correct (its visible suite is green) and only the held-out precision/consistency
# checks catch the bug, per TASK.md category 2's own design guidance.
_TASKS_WHOSE_VISIBLE_SUITE_ALREADY_PASSES_UNMODIFIED = [
    "refactor-money-cents",
]


def _fixed_clock() -> datetime:
    return _FIXED_NOW


def _copy_fixture(task_id: str, dest: Path) -> Path:
    fixture_src = _SUITE_PATH.parent / "tasks" / task_id / "fixture"
    repo_dir = dest / "repo"
    shutil.copytree(fixture_src, repo_dir)
    return repo_dir


def _apply_solution(repo_dir: Path) -> None:
    """Overlay-copy `.bench-solution/**` onto `repo_dir` then remove the marker dir --
    exactly what `FakeSubject(scripted_effect="copy-solution")` does (bench/subjects.py).
    Works uniformly across all 6 dev-medium tasks, including the test-writing task
    whose overlay creates a brand new `tests/` directory rather than touching an
    existing source module.
    """
    solution_dir = repo_dir / ".bench-solution"
    for src in solution_dir.rglob("*"):
        if src.is_file():
            target = repo_dir / src.relative_to(solution_dir)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, target)
    shutil.rmtree(solution_dir)


def _run_pytest(repo_dir: Path, *targets: str) -> int:
    """Bare pytest invocation against one or more relative targets under `repo_dir`,
    swapping `uv run` for the current interpreter so this stays subprocess-overhead-
    free in the default tier (same substitution test_dev_core_suite.py's own
    `_run_pytest` makes)."""
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--tb=no", *targets],
        cwd=repo_dir,
        check=False,
    ).returncode


def _grading_script(task_id: str) -> Path:
    return _SUITE_PATH.parent / "tasks" / task_id / "grading" / "run.py"


def _run_grading_harness(task_id: str, repo_dir: Path) -> int:
    """Invoke the task's own `grading/run.py` exactly as the suite's `command` grader does
    (`uv run python {grading_dir}/run.py --repo {repo_dir}`, cwd = the mutated repo root),
    swapping `uv run` for the current interpreter (same rationale as `_run_pytest`)."""
    return subprocess.run(
        [sys.executable, str(_grading_script(task_id)), "--repo", str(repo_dir)],
        cwd=repo_dir,
        check=False,
    ).returncode


# ---------------------------------------------------------------------------
# Suite spec loading (AC1)
# ---------------------------------------------------------------------------


def test_suite_loads_and_validates_as_medium_tier() -> None:
    suite = load_suite(_SUITE_PATH)
    assert suite.id == "dev-medium"
    assert suite.tier == "medium"
    assert 4 <= len(suite.tasks) <= 8


def test_suite_has_six_tasks_spanning_the_ticket_required_categories() -> None:
    """TASK.md's task-design ask: feature-across-a-package, cross-cutting refactor,
    misleading-symptom bug, multi-file integration-point feature, a performance/
    correctness fix, and a test-writing task -- 6 tasks total."""
    suite = load_suite(_SUITE_PATH)
    assert len(suite.tasks) == 6
    by_category: dict[str, int] = {}
    for t in suite.tasks:
        by_category[t.category] = by_category.get(t.category, 0) + 1
    assert by_category == {"feature": 2, "refactor": 1, "bugfix": 2, "test": 1}


def test_suite_task_ids_unique_and_lowercase() -> None:
    suite = load_suite(_SUITE_PATH)
    ids = [t.id for t in suite.tasks]
    assert len(ids) == len(set(ids))
    assert all(i == i.lower() for i in ids)


def test_suite_tasks_reference_only_existing_instruction_and_fixture_paths() -> None:
    suite: BenchSuite = load_suite(_SUITE_PATH)
    base = _SUITE_PATH.parent
    for task in suite.tasks:
        assert (base / task.instruction).is_file()
        assert (base / task.fixture).is_dir()


def test_every_task_uses_the_held_out_command_grader_pointed_at_grading_run_py() -> None:
    suite = load_suite(_SUITE_PATH)
    for task in suite.tasks:
        assert task.grader.type == "command"
        assert task.grader.assets == f"tasks/{task.id}/grading"
        assert task.grader.command == "uv run python {grading_dir}/run.py --repo {repo_dir}"
        assert task.grader.cwd == "."
        assert _grading_script(task.id).is_file(), f"{task.id}: missing grading/run.py"
        assert (_SUITE_PATH.parent / task.grader.assets / "run.py").is_file()


def test_description_records_the_fingerprint_change() -> None:
    assert "2026-10-23" in load_suite(_SUITE_PATH).description
    assert "config_fingerprint" in load_suite(_SUITE_PATH).description


def test_every_task_declares_an_agent_work_size_tag() -> None:
    """AC5: each task is estimated at 30-90 min agent-work scale via a tag."""
    suite = load_suite(_SUITE_PATH)
    for task in suite.tasks:
        assert any("min" in tag for tag in task.tags), (
            f"{task.id}: expected a '~NNmin'-style tag documenting its estimated agent-work size"
        )


# ---------------------------------------------------------------------------
# Per-task fixture behaviour: full grading harness fails before, passes after the
# reference solution overlay (AC2), across all 6 tasks uniformly.
# ---------------------------------------------------------------------------

_ALL_TASK_IDS = [
    "feature-plugin-priority-registry",
    "refactor-money-cents",
    "bugfix-cache-key-collision",
    "feature-tracker-priority-filter",
    "bugfix-search-index-scan-budget",
    "test-write-validation-spec",
]


def test_suite_task_id_list_matches_the_committed_task_directories() -> None:
    suite = load_suite(_SUITE_PATH)
    assert sorted(t.id for t in suite.tasks) == sorted(_ALL_TASK_IDS)


@pytest.mark.parametrize("task_id", _ALL_TASK_IDS)
def test_task_grading_harness_fails_before_and_passes_after_solution(
    task_id: str, tmp_path: Path
) -> None:
    repo_dir = _copy_fixture(task_id, tmp_path)

    assert _run_grading_harness(task_id, repo_dir) != 0, (
        f"{task_id}: the FULL grading harness (grading/run.py) must FAIL on the "
        "unmodified fixture (AC2/R4 -- proves the grader discriminates before any "
        "spend)"
    )

    _apply_solution(repo_dir)
    assert _run_grading_harness(task_id, repo_dir) == 0, (
        f"{task_id}: the FULL grading harness must PASS once the reference solution "
        "overlay is applied"
    )


@pytest.mark.parametrize("task_id", _TASKS_WHOSE_VISIBLE_SUITE_ALREADY_PASSES_UNMODIFIED)
def test_held_out_layer_is_required_beyond_the_visible_suite(task_id: str, tmp_path: Path) -> None:
    """AC3: for a task whose VISIBLE tests/ suite already passes on the unmodified
    fixture (the refactor and performance/correctness tasks -- their bug is invisible
    to the visible suite alone), the FULL grading harness must still fail, proving the
    held-out `grading/tests/` layer -- not the visible suite -- is what gates
    `solved`.
    """
    repo_dir = _copy_fixture(task_id, tmp_path)

    assert _run_pytest(repo_dir, "tests") == 0, (
        f"{task_id}: expected the VISIBLE tests/ suite to already pass unmodified "
        "(that is what makes this task's bug a held-out-only regression)"
    )
    assert _run_grading_harness(task_id, repo_dir) != 0, (
        f"{task_id}: the full grading harness (visible + held-out) must still fail "
        "even though the visible suite alone passes"
    )


def test_test_writing_task_grading_harness_rejects_a_missing_test_file(tmp_path: Path) -> None:
    """The test-writing task's own no-op case: no tests/test_validation.py exists at
    all on the unmodified fixture (distinct from "tests exist but fail", covered by
    the parametrized fail-before check above -- this asserts the SPECIFIC reason)."""
    repo_dir = _copy_fixture("test-write-validation-spec", tmp_path)
    assert not (repo_dir / "tests" / "test_validation.py").exists()
    assert _run_grading_harness("test-write-validation-spec", repo_dir) != 0


# ---------------------------------------------------------------------------
# Full-suite FakeSubject run (harness plumbing works end to end, no LLM)
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

    assert record.suite_id == "dev-medium"
    assert record.subject.id == "fake-pass"
    assert record.aggregate.total == len(load_suite(_SUITE_PATH).tasks)
    assert record.aggregate.solved == record.aggregate.total
    assert record.aggregate.solve_rate == 1.0
    assert all(t.solved for t in record.tasks)
    assert all(t.subject_status == "succeeded" for t in record.tasks)

    result_dir = tmp_path / record.bench_run_id
    summary_path = write_summary_md(record, result_dir)
    assert summary_path.is_file()
    assert "dev-medium" in summary_path.read_text()

    reloaded = load_run(result_dir)
    assert reloaded.bench_run_id == record.bench_run_id
    assert reloaded.aggregate.solved == record.aggregate.solved


def test_committed_suite_task_fixture_never_mutated_by_a_fake_run(tmp_path: Path) -> None:
    """Fixtures are never mutated by a run -- the runner always operates on a fresh
    workspace COPY (bench/workspace.py's `materialize_workspace`)."""
    committed_fixture = (
        _SUITE_PATH.parent
        / "tasks"
        / "feature-plugin-priority-registry"
        / "fixture"
        / "registry"
        / "core.py"
    )
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
        / "feature-plugin-priority-registry"
        / "fixture"
        / ".bench-solution"
        / "registry"
        / "core.py"
    ).is_file()


# ---------------------------------------------------------------------------
# Hidden-asset rule (HLD §8): nothing grader-owned is visible to the agent.
# ---------------------------------------------------------------------------


def test_no_grading_dir_remains_under_any_fixture() -> None:
    tasks_dir = _SUITE_PATH.parent / "tasks"
    leftovers = [p for p in tasks_dir.rglob(".grading")]
    assert not leftovers, f"agent-readable .grading dirs remain: {leftovers}"
    for task_id in _ALL_TASK_IDS:
        grading = tasks_dir / task_id / "grading"
        assert (grading / "run.py").is_file(), task_id
        assert any(grading.glob("tests/*.py")) or task_id == "test-write-validation-spec", task_id


def test_no_instruction_or_fixture_file_mentions_the_held_out_harness() -> None:
    tasks_dir = _SUITE_PATH.parent / "tasks"
    offenders = []
    for task_id in _ALL_TASK_IDS:
        paths = [
            tasks_dir / task_id / "instruction.md",
            *(tasks_dir / task_id / "fixture").rglob("*"),
        ]
        for path in paths:
            if path.is_file() and ".grading" in path.read_text(errors="replace"):
                offenders.append(str(path.relative_to(tasks_dir)))
    assert not offenders, f"agent-visible files still mention .grading: {offenders}"


@pytest.mark.parametrize("task_id", _ALL_TASK_IDS)
def test_grading_harness_lands_in_grading_dir_never_in_the_agent_workspace(
    task_id: str, bench_ws: Path
) -> None:
    task = load_suite(_SUITE_PATH).task(task_id)
    ctx = materialize_workspace(
        task,
        bench_ws / "ws",
        suite_base_dir=_SUITE_PATH.parent,
        timeout_seconds=60,
        grading_dir=bench_ws / "grading",
    )
    repo, grading = Path(ctx.repo_dir), Path(ctx.grading_dir or "")
    assert (grading / "run.py").is_file()
    assert not (repo / ".grading").exists() and not (repo / "grading").exists()
    held_out = {p.name for p in grading.rglob("test_*.py")}
    visible = {p.name for p in repo.rglob("test_*.py")}
    assert not held_out & visible, "a held-out test file leaked into the agent workspace"


def test_grader_command_expands_to_the_hidden_run_py_and_the_repo() -> None:
    task = load_suite(_SUITE_PATH).task("bugfix-cache-key-collision")
    ctx = RunContext(
        workspace="/w",
        repo_dir="/w/repo",
        instruction_path="/w/repo/INSTRUCTION.md",
        capture_dir="/w/capture",
        timeout_seconds=1,
        grading_dir="/g/bugfix-cache-key-collision",
    )
    assert expand_command(task.grader.command or "", ctx) == (
        "uv run python /g/bugfix-cache-key-collision/run.py --repo /w/repo"
    )


@pytest.mark.parametrize("task_id", _ALL_TASK_IDS)
def test_run_py_defaults_to_cwd_when_repo_flag_is_omitted(task_id: str, tmp_path: Path) -> None:
    """`--repo` is optional (default: cwd), and the script never derives the repo from its
    own location any more (it now lives outside the repo)."""
    repo_dir = _copy_fixture(task_id, tmp_path)
    _apply_solution(repo_dir)
    rc = subprocess.run(
        [sys.executable, str(_grading_script(task_id))], cwd=repo_dir, check=False
    ).returncode
    assert rc == 0


# ---------------------------------------------------------------------------
# Portability hygiene: no committed file embeds this checkout's own absolute path.
# ---------------------------------------------------------------------------


def test_no_committed_dev_medium_file_embeds_this_checkouts_absolute_path() -> None:
    repo_root_str = str(_REPO_ROOT)
    offenders = []
    for path in _SUITE_PATH.parent.rglob("*"):
        if path.is_file() and path.suffix in (".py", ".md", ".json"):
            text = path.read_text(errors="replace")
            if repo_root_str in text:
                offenders.append(str(path))
    assert not offenders, f"absolute checkout path embedded in: {offenders}"
