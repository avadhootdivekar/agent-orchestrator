"""Unit tests for bench/workspace.py: materialize_workspace (AC6 of T-Sbj9Ka).

Escape/happy-path tests target the REAL `BENCH_WORKSPACE_ROOT`
(`playground/.tmp/bench/tests/<uuid>/...`), not `tmp_path` -- the path guard is defined
in terms of that real, repo-local, gitignored root (C2), so exercising it against a
fake root would not prove anything about the actual guard. Workspaces created here are
deliberately NOT cleaned up (mirrors `tests/playground/conftest.py`'s
`real_llm_workspace` fixture: "Workspaces are deliberately preserved after the test" --
also documented as an intentional bench edge case, design doc §4.5: "disk cleanup is
manual... workspaces preserved for audit").
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from agent_orchestrator.bench.errors import SubjectError
from agent_orchestrator.bench.spec import BenchTask, GraderConfig
from agent_orchestrator.bench.workspace import (
    BENCH_WORKSPACE_ROOT,
    RunContext,
    materialize_workspace,
)


@pytest.fixture()
def bench_ws_target() -> Path:
    """A fresh, unique target dir under the real `playground/.tmp/bench/tests/` root --
    resolves under `BENCH_WORKSPACE_ROOT` so `materialize_workspace`'s guard passes.
    Not created here: `materialize_workspace` itself must create it.
    """
    (BENCH_WORKSPACE_ROOT / "tests").mkdir(parents=True, exist_ok=True)
    return BENCH_WORKSPACE_ROOT / "tests" / f"ws-{uuid.uuid4().hex[:12]}"


def _task(
    task_id: str = "t1",
    *,
    instruction: str = "tasks/t1/instruction.md",
    fixture: str = "tasks/t1/fixture",
) -> BenchTask:
    return BenchTask(
        id=task_id,
        category="bugfix",
        instruction=instruction,
        fixture=fixture,
        grader=GraderConfig(type="fake"),
    )


def _materialize_fixture(
    suite_base: Path, task: BenchTask, *, content: str = "print('hi')\n"
) -> None:
    fixture_dir = suite_base / task.fixture
    fixture_dir.mkdir(parents=True, exist_ok=True)
    (fixture_dir / "app.py").write_text(content)
    instruction_path = suite_base / task.instruction
    instruction_path.parent.mkdir(parents=True, exist_ok=True)
    instruction_path.write_text("# Fix the bug\n")


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_materialize_workspace_copies_fixture_and_instruction(
    tmp_path: Path, bench_ws_target: Path
) -> None:
    task = _task()
    _materialize_fixture(tmp_path, task)

    ctx = materialize_workspace(task, bench_ws_target, suite_base_dir=tmp_path, timeout_seconds=60)

    assert isinstance(ctx, RunContext)
    assert ctx.workspace == str(bench_ws_target)
    assert ctx.repo_dir == str(bench_ws_target / "repo")
    assert Path(ctx.repo_dir, "app.py").read_text() == "print('hi')\n"
    assert Path(ctx.instruction_path) == bench_ws_target / "repo" / "INSTRUCTION.md"
    assert Path(ctx.instruction_path).read_text() == "# Fix the bug\n"
    assert Path(ctx.capture_dir).is_dir()
    assert list(Path(ctx.capture_dir).iterdir()) == []
    assert ctx.timeout_seconds == 60
    assert ctx.budget_total is None
    assert ctx.max_turns is None


def test_materialize_workspace_never_mutates_committed_fixture(
    tmp_path: Path, bench_ws_target: Path
) -> None:
    task = _task()
    _materialize_fixture(tmp_path, task)
    fixture_dir = tmp_path / task.fixture

    materialize_workspace(task, bench_ws_target, suite_base_dir=tmp_path, timeout_seconds=60)

    # The workspace copy is free to diverge; the committed fixture must not.
    (bench_ws_target / "repo" / "app.py").write_text("mutated\n")
    assert (fixture_dir / "app.py").read_text() == "print('hi')\n"


def test_materialize_workspace_fresh_copy_removes_stale_files(
    tmp_path: Path, bench_ws_target: Path
) -> None:
    task = _task()
    _materialize_fixture(tmp_path, task)

    ctx1 = materialize_workspace(task, bench_ws_target, suite_base_dir=tmp_path, timeout_seconds=60)
    stray = Path(ctx1.repo_dir) / "stray-from-a-run.txt"
    stray.write_text("leftover\n")
    assert stray.exists()

    materialize_workspace(task, bench_ws_target, suite_base_dir=tmp_path, timeout_seconds=60)

    assert not stray.exists()


def test_materialize_workspace_passes_through_run_knobs(
    tmp_path: Path, bench_ws_target: Path
) -> None:
    task = _task()
    _materialize_fixture(tmp_path, task)

    ctx = materialize_workspace(
        task,
        bench_ws_target,
        suite_base_dir=tmp_path,
        timeout_seconds=120,
        budget_total=5000,
        max_turns=10,
        subject_base_dir=tmp_path / "subjects",
        workflow_json="some/workflow.json",
    )

    assert ctx.timeout_seconds == 120
    assert ctx.budget_total == 5000
    assert ctx.max_turns == 10
    assert ctx.subject_base_dir == str(tmp_path / "subjects")
    assert ctx.workflow_json == "some/workflow.json"


# ---------------------------------------------------------------------------
# Path-escape guard (AC6)
# ---------------------------------------------------------------------------


def test_materialize_workspace_rejects_dotdot_traversal(tmp_path: Path) -> None:
    task = _task()
    _materialize_fixture(tmp_path, task)
    evil_ws = BENCH_WORKSPACE_ROOT / "tests" / ".." / ".." / ".." / "escaped-bench-test"

    with pytest.raises(SubjectError, match="escapes the sandbox"):
        materialize_workspace(task, evil_ws, suite_base_dir=tmp_path, timeout_seconds=60)

    # Never actually created outside the sandbox.
    assert not (BENCH_WORKSPACE_ROOT.parent.parent / "escaped-bench-test").exists()


def test_materialize_workspace_rejects_symlink_escape(tmp_path: Path) -> None:
    task = _task()
    _materialize_fixture(tmp_path, task)

    outside = tmp_path / "outside-the-sandbox"
    outside.mkdir()
    link_parent = BENCH_WORKSPACE_ROOT / "tests"
    link_parent.mkdir(parents=True, exist_ok=True)
    link = link_parent / f"evil-symlink-{uuid.uuid4().hex[:8]}"
    link.symlink_to(outside, target_is_directory=True)
    try:
        evil_ws = link / "subject" / "task"
        with pytest.raises(SubjectError, match="escapes the sandbox"):
            materialize_workspace(task, evil_ws, suite_base_dir=tmp_path, timeout_seconds=60)
        assert list(outside.iterdir()) == []
    finally:
        link.unlink()


def test_materialize_workspace_allows_root_itself() -> None:
    # Boundary case: ws == BENCH_WORKSPACE_ROOT resolves as "under" its own root
    # (mirrors LocalFsArtifactStore.resolve's `full == self._root` allowance) --
    # exercised via the private guard directly to avoid clobbering the shared root
    # other tests rely on existing.
    from agent_orchestrator.bench.workspace import _assert_under_bench_root

    resolved = _assert_under_bench_root(BENCH_WORKSPACE_ROOT)
    assert resolved == BENCH_WORKSPACE_ROOT.resolve()


# ---------------------------------------------------------------------------
# Missing fixture / instruction on disk
# ---------------------------------------------------------------------------


def test_materialize_workspace_missing_fixture_raises(
    tmp_path: Path, bench_ws_target: Path
) -> None:
    task = _task()
    instruction_path = tmp_path / task.instruction
    instruction_path.parent.mkdir(parents=True, exist_ok=True)
    instruction_path.write_text("# fix\n")
    # fixture dir intentionally not created

    with pytest.raises(SubjectError, match="fixture directory not found"):
        materialize_workspace(task, bench_ws_target, suite_base_dir=tmp_path, timeout_seconds=60)


def test_materialize_workspace_missing_instruction_raises(
    tmp_path: Path, bench_ws_target: Path
) -> None:
    task = _task()
    fixture_dir = tmp_path / task.fixture
    fixture_dir.mkdir(parents=True)
    (fixture_dir / "app.py").write_text("print('hi')\n")
    # instruction file intentionally not created

    with pytest.raises(SubjectError, match="instruction file not found"):
        materialize_workspace(task, bench_ws_target, suite_base_dir=tmp_path, timeout_seconds=60)
