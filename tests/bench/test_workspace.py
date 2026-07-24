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
from agent_orchestrator.bench.registries import (
    WORKSPACE_PROVIDER_REGISTRY,
    register_workspace_provider,
)
from agent_orchestrator.bench.spec import BenchTask, GraderConfig, Source
from agent_orchestrator.bench.workspace import (
    BENCH_WORKSPACE_ROOT,
    FixtureProvider,
    RunContext,
    WorkspaceProvider,
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
    assert task.fixture is not None  # every task built by _task() below sets one
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
    assert task.fixture is not None
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
    assert task.fixture is not None
    fixture_dir = tmp_path / task.fixture
    fixture_dir.mkdir(parents=True)
    (fixture_dir / "app.py").write_text("print('hi')\n")
    # instruction file intentionally not created

    with pytest.raises(SubjectError, match="instruction file not found"):
        materialize_workspace(task, bench_ws_target, suite_base_dir=tmp_path, timeout_seconds=60)


# ---------------------------------------------------------------------------
# WorkspaceProvider seam (T-Wp4Nz5, FR-4): registry wiring + provider dispatch.
# ---------------------------------------------------------------------------


def test_workspace_provider_registry_contains_fixture_at_import_time() -> None:
    # AC2: importing bench.workspace (already imported at module scope above) is
    # enough to register the default provider.
    assert WORKSPACE_PROVIDER_REGISTRY["fixture"] is FixtureProvider


def test_fixture_provider_is_a_workspace_provider() -> None:
    assert issubclass(FixtureProvider, WorkspaceProvider)


def test_workspace_provider_abc_cannot_be_instantiated_directly() -> None:
    with pytest.raises(TypeError):
        WorkspaceProvider()  # type: ignore[abstract]


class _RecordingProvider(WorkspaceProvider):
    """Test double: records the exact args it was called with and writes a marker
    file into `repo_dir` instead of copying a fixture -- proves `materialize_workspace`
    dispatches to a task's declared `source.type` rather than always using `fixture`.
    """

    calls: list[dict] = []

    def prepare(self, task, repo_dir, *, suite_base_dir, subject_base_dir=None) -> None:
        self.__class__.calls.append(
            {
                "task_id": task.id,
                "repo_dir": repo_dir,
                "suite_base_dir": suite_base_dir,
                "subject_base_dir": subject_base_dir,
            }
        )
        repo_dir.mkdir(parents=True)
        (repo_dir / "marker.txt").write_text("materialized by _RecordingProvider\n")


def test_materialize_workspace_dispatches_to_declared_source_provider(
    tmp_path: Path, bench_ws_target: Path
) -> None:
    register_workspace_provider("_recording_test_provider", _RecordingProvider)
    _RecordingProvider.calls = []
    try:
        task = BenchTask(
            id="swebench-like",
            category="bugfix",
            instruction="tasks/swebench-like/instruction.md",
            source=Source(type="_recording_test_provider"),
            grader=GraderConfig(type="fake"),
        )
        instruction_path = tmp_path / task.instruction
        instruction_path.parent.mkdir(parents=True, exist_ok=True)
        instruction_path.write_text("# fix\n")

        ctx = materialize_workspace(
            task,
            bench_ws_target,
            suite_base_dir=tmp_path,
            timeout_seconds=60,
            subject_base_dir=tmp_path / "subjects",
        )

        assert len(_RecordingProvider.calls) == 1
        call = _RecordingProvider.calls[0]
        assert call["task_id"] == "swebench-like"
        assert call["repo_dir"] == bench_ws_target / "repo"
        assert call["suite_base_dir"] == tmp_path
        assert call["subject_base_dir"] == tmp_path / "subjects"

        # The provider's own output is present ...
        marker_text = Path(ctx.repo_dir, "marker.txt").read_text()
        assert marker_text == "materialized by _RecordingProvider\n"
        # ... and materialize_workspace's own common steps (INSTRUCTION.md copy,
        # capture/ dir, RunContext) still run exactly as for the fixture provider.
        assert Path(ctx.instruction_path) == bench_ws_target / "repo" / "INSTRUCTION.md"
        assert Path(ctx.instruction_path).read_text() == "# fix\n"
        assert Path(ctx.capture_dir).is_dir()
    finally:
        del WORKSPACE_PROVIDER_REGISTRY["_recording_test_provider"]


def test_materialize_workspace_unregistered_provider_type_raises(
    tmp_path: Path, bench_ws_target: Path
) -> None:
    # Defense-in-depth: bench/spec.py's load_suite already rejects an unknown
    # source.type at suite-load time, but a BenchTask built directly (bypassing
    # load_suite, as here) must not silently KeyError inside materialize_workspace.
    task = BenchTask(
        id="bad-provider",
        category="bugfix",
        instruction="tasks/bad-provider/instruction.md",
        source=Source(type="does_not_exist"),
        grader=GraderConfig(type="fake"),
    )
    instruction_path = tmp_path / task.instruction
    instruction_path.parent.mkdir(parents=True, exist_ok=True)
    instruction_path.write_text("# fix\n")

    with pytest.raises(SubjectError, match="unknown workspace provider"):
        materialize_workspace(task, bench_ws_target, suite_base_dir=tmp_path, timeout_seconds=60)


def test_fixture_provider_requires_task_fixture_when_source_absent(tmp_path: Path) -> None:
    # Defense-in-depth (bench/spec.py's load_suite already rejects this combination at
    # suite-load time): a BenchTask with neither fixture nor source, handed directly to
    # FixtureProvider, raises a typed SubjectError rather than an AttributeError.
    task = BenchTask(
        id="no-fixture",
        category="bugfix",
        instruction="tasks/no-fixture/instruction.md",
        grader=GraderConfig(type="fake"),
    )
    with pytest.raises(SubjectError, match="requires task.fixture"):
        FixtureProvider().prepare(task, tmp_path / "repo", suite_base_dir=tmp_path)


def test_materialize_workspace_sandbox_guard_applies_before_provider_dispatch(
    tmp_path: Path,
) -> None:
    # AC5: the path-escape guard applies to the workspace target regardless of which
    # provider a task declares.
    register_workspace_provider("_never_called_provider", _RecordingProvider)
    _RecordingProvider.calls = []
    try:
        task = BenchTask(
            id="escape-with-source",
            category="bugfix",
            instruction="tasks/escape-with-source/instruction.md",
            source=Source(type="_never_called_provider"),
            grader=GraderConfig(type="fake"),
        )
        evil_ws = BENCH_WORKSPACE_ROOT / "tests" / ".." / ".." / ".." / "escaped-bench-test-2"

        with pytest.raises(SubjectError, match="escapes the sandbox"):
            materialize_workspace(task, evil_ws, suite_base_dir=tmp_path, timeout_seconds=60)

        assert _RecordingProvider.calls == []
    finally:
        del WORKSPACE_PROVIDER_REGISTRY["_never_called_provider"]
