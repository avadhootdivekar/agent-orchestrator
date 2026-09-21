"""CliRunner e2e tests for `ao prune`'s isolation-worktree GC extension (E-Wk9Tz3 HLD
§11 M9, §14; T-Cx4Jf1 Part A AC-5/AC-6/R-6).

Outermost-boundary coverage (CLAUDE.md's e2e rule): drives `ao prune` through
`typer.testing.CliRunner`. Worktree/run-directory FIXTURES are built directly against
`isolation.worktrees.WorktreeManager` (mirroring `tests/isolation/test_worktrees.py`'s own
style) rather than through a full `ao run` -- this file's subject is `ao prune`'s own
discovery-and-GC logic (`cli._discover_run_worktree_repos`/`_plan_run_gc`/
`_execute_run_gc`), not the run path `tests/test_e2e_cli_isolation.py` already
covers end-to-end.
"""

from __future__ import annotations

import json
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
    """`$AO_STATE_DIR` for one test, deliberately OUTSIDE the workspace root (`tmp_path`).

    `isolation.paths` rejects a worktree root that lies inside the workspace root (one
    task's worktree would otherwise look like an ordinary workspace path to the artifact
    containment guard), which is also how a real install is laid out: the state dir is
    `$XDG_STATE_HOME/ao`, never a subdirectory of the project being orchestrated.
    """
    return tmp_path.parent / f"{tmp_path.name}-ao-state"


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


# ---------------------------------------------------------------------------------------
# The 2026-09-07 field defect: a run whose tasks ALL succeeded.
#
# `WorktreeManager.release(..., policy="never")` removes every worktree directory at task
# end by design (`worktree.removed`), so the normal end state of a healthy run leaves
# NOTHING under the run's worktree prefix to probe -- while its `refs/heads/ao/<run>/*` and
# `refs/ao/runs/<run>/**` refs are all still there. Reproduced on a live two-task `ao run`
# before the fix: `ao prune --older-than 0` deleted the run directory and reported success
# while leaving all four refs behind, and `--worktrees-only` then reported "0 orphaned
# run(s) reaped". The fixtures below rebuild exactly that state.
# ---------------------------------------------------------------------------------------


def _integration_state(run_id: str, repo: Path, iso_repo: IsolatedRepo, head: str) -> dict:
    """The `RunState.integration` block the engine persists at integration activation --
    the record `ao prune` now recovers a fully successful run's repos from."""
    return {
        "active": True,
        "branch": f"ao/{run_id}/integration",
        "repos": {iso_repo.key: iso_repo.common_dir},
        "heads": {iso_repo.key: head},
        "base_heads": {iso_repo.key: head},
    }


def _make_completed_run(
    workspace_root: Path,
    run_id: str,
    repo: Path,
    task_ids: tuple[str, ...] = ("alpha", "beta"),
    *,
    write_run_dir: bool = True,
    age_days: int = 30,
) -> IsolatedRepo:
    """A run that ran to completion: task branches, an integration branch, squash refs, a
    persisted `state.json` recording the repo -- and NO surviving worktree directory,
    because every task succeeded and released its worktree.
    """
    groups, skipped = group_repos({"core": str(repo)})
    assert skipped == []
    iso_repo = groups[0]
    head = _git(["rev-parse", "HEAD"], repo).strip()
    manager = WorktreeManager(
        str(workspace_root), run_id, groups, integration_heads={iso_repo.key: head}
    )
    git = GitRepo(str(repo))
    git.create_ref(f"refs/heads/ao/{run_id}/integration", head)
    for task_id in task_ids:
        manager.ensure(task_id, 1, [])
        # The integrator's squash ref, kept alive for T3 rerun/audit (`refs/ao/runs/...`).
        git.create_ref(f"refs/ao/runs/{run_id}/{task_id}/squash-1", head)
        # `keep_worktrees: "never"` + a successful integration -> directory removed.
        manager.release(task_id, "integrated", "never")

    assert not any(run_id in p for p in _worktree_list_paths(repo)), (
        "fixture must leave no worktree behind -- that is the whole point of this case"
    )

    if write_run_dir:
        run_dir = workspace_root / ".orchestrator" / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "state.json").write_text(
            json.dumps(
                {
                    "run_id": run_id,
                    "workflow_id": "wf",
                    "repo_set": "rs",
                    "started_at": "2026-09-07T00:00:00+00:00",
                    "updated_at": "2026-09-07T00:00:00+00:00",
                    "status": "succeeded",
                    "tasks": {},
                    "integration": _integration_state(run_id, repo, iso_repo, head),
                }
            )
        )
        old = time.time() - age_days * 86400
        os.utime(run_dir, (old, old))
    return iso_repo


