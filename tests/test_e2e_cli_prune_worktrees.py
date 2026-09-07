"""CliRunner e2e tests for `ao prune`'s isolation-worktree GC extension (E-Wk9Tz3 HLD
§11 M9, §14; T-Cx4Jf1 Part A AC-5/AC-6/R-6).

Outermost-boundary coverage (CLAUDE.md's e2e rule): drives `ao prune` through
`typer.testing.CliRunner`. Worktree/run-directory FIXTURES are built directly against
`isolation.worktrees.WorktreeManager` (mirroring `tests/isolation/test_worktrees.py`'s own
style) rather than through a full `ao run` -- this file's subject is `ao prune`'s own
discovery-and-GC logic (`cli._discover_run_worktree_repos`/`_gc_run_worktrees`/
`_preview_run_worktrees`), not the run path `tests/test_e2e_cli_isolation.py` already
covers end-to-end.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agent_orchestrator.cli import app
from agent_orchestrator.isolation.git import GitRepo
from agent_orchestrator.isolation.worktrees import IsolatedRepo, WorktreeManager, group_repos
from tests.test_e2e_cli_isolation import _git, _git_repo

runner = CliRunner()


def _state_dir(tmp_path: Path) -> Path:
    return tmp_path / "ao-state"


def _env(tmp_path: Path) -> dict[str, str]:
    return {"AO_STATE_DIR": str(_state_dir(tmp_path)), "HOME": str(tmp_path / "home")}


def _make_run_worktree(
    workspace_root: Path, run_id: str, repo: Path, task_id: str = "task-a"
) -> IsolatedRepo:
    """Create one real worktree for *run_id*/*task_id* against *repo* -- what a live
    isolated run leaves behind before it either succeeds (and gets released) or crashes
    (and doesn't). `AO_STATE_DIR` must already be set in the process env (via
    `monkeypatch.setenv`) before calling this, matching whatever `ao prune` will later
    read for the same workspace.
    """
    groups, skipped = group_repos({"core": str(repo)})
    assert skipped == []
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    manager = WorktreeManager(
        str(workspace_root), run_id, groups, integration_heads={groups[0].key: head}
    )
    manager.ensure(task_id, 1, [])
    return groups[0]


def _worktree_list_paths(repo: Path) -> list[str]:
    listing = _git(["worktree", "list", "--porcelain"], repo)
    return [
        line[len("worktree ") :] for line in listing.splitlines() if line.startswith("worktree ")
    ]


def _make_foreign_worktree(repo: Path, dest: Path, branch: str = "user-branch") -> Path:
    """A worktree `ao` never created or registered (D-ENS/R-6: must survive every prune
    variant untouched)."""
    _git(["worktree", "add", "-b", branch, str(dest)], repo)
    return dest


def _write_run_dir(workspace_root: Path, run_id: str, *, age_days: int = 30) -> Path:
    run_dir = workspace_root / ".orchestrator" / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "state.json").write_text("{}")
    old = time.time() - age_days * 86400
    os.utime(run_dir, (old, old))
    return run_dir


class TestPruneWorktreesOnly:
    def test_orphaned_worktree_reaped_foreign_worktree_survives_report_text(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _git_repo(tmp_path)
        monkeypatch.setenv("AO_STATE_DIR", str(_state_dir(tmp_path)))
        _make_run_worktree(tmp_path, "orphan-run", repo)

        foreign = _make_foreign_worktree(repo, tmp_path / "user-created-worktree")
        assert foreign.exists()

        # No `.orchestrator/runs/orphan-run` directory at all -- simulates a crash before
        # the run directory (or even state.json) was ever written.
        result = runner.invoke(
            app,
            ["prune", "--workspace", str(tmp_path), "--worktrees-only"],
            env=_env(tmp_path),
        )
        assert result.exit_code == 0, result.output
        assert "orphan-run" in result.output
        assert "1 orphaned run(s) reaped" in result.output

        paths = _worktree_list_paths(repo)
        assert str(foreign) in paths  # foreign worktree untouched
        assert not any("orphan-run" in p for p in paths)  # ao's own worktree is gone

        git = GitRepo(str(repo))
        remaining = git.list_refs("refs/heads/ao/orphan-run/")
        assert remaining == {}

    def test_dry_run_touches_nothing(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        repo = _git_repo(tmp_path)
        monkeypatch.setenv("AO_STATE_DIR", str(_state_dir(tmp_path)))
        _make_run_worktree(tmp_path, "orphan-run", repo)

        result = runner.invoke(
            app,
            ["prune", "--workspace", str(tmp_path), "--worktrees-only", "--dry-run"],
            env=_env(tmp_path),
        )
        assert result.exit_code == 0, result.output
        assert "would be reaped" in result.output
        assert "orphan-run" in result.output

        paths = _worktree_list_paths(repo)
        assert any("orphan-run" in p for p in paths)  # nothing removed

        git = GitRepo(str(repo))
        remaining = git.list_refs("refs/heads/ao/orphan-run/")
        assert remaining != {}  # ref still present

    def test_live_run_directory_is_not_touched(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _git_repo(tmp_path)
        monkeypatch.setenv("AO_STATE_DIR", str(_state_dir(tmp_path)))
        _make_run_worktree(tmp_path, "live-run", repo)
        _write_run_dir(tmp_path, "live-run", age_days=0)  # run directory still exists

        result = runner.invoke(
            app,
            ["prune", "--workspace", str(tmp_path), "--worktrees-only"],
            env=_env(tmp_path),
        )
        assert result.exit_code == 0, result.output
        assert "0 orphaned run(s) reaped" in result.output
        assert "live-run" not in result.output

        paths = _worktree_list_paths(repo)
        assert any("live-run" in p for p in paths)  # untouched


class TestPruneDefaultWithWorktrees:
    def test_deleted_run_directory_also_gcs_its_worktrees(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _git_repo(tmp_path)
        monkeypatch.setenv("AO_STATE_DIR", str(_state_dir(tmp_path)))
        _make_run_worktree(tmp_path, "old-run", repo)
        run_dir = _write_run_dir(tmp_path, "old-run", age_days=30)

        result = runner.invoke(
            app,
            ["prune", "--workspace", str(tmp_path), "--older-than", "7"],
            env=_env(tmp_path),
        )
        assert result.exit_code == 0, result.output
        assert not run_dir.exists()
        assert "worktrees GC'd for old-run" in result.output

        paths = _worktree_list_paths(repo)
        assert not any("old-run" in p for p in paths)

    def test_no_worktrees_flag_opts_out(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _git_repo(tmp_path)
        monkeypatch.setenv("AO_STATE_DIR", str(_state_dir(tmp_path)))
        _make_run_worktree(tmp_path, "old-run", repo)
        run_dir = _write_run_dir(tmp_path, "old-run", age_days=30)

        result = runner.invoke(
            app,
            ["prune", "--workspace", str(tmp_path), "--older-than", "7", "--no-worktrees"],
            env=_env(tmp_path),
        )
        assert result.exit_code == 0, result.output
        assert not run_dir.exists()  # run directory still deleted

        paths = _worktree_list_paths(repo)
        assert any("old-run" in p for p in paths)  # worktree survives (opted out)

    def test_dry_run_touches_neither_run_dir_nor_worktree(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _git_repo(tmp_path)
        monkeypatch.setenv("AO_STATE_DIR", str(_state_dir(tmp_path)))
        _make_run_worktree(tmp_path, "old-run", repo)
        run_dir = _write_run_dir(tmp_path, "old-run", age_days=30)

        result = runner.invoke(
            app,
            ["prune", "--workspace", str(tmp_path), "--older-than", "7", "--dry-run"],
            env=_env(tmp_path),
        )
        assert result.exit_code == 0, result.output
        assert "Would delete" in result.output
        assert "would remove worktree" in result.output
        assert run_dir.exists()

        paths = _worktree_list_paths(repo)
        assert any("old-run" in p for p in paths)


class TestPruneScopedNeverTouchesForeignWorktree:
    """R-6/AC-14: `ao prune`'s worktree GC is routed through `WorktreeManager.gc_run` /
    `GitRepo.prune_worktrees_scoped` only -- never a blanket `git worktree prune` -- so a
    hand-created worktree on the same repo survives both prune variants untouched."""

    def test_foreign_worktree_survives_prune_and_worktrees_only(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _git_repo(tmp_path)
        monkeypatch.setenv("AO_STATE_DIR", str(_state_dir(tmp_path)))
        foreign = _make_foreign_worktree(repo, tmp_path / "user-created-worktree")

        _make_run_worktree(tmp_path, "orphan-run", repo)  # no run dir -> orphan
        _make_run_worktree(tmp_path, "old-run", repo)
        run_dir = _write_run_dir(tmp_path, "old-run", age_days=30)

        result1 = runner.invoke(
            app,
            ["prune", "--workspace", str(tmp_path), "--worktrees-only"],
            env=_env(tmp_path),
        )
        assert result1.exit_code == 0, result1.output
        assert foreign.exists()
        assert str(foreign) in _worktree_list_paths(repo)

        result2 = runner.invoke(
            app,
            ["prune", "--workspace", str(tmp_path), "--older-than", "7"],
            env=_env(tmp_path),
        )
        assert result2.exit_code == 0, result2.output
        assert not run_dir.exists()
        assert foreign.exists()
        assert str(foreign) in _worktree_list_paths(repo)


class TestPruneReapsDanglingAdminEntry:
    """C-5 (2026-09-07 review): a worktree directory manually removed (not via `ao`/`git
    worktree remove`) leaves a dangling `.git/worktrees/<id>` admin entry -- `gc_run`'s
    own cleanup is driven by git's worktree REGISTRY (`git worktree list`), not by
    re-scanning the filesystem for surviving directories, so it reaps a dangling entry too
    once `ao prune` can discover the repo at all (i.e. as long as at least one sibling
    worktree directory for the same run/repo still exists to bootstrap that discovery --
    see `ao prune --help`/this file's own docstring for the residual limit when NONE do).
    """

    def test_manually_removed_worktree_dir_still_reaped_via_sibling_discovery(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import shutil

        from agent_orchestrator.isolation.paths import worktree_root

        repo = _git_repo(tmp_path)
        monkeypatch.setenv("AO_STATE_DIR", str(_state_dir(tmp_path)))
        iso_repo = _make_run_worktree(tmp_path, "orphan-run", repo, task_id="task-a")
        _make_run_worktree(tmp_path, "orphan-run", repo, task_id="task-b")

        # Simulate an operator/tooling `rm -rf` of task-b's worktree directory directly --
        # NOT `git worktree remove` -- leaving a dangling admin entry + orphaned branch
        # ref that only git's own registry (not the filesystem) still knows about.
        task_b_dir = worktree_root(str(tmp_path), "orphan-run", "task-b", iso_repo.key)
        shutil.rmtree(task_b_dir)
        listing_before = _git(["worktree", "list", "--porcelain"], repo)
        assert str(task_b_dir) in listing_before  # git still has the dangling entry

        result = runner.invoke(
            app,
            ["prune", "--workspace", str(tmp_path), "--worktrees-only"],
            env=_env(tmp_path),
        )
        assert result.exit_code == 0, result.output

        listing_after = _git(["worktree", "list", "--porcelain"], repo)
        assert str(task_b_dir) not in listing_after  # dangling entry reaped
        assert not any("orphan-run" in p for p in _worktree_list_paths(repo))

        git = GitRepo(str(repo))
        assert git.list_refs("refs/heads/ao/orphan-run/") == {}
