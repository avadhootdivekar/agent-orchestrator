"""Tests for `agent_orchestrator.isolation.view.IsolatedArtifactView` (E-Wk9Tz3 T-Wk3Nv6,
review finding S-4).

No git needed here -- `IsolatedArtifactView` is pure path-containment logic layered over
`LocalFsArtifactStore`. Real directories/symlinks on `tmp_path` are used where symlink
resolution matters (AC-18b/c).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import agent_orchestrator
from agent_orchestrator.artifacts import LocalFsArtifactStore
from agent_orchestrator.errors import ArtifactPathError
from agent_orchestrator.isolation.view import IsolatedArtifactView
from agent_orchestrator.isolation.worktrees import RepoIsolation, TaskIsolation
from agent_orchestrator.runstate import RunStateStore


def _repo_iso(toplevel: Path, worktree_root: Path, *, key: str = "core-x") -> RepoIsolation:
    return RepoIsolation(
        key=key,
        toplevel=str(toplevel),
        common_dir=str(toplevel / ".git"),
        worktree_root=str(worktree_root),
        branch=f"ao/run-1/task-{key}",
        base="deadbeef" * 5,
        members=[],
    )


def _task_iso(
    workspace_root: Path, repos: list[RepoIsolation], *, task_id: str = "task-a"
) -> TaskIsolation:
    return TaskIsolation(
        task_id=task_id,
        cycle=1,
        declared_outputs=[],
        workspace_root=str(workspace_root),
        repos=repos,
    )


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    (ws / "repo").mkdir(parents=True)
    (ws / "repo" / "src.py").write_text("x = 1\n")
    (ws / ".orchestrator" / "runs" / "run-1").mkdir(parents=True)
    (ws / ".orchestrator" / "runs" / "run-1" / "state.json").write_text("{}")
    return ws


class TestBasicRemap:
    def test_path_inside_isolated_repo_remaps_into_worktree(
        self, tmp_path: Path, workspace: Path
    ) -> None:
        base = LocalFsArtifactStore(str(workspace))
        wt_a = tmp_path / "state" / "worktrees" / "task-a" / "repo"
        wt_a.mkdir(parents=True)
        iso = _task_iso(workspace, [_repo_iso(workspace / "repo", wt_a)])
        view = IsolatedArtifactView(base, iso)

        resolved = view.resolve("repo/src.py")

        assert resolved == str(wt_a / "src.py")

    def test_path_outside_every_repo_stays_shared(self, tmp_path: Path, workspace: Path) -> None:
        base = LocalFsArtifactStore(str(workspace))
        wt_a = tmp_path / "state" / "worktrees" / "task-a" / "repo"
        wt_a.mkdir(parents=True)
        (workspace / "outputs").mkdir()
        iso = _task_iso(workspace, [_repo_iso(workspace / "repo", wt_a)])
        view = IsolatedArtifactView(base, iso)

        resolved = view.resolve("outputs/x.md")

        assert resolved == str(workspace / "outputs" / "x.md")

    def test_orchestrator_dir_stays_shared_even_though_it_is_under_repo_root(
        self, tmp_path: Path, workspace: Path
    ) -> None:
        # Repo toplevel IS the workspace root here -- .orchestrator/ must still stay shared.
        base = LocalFsArtifactStore(str(workspace))
        wt_a = tmp_path / "state" / "worktrees" / "task-a" / "repo"
        wt_a.mkdir(parents=True)
        iso = _task_iso(workspace, [_repo_iso(workspace, wt_a)])
        view = IsolatedArtifactView(base, iso)

        resolved = view.resolve(".orchestrator/runs/run-1/state.json")

        assert resolved == str(workspace / ".orchestrator" / "runs" / "run-1" / "state.json")

    def test_absolute_path_under_own_worktree_resolves(
        self, tmp_path: Path, workspace: Path
    ) -> None:
        base = LocalFsArtifactStore(str(workspace))
        wt_a = tmp_path / "state" / "worktrees" / "task-a" / "repo"
        wt_a.mkdir(parents=True)
        (wt_a / "generated.txt").write_text("hi\n")
        iso = _task_iso(workspace, [_repo_iso(workspace / "repo", wt_a)])
        view = IsolatedArtifactView(base, iso)

        resolved = view.resolve(str(wt_a / "generated.txt"))

        assert resolved == str(wt_a / "generated.txt")
        assert view.exists(str(wt_a / "generated.txt")) is True


class TestTraversalStillRejected:
    def test_relative_traversal_raises(self, tmp_path: Path, workspace: Path) -> None:
        base = LocalFsArtifactStore(str(workspace))
        wt_a = tmp_path / "state" / "worktrees" / "task-a" / "repo"
        wt_a.mkdir(parents=True)
        iso = _task_iso(workspace, [_repo_iso(workspace / "repo", wt_a)])
        view = IsolatedArtifactView(base, iso)

        with pytest.raises(ArtifactPathError):
            view.resolve("../../etc/passwd")

    def test_absolute_path_outside_workspace_and_worktrees_raises(
        self, tmp_path: Path, workspace: Path
    ) -> None:
        base = LocalFsArtifactStore(str(workspace))
        wt_a = tmp_path / "state" / "worktrees" / "task-a" / "repo"
        wt_a.mkdir(parents=True)
        iso = _task_iso(workspace, [_repo_iso(workspace / "repo", wt_a)])
        view = IsolatedArtifactView(base, iso)

        with pytest.raises(ArtifactPathError):
            view.resolve(str(tmp_path / "somewhere-else" / "f.txt"))


class TestSymlinkEscapeInsideOwnWorktreeStillRejected:
    """AC-18c: a symlink inside A's OWN worktree pointing outside still raises. The route
    that actually gets symlink-resolved is an already-absolute path under the worktree
    (`repo_paths`/`cwd` entries the engine builds are absolute worktree paths) -- resolution
    happens before the containment test, exactly like the base store.
    """

    def test_symlink_escape_raises(self, tmp_path: Path, workspace: Path) -> None:
        base = LocalFsArtifactStore(str(workspace))
        wt_a = tmp_path / "state" / "worktrees" / "task-a" / "repo"
        wt_a.mkdir(parents=True)
        outside = tmp_path / "outside-secret"
        outside.mkdir()
        (outside / "secret.txt").write_text("nope\n")
        os.symlink(outside, wt_a / "escape")
        iso = _task_iso(workspace, [_repo_iso(workspace / "repo", wt_a)])
        view = IsolatedArtifactView(base, iso)

        with pytest.raises(ArtifactPathError):
            view.resolve(str(wt_a / "escape" / "secret.txt"))


class TestSiblingTaskIsolation:
    """AC-18a: an absolute path under task B's worktree, submitted as an input, an output,
    and as `cwd` for task A, raises from A's view -- three cases, same mechanism.
    """

    @pytest.fixture
    def views(self, tmp_path: Path, workspace: Path) -> tuple[IsolatedArtifactView, Path]:
        base = LocalFsArtifactStore(str(workspace))
        wt_a = tmp_path / "state" / "worktrees" / "task-a" / "repo"
        wt_b = tmp_path / "state" / "worktrees" / "task-b" / "repo"
        wt_a.mkdir(parents=True)
        wt_b.mkdir(parents=True)
        (wt_b / "uncommitted.txt").write_text("task B's private work\n")
        iso_a = _task_iso(
            workspace, [_repo_iso(workspace / "repo", wt_a, key="core-a")], task_id="task-a"
        )
        view_a = IsolatedArtifactView(base, iso_a)
        return view_a, wt_b

    def test_as_input(self, views: tuple[IsolatedArtifactView, Path]) -> None:
        view_a, wt_b = views
        with pytest.raises(ArtifactPathError):
            view_a.resolve(str(wt_b / "uncommitted.txt"))

    def test_as_output(self, views: tuple[IsolatedArtifactView, Path]) -> None:
        view_a, wt_b = views
        with pytest.raises(ArtifactPathError):
            view_a.resolve(str(wt_b / "new-output.txt"))

    def test_as_cwd(self, views: tuple[IsolatedArtifactView, Path]) -> None:
        view_a, wt_b = views
        with pytest.raises(ArtifactPathError):
            view_a.resolve(str(wt_b))

    def test_view_built_from_manager_registry_shape_would_be_wrong(
        self, tmp_path: Path, workspace: Path
    ) -> None:
        """Regression guard for the exact mistake S-4 calls out: this asserts the view
        constructed from task A's OWN `TaskIsolation` alone (never a second task's) cannot
        reach task B's worktree -- i.e. that `IsolatedArtifactView.__init__` takes one
        `TaskIsolation`, not a registry of every worktree in the run.
        """
        base = LocalFsArtifactStore(str(workspace))
        wt_a = tmp_path / "state" / "worktrees" / "task-a" / "repo"
        wt_a.mkdir(parents=True)
        iso_a = _task_iso(
            workspace, [_repo_iso(workspace / "repo", wt_a, key="core-a")], task_id="task-a"
        )
        view_a = IsolatedArtifactView(base, iso_a)
        assert len(view_a._roots) == 1
        assert view_a._roots[0] == str(wt_a)


class TestCrossRunIsolation:
    def test_absolute_path_under_a_different_runs_worktree_raises(
        self, tmp_path: Path, workspace: Path
    ) -> None:
        base = LocalFsArtifactStore(str(workspace))
        wt_run1 = tmp_path / "state" / "worktrees" / "run-1" / "task-a" / "repo"
        wt_run2 = tmp_path / "state" / "worktrees" / "run-2" / "task-a" / "repo"
        wt_run1.mkdir(parents=True)
        wt_run2.mkdir(parents=True)
        (wt_run2 / "other-run.txt").write_text("belongs to run-2\n")
        iso = _task_iso(workspace, [_repo_iso(workspace / "repo", wt_run1)])
        view = IsolatedArtifactView(base, iso)

        with pytest.raises(ArtifactPathError):
            view.resolve(str(wt_run2 / "other-run.txt"))


class TestExistsAndSize:
    def test_exists_false_on_path_error(self, tmp_path: Path, workspace: Path) -> None:
        base = LocalFsArtifactStore(str(workspace))
        wt_a = tmp_path / "state" / "worktrees" / "task-a" / "repo"
        wt_a.mkdir(parents=True)
        iso = _task_iso(workspace, [_repo_iso(workspace / "repo", wt_a)])
        view = IsolatedArtifactView(base, iso)

        assert view.exists("../../etc/passwd") is False

    def test_size_zero_on_missing(self, tmp_path: Path, workspace: Path) -> None:
        base = LocalFsArtifactStore(str(workspace))
        wt_a = tmp_path / "state" / "worktrees" / "task-a" / "repo"
        wt_a.mkdir(parents=True)
        iso = _task_iso(workspace, [_repo_iso(workspace / "repo", wt_a)])
        view = IsolatedArtifactView(base, iso)

        assert view.size("repo/does-not-exist.txt") == 0

    def test_size_matches_real_file(self, tmp_path: Path, workspace: Path) -> None:
        base = LocalFsArtifactStore(str(workspace))
        wt_a = tmp_path / "state" / "worktrees" / "task-a" / "repo"
        wt_a.mkdir(parents=True)
        content = b"hello world"
        (wt_a / "out.txt").write_bytes(content)
        iso = _task_iso(workspace, [_repo_iso(workspace / "repo", wt_a)])
        view = IsolatedArtifactView(base, iso)

        assert view.size("repo/out.txt") == len(content)


class TestRunStateStoreNeverWrapped:
    """AC-18d: RunStateStore's store is never an IsolatedArtifactView."""

    def test_run_state_store_keeps_the_base_store(self, workspace: Path) -> None:
        base = LocalFsArtifactStore(str(workspace))
        rs_store = RunStateStore(str(workspace), base)
        assert not isinstance(rs_store._store, IsolatedArtifactView)
        assert rs_store._store is base


class TestResolveUncheckedStructuralGuard:
    """C-10: `resolve_unchecked` skips the traversal guard by design (it is the building
    block `IsolatedArtifactView.resolve` layers its own containment test on top of) -- so
    it must never be reachable from anywhere else in the codebase, now or in the future.
    """

    def test_resolve_unchecked_referenced_only_in_its_two_sanctioned_files(self) -> None:
        src_root = Path(agent_orchestrator.__file__).parent
        sanctioned = {Path("artifacts.py"), Path("isolation") / "view.py"}
        offenders = []
        for py_file in src_root.rglob("*.py"):
            rel = py_file.relative_to(src_root)
            if rel in sanctioned:
                continue
            if "resolve_unchecked" in py_file.read_text():
                offenders.append(str(rel))
        assert offenders == [], (
            f"resolve_unchecked referenced outside its sanctioned callers: {offenders}"
        )