def _ao_refs(repo: Path, run_id: str) -> dict[str, str]:
    git = GitRepo(str(repo))
    return {
        **git.list_refs(f"refs/heads/ao/{run_id}/"),
        **git.list_refs(f"refs/ao/runs/{run_id}/"),
    }


class TestPruneReapsFullySuccessfulRun:
    """Field defect (2026-09-07): every worktree directory of a successful run is already
    gone at prune time, so physical probing discovered no repo and the run's refs leaked.
    The run's own persisted `integration.repos` is what makes it reapable."""

    def test_refs_of_run_with_no_surviving_worktree_are_deleted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _git_repo(tmp_path)
        monkeypatch.setenv("AO_STATE_DIR", str(_state_dir(tmp_path)))
        _make_completed_run(tmp_path, "done-run", repo)

        before = _ao_refs(repo, "done-run")
        # alpha, beta, integration + two squash refs
        assert len(before) == 5, before

        result = runner.invoke(
            app,
            ["prune", "--workspace", str(tmp_path), "--older-than", "0"],
            env=_env(tmp_path),
        )
        assert result.exit_code == 0, result.output
        assert "worktrees GC'd for done-run" in result.output
        assert not (tmp_path / ".orchestrator" / "runs" / "done-run").exists()
        assert _ao_refs(repo, "done-run") == {}

    def test_dry_run_previews_the_refs_and_deletes_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _git_repo(tmp_path)
        monkeypatch.setenv("AO_STATE_DIR", str(_state_dir(tmp_path)))
        _make_completed_run(tmp_path, "done-run", repo)
        before = _ao_refs(repo, "done-run")

        result = runner.invoke(
            app,
            ["prune", "--workspace", str(tmp_path), "--older-than", "0", "--dry-run"],
            env=_env(tmp_path),
        )
        assert result.exit_code == 0, result.output
        assert "refs/heads/ao/done-run/alpha" in result.output
        assert "refs/heads/ao/done-run/beta" in result.output
        assert "refs/heads/ao/done-run/integration" in result.output
        assert "refs/ao/runs/done-run/alpha/squash-1" in result.output
        assert (tmp_path / ".orchestrator" / "runs" / "done-run").exists()
        assert _ao_refs(repo, "done-run") == before  # dry run touched nothing

    def test_worktrees_only_reaps_the_same_run(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`--worktrees-only` sees an orphan (run directory already deleted), so the run's
        OWN state.json is gone too -- the repos come from the other run states still in the
        workspace. This is the exact sequence from the field report: `ao prune` first, then
        `ao prune --worktrees-only`, which used to report "0 orphaned run(s) reaped"."""
        repo = _git_repo(tmp_path)
        monkeypatch.setenv("AO_STATE_DIR", str(_state_dir(tmp_path)))
        _make_completed_run(tmp_path, "orphan-done-run", repo, write_run_dir=False)
        # A second, still-present run in the same workspace: its state.json is the only
        # surviving record of the repo the orphan's refs live in.
        _make_completed_run(tmp_path, "live-run", repo, task_ids=("alpha",), age_days=0)

        assert len(_ao_refs(repo, "orphan-done-run")) == 5

        result = runner.invoke(
            app,
            ["prune", "--workspace", str(tmp_path), "--worktrees-only"],
            env=_env(tmp_path),
        )
        assert result.exit_code == 0, result.output
        assert "1 orphaned run(s) reaped" in result.output
        assert _ao_refs(repo, "orphan-done-run") == {}
        # The still-present run is not this sweep's job -- its refs are untouched.
        assert len(_ao_refs(repo, "live-run")) == 3

    def test_worktrees_only_dry_run_previews_and_deletes_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _git_repo(tmp_path)
        monkeypatch.setenv("AO_STATE_DIR", str(_state_dir(tmp_path)))
        _make_completed_run(tmp_path, "orphan-done-run", repo, write_run_dir=False)
        _make_completed_run(tmp_path, "live-run", repo, task_ids=("alpha",), age_days=0)
        before = _ao_refs(repo, "orphan-done-run")

        result = runner.invoke(
            app,
            ["prune", "--workspace", str(tmp_path), "--worktrees-only", "--dry-run"],
            env=_env(tmp_path),
        )
        assert result.exit_code == 0, result.output
        assert "1 orphaned run(s) would be reaped" in result.output
        assert "refs/heads/ao/orphan-done-run/integration" in result.output
        assert _ao_refs(repo, "orphan-done-run") == before

    def test_user_branch_and_worktree_survive_both_variants(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """R-6/AC-14 on the new discovery path: recovering repos from run state must not
        widen the blast radius -- a user's own worktree and branch on the same repo survive
        both `ao prune` and `ao prune --worktrees-only`."""
        repo = _git_repo(tmp_path)
        monkeypatch.setenv("AO_STATE_DIR", str(_state_dir(tmp_path)))
        foreign = _make_foreign_worktree(repo, tmp_path / "user-created-worktree")
        _git(["branch", "user-feature"], repo)

        _make_completed_run(tmp_path, "done-run", repo)
        _make_completed_run(tmp_path, "orphan-done-run", repo, write_run_dir=False)

        result1 = runner.invoke(
            app,
            ["prune", "--workspace", str(tmp_path), "--worktrees-only"],
            env=_env(tmp_path),
        )
        assert result1.exit_code == 0, result1.output
        assert _ao_refs(repo, "orphan-done-run") == {}

        result2 = runner.invoke(
            app,
            ["prune", "--workspace", str(tmp_path), "--older-than", "0"],
            env=_env(tmp_path),
        )
        assert result2.exit_code == 0, result2.output
        assert _ao_refs(repo, "done-run") == {}

        assert foreign.exists()
        assert str(foreign) in _worktree_list_paths(repo)
        assert GitRepo(str(repo)).branch_exists("user-feature")
        assert GitRepo(str(repo)).branch_exists("user-branch")  # the foreign worktree's
        assert GitRepo(str(repo)).branch_exists("main")


class TestPruneRepoRecordDegradesSafely:
    def test_corrupt_state_json_falls_back_to_physical_probing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An unparseable/legacy state.json must never abort the prune: the run is still
        deleted and its worktree is still reaped via physical discovery."""
        repo = _git_repo(tmp_path)
        monkeypatch.setenv("AO_STATE_DIR", str(_state_dir(tmp_path)))
        _make_run_worktree(tmp_path, "old-run", repo)
        run_dir = _write_run_dir(tmp_path, "old-run", age_days=30)
        (run_dir / "state.json").write_text("{not json at all")

        result = runner.invoke(
            app,
            ["prune", "--workspace", str(tmp_path), "--older-than", "7"],
            env=_env(tmp_path),
        )
        assert result.exit_code == 0, result.output
        assert not run_dir.exists()
        assert "worktrees GC'd for old-run" in result.output
        assert _ao_refs(repo, "old-run") == {}

    def test_recorded_repo_that_no_longer_exists_warns_and_continues(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """C-4: a recorded common dir whose checkout is gone is reported, never skipped
        silently -- `ao prune` must not claim success over an unreapable repo."""
        repo = _git_repo(tmp_path)
        monkeypatch.setenv("AO_STATE_DIR", str(_state_dir(tmp_path)))
        _make_completed_run(tmp_path, "done-run", repo)
        run_dir = tmp_path / ".orchestrator" / "runs" / "done-run"
        state = json.loads((run_dir / "state.json").read_text())
        state["integration"]["repos"] = {"core": str(tmp_path / "vanished" / ".git")}
        (run_dir / "state.json").write_text(json.dumps(state))

        result = runner.invoke(
            app,
            ["prune", "--workspace", str(tmp_path), "--older-than", "0"],
            env=_env(tmp_path),
        )
        assert result.exit_code == 0, result.output
        assert not run_dir.exists()
        assert "gone or unreadable" in result.output
        assert "worktrees GC'd" not in result.output
        assert _ao_refs(repo, "done-run") != {}  # honestly left behind, not silently lost


# ---------------------------------------------------------------------------------------
# H-4 (as-built security audit, 2026-09-07): `ao prune` was too destructive by default.
# Three guards, each lifted only by --force:
#   (a) refuse while another process holds the workspace run lock
#   (b) keep a task branch whose commits never landed on the integration head
#   (c) keep a worktree holding uncommitted/untracked changes
# ---------------------------------------------------------------------------------------


def _commit_on_branch(repo: Path, branch: str, filename: str, text: str) -> str:
    """Add a real commit to *branch* through a throwaway worktree, and return its sha.

    Used to manufacture UNLANDED work: the resulting commit is reachable only from that
    branch, so it is not an ancestor of the run's integration head.
    """
    scratch = repo.parent / f"scratch-wt-{filename}"
    _git(["worktree", "add", "-q", str(scratch), branch], repo)
    (scratch / filename).write_text(text)
    _git(["add", "-A"], scratch)
    _git(["commit", "-q", "-m", f"unlanded {filename}"], scratch)
    sha = _git(["rev-parse", "HEAD"], scratch).strip()
    _git(["worktree", "remove", "--force", str(scratch)], repo)
    return sha


class TestPruneRefusesWhileRunLockHeld:
    """H-4a: a prune concurrent with a live run would delete that run's state directory and
    force-remove its worktrees mid-task."""

    def test_held_lock_refuses_and_force_overrides(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from agent_orchestrator.isolation.runlock import WorkspaceRunLock

        repo = _git_repo(tmp_path)
        monkeypatch.setenv("AO_STATE_DIR", str(_state_dir(tmp_path)))
        _make_completed_run(tmp_path, "done-run", repo)
        run_dir = tmp_path / ".orchestrator" / "runs" / "done-run"

        lock = WorkspaceRunLock(str(tmp_path), "some-live-run")
        assert lock.acquire().granted
        try:
            refused = runner.invoke(
                app,
                ["prune", "--workspace", str(tmp_path), "--older-than", "0"],
                env=_env(tmp_path),
            )
            assert refused.exit_code == 1, refused.output
            assert "run lock" in refused.output
            assert "--force" in refused.output
            assert run_dir.exists()  # nothing deleted
            assert _ao_refs(repo, "done-run") != {}

            # The same refusal protects the worktree-only reconciliation pass.
            refused_wt = runner.invoke(
                app,
                ["prune", "--workspace", str(tmp_path), "--worktrees-only"],
                env=_env(tmp_path),
            )
            assert refused_wt.exit_code == 1, refused_wt.output

            forced = runner.invoke(
                app,
                ["prune", "--workspace", str(tmp_path), "--older-than", "0", "--force"],
                env=_env(tmp_path),
            )
            assert forced.exit_code == 0, forced.output
            assert not run_dir.exists()
            assert _ao_refs(repo, "done-run") == {}
        finally:
            lock.release()

    def test_released_lock_does_not_block(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from agent_orchestrator.isolation.runlock import WorkspaceRunLock

        repo = _git_repo(tmp_path)
        monkeypatch.setenv("AO_STATE_DIR", str(_state_dir(tmp_path)))
        _make_completed_run(tmp_path, "done-run", repo)

        lock = WorkspaceRunLock(str(tmp_path), "finished-run")
        assert lock.acquire().granted
        lock.release()

        result = runner.invoke(
            app,
            ["prune", "--workspace", str(tmp_path), "--older-than", "0"],
            env=_env(tmp_path),
        )
        assert result.exit_code == 0, result.output
        assert _ao_refs(repo, "done-run") == {}


class TestPruneKeepsUnlandedTaskBranch:
    """H-4b: `keep_worktrees: on_failure` retains a failed task's worktree precisely so an
    operator can recover the work; prune must not discard the commits."""

    def test_unlanded_branch_survives_landed_sibling_is_reaped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _git_repo(tmp_path)
        monkeypatch.setenv("AO_STATE_DIR", str(_state_dir(tmp_path)))
        _make_completed_run(tmp_path, "mixed-run", repo)
        unlanded = _commit_on_branch(repo, "ao/mixed-run/alpha", "alpha-work.txt", "recover me\n")
        run_dir = tmp_path / ".orchestrator" / "runs" / "mixed-run"

        result = runner.invoke(
            app,
            ["prune", "--workspace", str(tmp_path), "--older-than", "0"],
            env=_env(tmp_path),
        )
        assert result.exit_code == 0, result.output
        assert "never landed" in result.output
        assert "Kept:" in result.output
        assert "1 run(s) kept" in result.output

        refs = _ao_refs(repo, "mixed-run")
        # The unlanded branch and the anchors it is recovered against survive...
        assert refs.get("refs/heads/ao/mixed-run/alpha") == unlanded
        assert "refs/heads/ao/mixed-run/integration" in refs
        assert "refs/ao/runs/mixed-run/alpha/squash-1" in refs
        # ...the landed sibling is still reclaimed...
        assert "refs/heads/ao/mixed-run/beta" not in refs
        # ...and the run directory (the operator's map of that work) is kept.
        assert run_dir.exists()

    def test_force_deletes_the_unlanded_branch_and_the_run(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _git_repo(tmp_path)
        monkeypatch.setenv("AO_STATE_DIR", str(_state_dir(tmp_path)))
        _make_completed_run(tmp_path, "mixed-run", repo)
        _commit_on_branch(repo, "ao/mixed-run/alpha", "alpha-work.txt", "recover me\n")
        run_dir = tmp_path / ".orchestrator" / "runs" / "mixed-run"

        result = runner.invoke(
            app,
            ["prune", "--workspace", str(tmp_path), "--older-than", "0", "--force"],
            env=_env(tmp_path),
        )
        assert result.exit_code == 0, result.output
        assert "never landed" not in result.output
        assert _ao_refs(repo, "mixed-run") == {}
        assert not run_dir.exists()

    def test_dry_run_shows_the_kept_run_and_omits_its_branch(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _git_repo(tmp_path)
        monkeypatch.setenv("AO_STATE_DIR", str(_state_dir(tmp_path)))
        _make_completed_run(tmp_path, "mixed-run", repo)
        _commit_on_branch(repo, "ao/mixed-run/alpha", "alpha-work.txt", "recover me\n")
        before = _ao_refs(repo, "mixed-run")

        result = runner.invoke(
            app,
            ["prune", "--workspace", str(tmp_path), "--older-than", "0", "--dry-run"],
            env=_env(tmp_path),
        )
        assert result.exit_code == 0, result.output
        assert "Would keep:" in result.output
        assert "would delete ref: refs/heads/ao/mixed-run/beta" in result.output
        # The protected branch is named only by the warning, never as a would-delete line.
        assert "would delete ref: refs/heads/ao/mixed-run/alpha" not in result.output
        assert "would delete ref: refs/heads/ao/mixed-run/integration" not in result.output
        assert (tmp_path / ".orchestrator" / "runs" / "mixed-run").exists()
        assert _ao_refs(repo, "mixed-run") == before


class TestPruneKeepsDirtyWorktree:
    """H-4c: `gc_run` removes worktrees with `--force`, which discards uncommitted AND
    untracked content."""

    def test_untracked_content_is_not_silently_destroyed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from agent_orchestrator.isolation.paths import worktree_root

        repo = _git_repo(tmp_path)
        monkeypatch.setenv("AO_STATE_DIR", str(_state_dir(tmp_path)))
        iso_repo = _make_run_worktree(tmp_path, "dirty-run", repo, task_id="task-a")
        _make_run_worktree(tmp_path, "dirty-run", repo, task_id="task-b")
        wt = worktree_root(str(tmp_path), "dirty-run", "task-a", iso_repo.key)
        (wt / "scratch-notes.md").write_text("hours of un-committed analysis\n")
        run_dir = _write_run_dir(tmp_path, "dirty-run", age_days=30)

        result = runner.invoke(
            app,
            ["prune", "--workspace", str(tmp_path), "--older-than", "7"],
            env=_env(tmp_path),
        )
        assert result.exit_code == 0, result.output
        assert "uncommitted or untracked" in result.output
        assert (wt / "scratch-notes.md").read_text() == "hours of un-committed analysis\n"
        assert str(wt) in _worktree_list_paths(repo)
        assert "refs/heads/ao/dirty-run/task-a" in _ao_refs(repo, "dirty-run")
        # The clean sibling is still reclaimed, and the run directory is kept.
        assert "refs/heads/ao/dirty-run/task-b" not in _ao_refs(repo, "dirty-run")
        assert run_dir.exists()

    def test_tracked_modification_is_not_silently_destroyed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from agent_orchestrator.isolation.paths import worktree_root

        repo = _git_repo(tmp_path)
        monkeypatch.setenv("AO_STATE_DIR", str(_state_dir(tmp_path)))
        iso_repo = _make_run_worktree(tmp_path, "dirty-run", repo, task_id="task-a")
        wt = worktree_root(str(tmp_path), "dirty-run", "task-a", iso_repo.key)
        (wt / "README.md").write_text("edited but never committed\n")
        _write_run_dir(tmp_path, "dirty-run", age_days=30)

        result = runner.invoke(
            app,
            ["prune", "--workspace", str(tmp_path), "--older-than", "7"],
            env=_env(tmp_path),
        )
        assert result.exit_code == 0, result.output
        assert "uncommitted or untracked" in result.output
        assert (wt / "README.md").read_text() == "edited but never committed\n"

    def test_force_removes_the_dirty_worktree(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from agent_orchestrator.isolation.paths import worktree_root

        repo = _git_repo(tmp_path)
        monkeypatch.setenv("AO_STATE_DIR", str(_state_dir(tmp_path)))
        iso_repo = _make_run_worktree(tmp_path, "dirty-run", repo, task_id="task-a")
        wt = worktree_root(str(tmp_path), "dirty-run", "task-a", iso_repo.key)
        (wt / "scratch-notes.md").write_text("expendable\n")
        run_dir = _write_run_dir(tmp_path, "dirty-run", age_days=30)

        result = runner.invoke(
            app,
            ["prune", "--workspace", str(tmp_path), "--older-than", "7", "--force"],
            env=_env(tmp_path),
        )
        assert result.exit_code == 0, result.output
        assert "uncommitted or untracked" not in result.output
        assert not wt.exists()
        assert not run_dir.exists()
        assert _ao_refs(repo, "dirty-run") == {}
        assert not any("dirty-run" in p for p in _worktree_list_paths(repo))

    def test_worktrees_only_keeps_a_dirty_orphan(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from agent_orchestrator.isolation.paths import worktree_root

        repo = _git_repo(tmp_path)
        monkeypatch.setenv("AO_STATE_DIR", str(_state_dir(tmp_path)))
        iso_repo = _make_run_worktree(tmp_path, "orphan-run", repo, task_id="task-a")
        wt = worktree_root(str(tmp_path), "orphan-run", "task-a", iso_repo.key)
        (wt / "scratch-notes.md").write_text("unsaved work\n")

        result = runner.invoke(
            app,
            ["prune", "--workspace", str(tmp_path), "--worktrees-only"],
            env=_env(tmp_path),
        )
        assert result.exit_code == 0, result.output
        assert "uncommitted or untracked" in result.output
        assert "prune refused to destroy" in result.output
        assert (wt / "scratch-notes.md").exists()
        assert "refs/heads/ao/orphan-run/task-a" in _ao_refs(repo, "orphan-run")

        forced = runner.invoke(
            app,
            ["prune", "--workspace", str(tmp_path), "--worktrees-only", "--force"],
            env=_env(tmp_path),
        )
        assert forced.exit_code == 0, forced.output
        assert not wt.exists()
        assert _ao_refs(repo, "orphan-run") == {}
