"""Tests for `agent_orchestrator.isolation.worktrees` (E-Wk9Tz3 T-Wk3Nv6).

Real `git init` temp repos (via `conftest.py`'s `make_repo`/`make_conflict_repo`) drive the
lifecycle tests (create/reuse/crash-recovery/release/reconcile/gc). A `RecordingFakeRunner`
covers the one case that needs a scripted git failure without a real git error condition
(AC-11's "never raises" proof).
"""

from __future__ import annotations

import ast
import inspect
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from agent_orchestrator.errors import TaskIdCollisionError, WorktreeCollisionError
from agent_orchestrator.isolation import paths, worktrees
from agent_orchestrator.isolation.git import GitRepo
from agent_orchestrator.isolation.worktrees import (
    IsolatedRepo,
    RepoMember,
    TaskIsolation,
    WorktreeManager,
    group_repos,
)

from .conftest import RecordingFakeRunner, make_conflict_repo, make_repo, ok


def _raw_git(args: list[str], cwd: Path) -> str:
    """Fixture-only raw git invocation (never the thing under test) -- mirrors
    `conftest.py`'s own `_git` helper pattern for building scenarios the module under test
    did not itself create (a foreign worktree, a manually-started conflicting rebase).
    """
    result = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"git {args} failed: {result.stderr}"
    return result.stdout


def _isolated_repo_for(repo: Path, repo_id: str = "core") -> IsolatedRepo:
    groups, skipped = group_repos({repo_id: str(repo)})
    assert skipped == []
    assert len(groups) == 1
    return groups[0]


def _manager_for(
    repo: Path,
    run_id: str,
    hooks_dir: Path,
    *,
    workspace_root: str | None = None,
    isolated_repo: IsolatedRepo | None = None,
    head: str | None = None,
) -> tuple[WorktreeManager, IsolatedRepo, str]:
    iso_repo = isolated_repo or _isolated_repo_for(repo)
    resolved_head = head or _raw_git(["rev-parse", "HEAD"], repo).strip()
    manager = WorktreeManager(
        workspace_root=workspace_root or str(repo),
        run_id=run_id,
        repos=[iso_repo],
        integration_heads={iso_repo.key: resolved_head},
        hooks_dir=hooks_dir,
    )
    return manager, iso_repo, resolved_head


# ---------------------------------------------------------------------------------------
# group_repos (AC-8)
# ---------------------------------------------------------------------------------------


class TestGroupRepos:
    def test_two_repo_refs_inside_one_repo_produce_one_group(self, tmp_path: Path) -> None:
        repo = make_repo(tmp_path, files={"f.txt": "x\n"})
        (repo / "docs-md").mkdir()
        (repo / "docs-md" / "readme.md").write_text("hi\n")
        _raw_git(["add", "-A"], repo)
        _raw_git(["commit", "-q", "-m", "add docs"], repo)

        groups, skipped = group_repos({"core": str(repo), "docs": str(repo / "docs-md")})

        assert skipped == []
        assert len(groups) == 1
        group = groups[0]
        assert group.toplevel == str(repo)
        rels = {m.repo_id: m.rel for m in group.members}
        assert rels == {"core": "", "docs": "docs-md"}

    def test_non_git_path_is_skipped_and_reported(self, tmp_path: Path) -> None:
        non_git = tmp_path / "not-a-repo"
        non_git.mkdir()
        groups, skipped = group_repos({"stray": str(non_git)})
        assert groups == []
        assert skipped == ["stray"]

    def test_git_and_non_git_refs_in_one_call_do_not_disturb_each_other(
        self, tmp_path: Path
    ) -> None:
        """C-9: a non-git fallback must not disturb the grouping of the OTHER, real refs
        in the same `group_repos` call."""
        repo = make_repo(tmp_path / "repo", files={"f.txt": "x\n"})
        non_git = tmp_path / "not-a-repo"
        non_git.mkdir()

        groups, skipped = group_repos({"core": str(repo), "stray": str(non_git)})

        assert skipped == ["stray"]
        assert len(groups) == 1
        assert groups[0].members == [RepoMember(repo_id="core", rel="")]

    def test_submodule_is_its_own_group(self, tmp_path: Path) -> None:
        outer = make_repo(tmp_path / "outer", files={"o.txt": "o\n"})
        inner = make_repo(tmp_path / "inner-src", files={"i.txt": "i\n"})
        # `git submodule add` needs network config disabled for a purely-local path -- a
        # local filesystem path add works offline. This is fixture SETUP (raw git), not the
        # code under test.
        _raw_git(["-c", "protocol.file.allow=always", "submodule", "add", str(inner), "sub"], outer)
        _raw_git(["commit", "-q", "-m", "add submodule"], outer)

        groups, skipped = group_repos({"outer": str(outer), "sub": str(outer / "sub")})

        assert skipped == []
        assert len(groups) == 2
        by_id = {g.members[0].repo_id: g for g in groups}
        assert by_id["outer"].submodule is False
        assert by_id["sub"].submodule is True
        assert by_id["sub"].toplevel == str(outer / "sub")

    def test_deterministic_order_stable_across_shuffles(self, tmp_path: Path) -> None:
        import random

        repos = {f"r{i}": str(make_repo(tmp_path / f"repo-{i}")) for i in range(4)}
        keys_seen = set()
        rng = random.Random(42)
        for _ in range(10):
            items = list(repos.items())
            rng.shuffle(items)
            groups, skipped = group_repos(dict(items))
            assert skipped == []
            keys_seen.add(tuple(g.key for g in groups))
            assert [g.key for g in groups] == sorted(g.key for g in groups)
        assert len(keys_seen) == 1  # identical ordered output regardless of input order


# ---------------------------------------------------------------------------------------
# R-6: no blanket prune, ever (AC-16)
# ---------------------------------------------------------------------------------------


class TestNoGlobalPrune:
    def test_module_source_never_calls_a_blanket_worktree_prune(self) -> None:
        source = inspect.getsource(worktrees)
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.List):
                values = [
                    elt.value
                    for elt in node.elts
                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
                ]
                assert values != ["worktree", "prune"], (
                    "worktrees.py must never construct a blanket ['worktree', 'prune'] argv "
                    "-- every prune site must go through GitRepo.prune_worktrees_scoped (R-6)"
                )
        # Non-vacuous: scoped pruning must actually be used somewhere in the module.
        assert source.count("prune_worktrees_scoped") >= 3  # ensure, reconcile, gc_run

    def test_foreign_worktree_survives_ensure_reconcile_and_gc_run(self, tmp_path: Path) -> None:
        repo = make_repo(tmp_path / "repo")
        foreign_path = tmp_path / "user-created-worktree"
        _raw_git(["worktree", "add", "-b", "user-branch", str(foreign_path)], repo)
        shutil.rmtree(foreign_path)  # unreachable, but still registered -> prunable

        def _foreign_survives() -> None:
            listing = _raw_git(["worktree", "list", "--porcelain"], repo)
            assert str(foreign_path) in listing

        _foreign_survives()
        manager, _repo, _head = _manager_for(repo, "run-1", tmp_path / "hooks")
        manager.ensure("task-a", 1, [])
        _foreign_survives()
        manager.reconcile({"task-a"})
        _foreign_survives()
        manager.gc_run("run-1")
        _foreign_survives()


# ---------------------------------------------------------------------------------------
# ensure() -- create, reuse, crash recovery (AC-9, AC-10)
# ---------------------------------------------------------------------------------------


class TestEnsureCreate:
    def test_creates_worktree_on_task_branch_from_base_commit(self, tmp_path: Path) -> None:
        repo = make_repo(tmp_path / "repo")
        manager, iso_repo, head = _manager_for(repo, "run-1", tmp_path / "hooks")

        iso = manager.ensure("task-a", 1, ["out/x.txt"])

        assert len(iso.repos) == 1
        repo_iso = iso.repos[0]
        assert repo_iso.branch == paths.task_branch("run-1", "task-a")
        assert repo_iso.base == head
        wt_path = Path(repo_iso.worktree_root)
        assert wt_path.is_dir()
        assert (wt_path / "f.txt").exists()  # checked out from `head`
        current_branch = _raw_git(["branch", "--show-current"], wt_path).strip()
        assert current_branch == paths.task_branch("run-1", "task-a")

    def test_ensure_reuses_on_second_call(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        repo = make_repo(tmp_path / "repo")
        manager, iso_repo, head = _manager_for(repo, "run-1", tmp_path / "hooks")

        first = manager.ensure("task-a", 1, [])
        wt_path = Path(first.repos[0].worktree_root)
        inode_before = wt_path.stat().st_ino
        mtime_before = wt_path.stat().st_mtime

        caplog.clear()
        with caplog.at_level("INFO"):
            second = manager.ensure("task-a", 2, [])

        wt_path_after = Path(second.repos[0].worktree_root)
        assert wt_path_after == wt_path
        assert wt_path_after.stat().st_ino == inode_before
        assert wt_path_after.stat().st_mtime == mtime_before
        assert any("worktree.reused" in r.message for r in caplog.records)

    def test_declared_outputs_and_cycle_round_trip(self, tmp_path: Path) -> None:
        repo = make_repo(tmp_path / "repo")
        manager, _iso_repo, _head = _manager_for(repo, "run-1", tmp_path / "hooks")
        iso = manager.ensure("task-a", 7, ["out/a.txt", "out/b.txt"])
        assert iso.cycle == 7
        assert iso.declared_outputs == ["out/a.txt", "out/b.txt"]


class TestEnsureCrashRecovery:
    def test_stale_unregistered_directory_is_removed_and_recreated(self, tmp_path: Path) -> None:
        """AC-10a."""
        repo = make_repo(tmp_path / "repo")
        manager, iso_repo, head = _manager_for(repo, "run-1", tmp_path / "hooks")
        wt_path = paths.worktree_root(manager.workspace_root, "run-1", "task-a", iso_repo.key)
        wt_path.mkdir(parents=True)
        (wt_path / "stale-marker.txt").write_text("not a real worktree")

        iso = manager.ensure("task-a", 1, [])

        recreated_path = Path(iso.repos[0].worktree_root)
        assert recreated_path == wt_path
        assert not (recreated_path / "stale-marker.txt").exists()
        assert (recreated_path / "f.txt").exists()

    def test_registered_worktree_with_deleted_directory_is_pruned_and_recreated(
        self, tmp_path: Path
    ) -> None:
        """AC-10b."""
        repo = make_repo(tmp_path / "repo")
        manager, iso_repo, head = _manager_for(repo, "run-1", tmp_path / "hooks")
        iso = manager.ensure("task-a", 1, [])
        wt_path = Path(iso.repos[0].worktree_root)
        shutil.rmtree(wt_path)  # simulate an external `rm -rf` bypassing ao

        iso2 = manager.ensure("task-a", 2, [])

        recreated_path = Path(iso2.repos[0].worktree_root)
        assert recreated_path == wt_path
        assert recreated_path.is_dir()
        assert (recreated_path / "f.txt").exists()

    def test_mid_rebase_worktree_is_aborted_and_reused(self, tmp_path: Path) -> None:
        """AC-10c."""
        repo, base_sha, ours, theirs = make_conflict_repo(tmp_path, "true_conflict")
        ours_head = _raw_git(["rev-parse", ours], repo).strip()
        isolated_repo = _isolated_repo_for(repo)
        manager = WorktreeManager(
            workspace_root=str(repo),
            run_id="run-1",
            repos=[isolated_repo],
            integration_heads={isolated_repo.key: ours_head},
            hooks_dir=tmp_path / "hooks",
        )
        iso = manager.ensure("task-a", 1, [])
        wt_path = Path(iso.repos[0].worktree_root)
        task_branch = iso.repos[0].branch

        git = GitRepo(str(repo), hooks_dir=tmp_path / "hooks")
        outcome = git.rebase_onto(str(wt_path), theirs, base_sha, task_branch)
        assert outcome.clean is False
        assert git.rebase_in_progress(str(wt_path)) is True

        manager.ensure("task-a", 2, [])

        assert git.rebase_in_progress(str(wt_path)) is False
        # The worktree is usable again: an ordinary git command succeeds.
        status = _raw_git(["status", "--porcelain"], wt_path)
        assert status is not None


class TestEnsureDEns:
    """D-ENS (HLD §11 M3, review 2026-09-07 -- resolves deviation 4 / C-6): `ensure()`
    never deletes a user-visible ref. Three states, each its own test.
    """

    def test_case_a_reuse_when_registered_worktree_matches_expected_branch(
        self, tmp_path: Path
    ) -> None:
        repo = make_repo(tmp_path / "repo")
        manager, iso_repo, head = _manager_for(repo, "run-1", tmp_path / "hooks")
        first = manager.ensure("task-a", 1, [])
        wt_path = Path(first.repos[0].worktree_root)
        inode_before = wt_path.stat().st_ino

        second = manager.ensure("task-a", 2, [])

        assert Path(second.repos[0].worktree_root).stat().st_ino == inode_before
        assert second.repos[0].branch == first.repos[0].branch

    def test_case_b_reattach_to_leftover_branch_preserves_its_commit(self, tmp_path: Path) -> None:
        """A crashed run's worktree directory is gone (its git registration cleaned up by
        the scoped prune, exactly like AC-10b) but its branch survives with a commit the
        task made before the crash. `ensure()` must RE-ATTACH (never delete the branch and
        recreate with `-b`), so that commit is still reachable afterward.
        """
        repo = make_repo(tmp_path / "repo")
        manager, iso_repo, head = _manager_for(repo, "run-1", tmp_path / "hooks")
        first = manager.ensure("task-a", 1, [])
        wt_path = Path(first.repos[0].worktree_root)
        branch = first.repos[0].branch

        # Simulate the task's own in-progress work before the crash.
        (wt_path / "leftover.txt").write_text("in-progress work\n")
        _raw_git(["add", "-A"], wt_path)
        _raw_git(["commit", "-q", "-m", "leftover work"], wt_path)
        leftover_sha = _raw_git(["rev-parse", "HEAD"], wt_path).strip()

        # Simulate the crash: the worktree checkout is gone, but its branch is not touched.
        git = GitRepo(str(repo), hooks_dir=tmp_path / "hooks")
        removed = git.worktree_remove(str(wt_path), force=True)
        assert removed == "removed"
        assert git.branch_exists(branch) is True

        second = manager.ensure("task-a", 2, [])

        recreated_path = Path(second.repos[0].worktree_root)
        assert recreated_path.is_dir()
        current_sha = _raw_git(["rev-parse", "HEAD"], recreated_path).strip()
        assert current_sha == leftover_sha
        assert (recreated_path / "leftover.txt").exists()

    def test_case_b_reattach_logs_branch_reattached(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        repo = make_repo(tmp_path / "repo")
        manager, iso_repo, head = _manager_for(repo, "run-1", tmp_path / "hooks")
        first = manager.ensure("task-a", 1, [])
        wt_path = Path(first.repos[0].worktree_root)
        branch = first.repos[0].branch
        git = GitRepo(str(repo), hooks_dir=tmp_path / "hooks")
        git.worktree_remove(str(wt_path), force=True)
        assert git.branch_exists(branch) is True

        with caplog.at_level("INFO"):
            manager.ensure("task-a", 2, [])

        assert any("worktree.branch_reattached" in r.message for r in caplog.records)

    def test_case_c_branch_held_by_another_worktree_is_a_hard_error(self, tmp_path: Path) -> None:
        """The branch `ensure()` needs is checked out at a DIFFERENT worktree path
        entirely. Must hard-error, naming the branch/worktree/remedy, and must NEVER
        delete or move any ref -- verified by comparing `rev-parse` before and after.
        """
        repo = make_repo(tmp_path / "repo")
        manager, iso_repo, head = _manager_for(repo, "run-1", tmp_path / "hooks")
        expected_branch = paths.task_branch("run-1", "task-a")
        other_worktree = tmp_path / "manually-placed-worktree"
        _raw_git(["worktree", "add", "-q", "-b", expected_branch, str(other_worktree)], repo)
        sha_before = _raw_git(["rev-parse", expected_branch], repo).strip()

        with pytest.raises(WorktreeCollisionError) as exc_info:
            manager.ensure("task-a", 1, [])

        err = exc_info.value
        assert err.expected_branch == f"refs/heads/{expected_branch}"
        assert err.worktree_path == str(other_worktree)
        assert "ao prune --worktrees-only" in err.remedy or "worktree remove" in str(err)
        assert expected_branch in str(err)
        assert str(other_worktree) in str(err)

        sha_after = _raw_git(["rev-parse", expected_branch], repo).strip()
        assert sha_after == sha_before
        listing_after = _raw_git(["worktree", "list", "--porcelain"], repo)
        assert str(other_worktree) in listing_after  # untouched, not removed

    def test_case_2_different_worktree_at_our_own_expected_path_is_a_hard_error(
        self, tmp_path: Path
    ) -> None:
        """C-1/C-6: a worktree already sits at OUR expected path but on a different
        branch -- never silently rmtree+recreate; hard-error instead.
        """
        repo = make_repo(tmp_path / "repo")
        manager, iso_repo, head = _manager_for(repo, "run-1", tmp_path / "hooks")
        expected_path = paths.worktree_root(manager.workspace_root, "run-1", "task-a", iso_repo.key)
        _raw_git(["worktree", "add", "-q", "-b", "some-other-branch", str(expected_path)], repo)
        sha_before = _raw_git(["rev-parse", "some-other-branch"], repo).strip()

        with pytest.raises(WorktreeCollisionError) as exc_info:
            manager.ensure("task-a", 1, [])

        err = exc_info.value
        assert err.found_branch == "refs/heads/some-other-branch"
        assert err.worktree_path == str(expected_path)

        sha_after = _raw_git(["rev-parse", "some-other-branch"], repo).strip()
        assert sha_after == sha_before
        assert expected_path.is_dir()  # untouched, not rmtree'd


# ---------------------------------------------------------------------------------------
# release() (AC-11)
# ---------------------------------------------------------------------------------------


class TestRelease:
    def test_removes_worktree_on_failure_when_policy_is_never(self, tmp_path: Path) -> None:
        repo = make_repo(tmp_path / "repo")
        manager, iso_repo, head = _manager_for(repo, "run-1", tmp_path / "hooks")
        iso = manager.ensure("task-a", 1, [])
        wt_path = Path(iso.repos[0].worktree_root)
        assert wt_path.exists()

        manager.release("task-a", "failed", "never")

        assert not wt_path.exists()

    def test_keeps_worktree_on_failure_when_policy_is_on_failure_and_outcome_failed(
        self, tmp_path: Path
    ) -> None:
        repo = make_repo(tmp_path / "repo")
        manager, iso_repo, head = _manager_for(repo, "run-1", tmp_path / "hooks")
        iso = manager.ensure("task-a", 1, [])
        wt_path = Path(iso.repos[0].worktree_root)

        manager.release("task-a", "failed", "on_failure")

        assert wt_path.exists()

    def test_keeps_worktree_when_policy_is_always_even_on_success(self, tmp_path: Path) -> None:
        repo = make_repo(tmp_path / "repo")
        manager, iso_repo, head = _manager_for(repo, "run-1", tmp_path / "hooks")
        iso = manager.ensure("task-a", 1, [])
        wt_path = Path(iso.repos[0].worktree_root)

        manager.release("task-a", "integrated", "always")

        assert wt_path.exists()

    def test_removes_worktree_on_success_when_policy_is_on_failure(self, tmp_path: Path) -> None:
        repo = make_repo(tmp_path / "repo")
        manager, iso_repo, head = _manager_for(repo, "run-1", tmp_path / "hooks")
        iso = manager.ensure("task-a", 1, [])
        wt_path = Path(iso.repos[0].worktree_root)

        manager.release("task-a", "integrated", "on_failure")

        assert not wt_path.exists()

    def test_locked_outcome_logs_remove_skipped_not_removed(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """C-5: a non-removal outcome (`locked`/`in_use`) must never log under the
        `worktree.removed` event name -- a reader grepping/alerting on that exact name
        would otherwise mis-count it as a successful removal."""
        workspace_root = str(tmp_path / "ws")
        run_id = "run-1"
        task_id = "task-a"
        common_dir = str(tmp_path / "repo" / ".git")
        isolated_repo = IsolatedRepo(
            key="core-abcd1234",
            toplevel=str(tmp_path / "repo"),
            common_dir=common_dir,
            members=[RepoMember(repo_id="core", rel="")],
        )
        expected_path = paths.worktree_root(workspace_root, run_id, task_id, isolated_repo.key)
        porcelain_locked = (
            f"worktree {expected_path}\n"
            f"HEAD {'a' * 40}\n"
            f"branch refs/heads/ao/{run_id}/{task_id}\n"
            "locked\n"
            "\n"
        ).encode()
        runner = RecordingFakeRunner(
            responses=[
                ok(stdout=(common_dir + "\n").encode()),
                ok(stdout=porcelain_locked),
            ]
        )
        manager = WorktreeManager(
            workspace_root=workspace_root,
            run_id=run_id,
            repos=[isolated_repo],
            integration_heads={isolated_repo.key: "a" * 40},
            runner=runner,
            hooks_dir=tmp_path / "hooks",
        )

        with caplog.at_level("WARNING"):
            manager.release(task_id, "failed", "never")

        assert not any(r.message == "worktree.removed" for r in caplog.records)
        assert any(r.message == "worktree.remove_skipped" for r in caplog.records)

    def test_worktree_remove_git_error_is_caught_logged_and_never_propagates(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """AC-11: a GitError-raising stub proves release() never raises."""
        workspace_root = str(tmp_path / "ws")
        run_id = "run-1"
        task_id = "task-a"
        common_dir = str(tmp_path / "repo" / ".git")
        isolated_repo = IsolatedRepo(
            key="core-abcd1234",
            toplevel=str(tmp_path / "repo"),
            common_dir=common_dir,
            members=[RepoMember(repo_id="core", rel="")],
        )
        expected_path = paths.worktree_root(workspace_root, run_id, task_id, isolated_repo.key)
        porcelain = (
            f"worktree {expected_path}\n"
            f"HEAD {'a' * 40}\n"
            f"branch refs/heads/ao/{run_id}/{task_id}\n"
            "\n"
        ).encode()
        runner = RecordingFakeRunner(
            responses=[
                ok(stdout=(common_dir + "\n").encode()),  # rev-parse --git-common-dir
                ok(stdout=porcelain),  # worktree list --porcelain
                ok(stdout=b"", stderr=b"fatal: unexpected boom", returncode=128),  # remove
            ]
        )
        manager = WorktreeManager(
            workspace_root=workspace_root,
            run_id=run_id,
            repos=[isolated_repo],
            integration_heads={isolated_repo.key: "a" * 40},
            runner=runner,
            hooks_dir=tmp_path / "hooks",
        )

        with caplog.at_level("WARNING"):
            manager.release(task_id, "failed", "never")  # must not raise

        assert any("worktree.remove_failed" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------------------
# reconcile() (AC-12)
# ---------------------------------------------------------------------------------------


class TestReconcile:
    def test_noop_on_clean_state(self, tmp_path: Path) -> None:
        repo = make_repo(tmp_path / "repo")
        manager, iso_repo, head = _manager_for(repo, "run-1", tmp_path / "hooks")
        manager.ensure("task-a", 1, [])

        report1 = manager.reconcile({"task-a"})
        report2 = manager.reconcile({"task-a"})

        assert report1.removed_worktrees == []
        assert report1.deleted_refs == []
        assert report2.removed_worktrees == []
        assert report2.deleted_refs == []

    def test_reaps_worktree_and_branch_of_unknown_task(self, tmp_path: Path) -> None:
        repo = make_repo(tmp_path / "repo")
        manager, iso_repo, head = _manager_for(repo, "run-1", tmp_path / "hooks")
        iso = manager.ensure("task-a", 1, [])
        wt_path = Path(iso.repos[0].worktree_root)
        branch = iso.repos[0].branch

        report = manager.reconcile(set())  # task-a is no longer known

        assert not wt_path.exists()
        assert str(wt_path) in report.removed_worktrees
        git = GitRepo(str(repo), hooks_dir=tmp_path / "hooks")
        assert git.branch_exists(branch) is False
        assert f"refs/heads/{branch}" in report.deleted_refs

    def test_idempotent_after_reaping(self, tmp_path: Path) -> None:
        repo = make_repo(tmp_path / "repo")
        manager, iso_repo, head = _manager_for(repo, "run-1", tmp_path / "hooks")
        manager.ensure("task-a", 1, [])

        manager.reconcile(set())
        report2 = manager.reconcile(set())

        assert report2.removed_worktrees == []
        assert report2.deleted_refs == []

    def test_integration_branch_is_never_reaped(self, tmp_path: Path) -> None:
        """`ao/<run>/integration` is run-scoped, not task-scoped (RESERVED_BRANCH_COMPONENTS)
        -- reconcile must never treat it as an orphaned task branch, even when the known
        task-id set is empty.
        """
        repo = make_repo(tmp_path / "repo")
        manager, iso_repo, head = _manager_for(repo, "run-1", tmp_path / "hooks")
        git = GitRepo(str(repo), hooks_dir=tmp_path / "hooks")
        integration_branch = paths.integration_branch("run-1")
        git.create_ref(f"refs/heads/{integration_branch}", head)

        report = manager.reconcile(set())

        assert git.branch_exists(integration_branch) is True
        assert f"refs/heads/{integration_branch}" not in report.deleted_refs

    def test_worktree_remove_git_error_during_reconcile_is_caught_and_logged(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Mirrors AC-11's release() proof: an orphan removal failure inside reconcile() is
        caught and logged, never propagated, and the run keeps going.
        """
        workspace_root = str(tmp_path / "ws")
        run_id = "run-1"
        common_dir = str(tmp_path / "repo" / ".git")
        isolated_repo = IsolatedRepo(
            key="core-abcd1234",
            toplevel=str(tmp_path / "repo"),
            common_dir=common_dir,
            members=[RepoMember(repo_id="core", rel="")],
        )
        orphan_path = paths.worktree_root(workspace_root, run_id, "orphan-task", isolated_repo.key)
        porcelain = (
            f"worktree {orphan_path}\n"
            f"HEAD {'a' * 40}\n"
            f"branch refs/heads/ao/{run_id}/orphan-task\n"
            "\n"
        ).encode()
        common_dir_resp = ok(stdout=(common_dir + "\n").encode())
        porcelain_resp = ok(stdout=porcelain)
        runner = RecordingFakeRunner(
            responses=[
                common_dir_resp,  # 1: prune_worktrees_scoped's own worktree_list -> rev-parse
                porcelain_resp,  # 2: ...                              -> worktree list --porcelain
                ok(stdout=b""),  # 3: worktree prune (no foreign prunable -> safe global prune)
                common_dir_resp,  # 4: reconcile's own worktree_list -> rev-parse
                porcelain_resp,  # 5: ...                             -> worktree list --porcelain
                common_dir_resp,  # 6: worktree_remove's internal worktree_list -> rev-parse
                porcelain_resp,  # 7: ...                                        -> list --porcelain
                ok(stdout=b"", stderr=b"fatal: boom", returncode=128),  # 8: worktree remove fails
                # 9: list_refs (refs/heads/ao/run-1/) -- returns the orphan's OWN branch ref,
                # so C-8's guard (skip deletion when the branch is in `remaining_branches`)
                # is actually exercised, not vacuously passed via an empty list.
                ok(stdout=f"refs/heads/ao/{run_id}/orphan-task {'a' * 40}\n".encode()),
            ]
        )
        manager = WorktreeManager(
            workspace_root=workspace_root,
            run_id=run_id,
            repos=[isolated_repo],
            integration_heads={isolated_repo.key: "a" * 40},
            runner=runner,
            hooks_dir=tmp_path / "hooks",
        )

        with caplog.at_level("WARNING"):
            report = manager.reconcile(set())  # must not raise

        assert report.removed_worktrees == []
        assert any("worktree.remove_failed" in r.message for r in caplog.records)
        # C-8: the branch ref survives because its worktree removal just failed above.
        assert report.deleted_refs == []


# ---------------------------------------------------------------------------------------
# C-4 (security review, 2026-09-07): reconcile() must compare SANITIZED components against
# SANITIZED components. Comparing an on-disk directory/ref component against the RAW known
# task ids judged every non-sanitize-identity id unknown and destroyed its live work.
# ---------------------------------------------------------------------------------------


class TestReconcileWithIdsThatNeedSanitizing:
    # Ids that are NOT sanitize-identity: each holds a character outside `[A-Za-z0-9._-]`.
    _NEEDS_SANITIZING = ["build:web", "svc/api", "task a", "feat(x)"]

    @pytest.mark.parametrize("task_id", _NEEDS_SANITIZING)
    def test_live_declared_task_keeps_its_worktree_branch_and_uncommitted_work(
        self, tmp_path: Path, task_id: str
    ) -> None:
        """The auditor's exact attack shape (`exp10_reconcile.py`): two declared, live tasks
        -- one whose id needs sanitizing, one that does not -- both holding uncommitted work,
        and `reconcile()` called with BOTH raw ids known and current. Before the fix the
        sanitizing one lost its worktree (`worktree_remove(force=True)` discards uncommitted
        work), its branch, and therefore the only pointer to that work; on the resume path
        that happened before the task ran.
        """
        repo = make_repo(tmp_path / "repo")
        manager, iso_repo, head = _manager_for(repo, "run-1", tmp_path / "hooks")
        known = {task_id, "plain-task"}

        worktrees_by_id = {}
        for tid in sorted(known):
            iso = manager.ensure(tid, 1, [])
            wt = Path(iso.repos[0].worktree_root)
            (wt / "WORK_IN_PROGRESS.txt").write_text(f"unmerged work of {tid}\n")
            worktrees_by_id[tid] = (wt, iso.repos[0].branch)

        report = manager.reconcile(known)

        assert report.removed_worktrees == []
        assert report.deleted_refs == []
        git = GitRepo(str(repo), hooks_dir=tmp_path / "hooks")
        for tid, (wt, branch) in worktrees_by_id.items():
            assert wt.exists(), f"{tid!r} lost its worktree"
            assert (wt / "WORK_IN_PROGRESS.txt").read_text() == f"unmerged work of {tid}\n"
            assert git.branch_exists(branch) is True, f"{tid!r} lost its branch"

    @pytest.mark.parametrize("task_id", _NEEDS_SANITIZING)
    def test_genuinely_unknown_task_with_a_sanitizing_id_is_still_reaped(
        self, tmp_path: Path, task_id: str
    ) -> None:
        """The control that keeps the C-4 fix from being a weakening: an id needing
        sanitization that is genuinely NOT known must still lose its worktree and branch.
        """
        repo = make_repo(tmp_path / "repo")
        manager, iso_repo, head = _manager_for(repo, "run-1", tmp_path / "hooks")
        iso = manager.ensure(task_id, 1, [])
        wt = Path(iso.repos[0].worktree_root)
        branch = iso.repos[0].branch

        report = manager.reconcile({"some-other-task"})

        assert not wt.exists()
        assert str(wt) in report.removed_worktrees
        assert f"refs/heads/{branch}" in report.deleted_refs
        git = GitRepo(str(repo), hooks_dir=tmp_path / "hooks")
        assert git.branch_exists(branch) is False

    def test_a_raw_id_that_merely_looks_like_another_ids_component_does_not_shield_it(
        self, tmp_path: Path
    ) -> None:
        """`known_task_ids` is sanitized before comparison, so knowing the RAW id
        `build-web` legitimately covers a leftover `build:web` worktree -- the two are
        genuinely indistinguishable on disk. This pins that consequence explicitly rather
        than leaving it implicit, and V13/`TaskIdCollisionError` are what stop both ids from
        ever being live in one run.
        """
        repo = make_repo(tmp_path / "repo")
        manager, iso_repo, head = _manager_for(repo, "run-1", tmp_path / "hooks")
        iso = manager.ensure("build:web", 1, [])
        wt = Path(iso.repos[0].worktree_root)

        report = manager.reconcile({"build-web"})

        assert wt.exists()
        assert report.removed_worktrees == []
        assert report.deleted_refs == []


# ---------------------------------------------------------------------------------------
# M-2 (security review, 2026-09-07): two distinct ids that sanitize to one component are
# refused at ensure() time, rather than silently sharing a worktree via the reuse path.
# ---------------------------------------------------------------------------------------


class TestTaskIdCollisionAtEnsure:
    @pytest.mark.parametrize(
        ("id_a", "id_b"),
        [("svc/api", "svc-api"), ("../../../../etc", "etc"), ("$(id)", "id"), ("a b", "a-b")],
    )
    def test_second_colliding_id_is_refused_naming_both_ids(
        self, tmp_path: Path, id_a: str, id_b: str
    ) -> None:
        """The auditor's M-2 shape: before the fix the second `ensure()` found a registered
        worktree already on the expected branch and took the *reuse* path, returning the
        IDENTICAL worktree_root and branch -- two supposedly isolated tasks in one checkout.
        """
        repo = make_repo(tmp_path / "repo")
        manager, iso_repo, head = _manager_for(repo, "run-1", tmp_path / "hooks")
        first = manager.ensure(id_a, 1, [])

        with pytest.raises(TaskIdCollisionError) as excinfo:
            manager.ensure(id_b, 1, [])

        exc = excinfo.value
        assert exc.task_id == id_b
        assert exc.other_task_id == id_a
        message = str(exc)
        assert repr(id_a) in message and repr(id_b) in message
        # And the first task's worktree is untouched -- the refusal costs it nothing.
        assert Path(first.repos[0].worktree_root).exists()

    def test_collision_error_is_a_worktree_collision_error(self, tmp_path: Path) -> None:
        """Subclassing is load-bearing: the engine's dispatch path catches
        `WorktreeCollisionError` and fails just that task. A brand-new unrelated exception
        type would escape it and crash the whole run.
        """
        assert issubclass(TaskIdCollisionError, WorktreeCollisionError)
        repo = make_repo(tmp_path / "repo")
        manager, iso_repo, head = _manager_for(repo, "run-1", tmp_path / "hooks")
        manager.ensure("svc/api", 1, [])
        with pytest.raises(WorktreeCollisionError):
            manager.ensure("svc-api", 1, [])

    def test_re_ensuring_the_same_id_is_not_a_collision(self, tmp_path: Path) -> None:
        """`ensure()` is called once per dispatch cycle (retry, rerun-on-fresh-base), so
        re-claiming one's own component must stay the ordinary reuse path.
        """
        repo = make_repo(tmp_path / "repo")
        manager, iso_repo, head = _manager_for(repo, "run-1", tmp_path / "hooks")
        first = manager.ensure("svc/api", 1, [])
        second = manager.ensure("svc/api", 2, [])
        assert second.repos[0].worktree_root == first.repos[0].worktree_root
        assert second.repos[0].branch == first.repos[0].branch

    def test_distinct_components_do_not_collide(self, tmp_path: Path) -> None:
        repo = make_repo(tmp_path / "repo")
        manager, iso_repo, head = _manager_for(repo, "run-1", tmp_path / "hooks")
        a = manager.ensure("svc/api", 1, [])
        b = manager.ensure("svc/web", 1, [])
        assert a.repos[0].worktree_root != b.repos[0].worktree_root
        assert a.repos[0].branch != b.repos[0].branch

    def test_collision_is_detected_before_any_git_call(self, tmp_path: Path) -> None:
        """The claim is checked at the top of `ensure()`, so the colliding task never
        touches the first task's worktree -- not even a prune or a `worktree list`.
        """
        repo = make_repo(tmp_path / "repo")
        manager, iso_repo, head = _manager_for(repo, "run-1", tmp_path / "hooks")
        manager.ensure("svc/api", 1, [])
        runner = RecordingFakeRunner([])  # any git call would raise IndexError/StopIteration
        manager._runner = runner
        manager._git_repos.clear()
        with pytest.raises(TaskIdCollisionError):
            manager.ensure("svc-api", 1, [])
        assert runner.calls == []


# ---------------------------------------------------------------------------------------
# gc_run() (AC-13)
# ---------------------------------------------------------------------------------------


class TestGcRun:
    def test_removes_all_worktrees_and_refs_for_the_run(self, tmp_path: Path) -> None:
        repo = make_repo(tmp_path / "repo")
        manager, iso_repo, head = _manager_for(repo, "run-1", tmp_path / "hooks")
        manager.ensure("task-a", 1, [])
        manager.ensure("task-b", 1, [])

        manager.gc_run("run-1")

        git = GitRepo(str(repo), hooks_dir=tmp_path / "hooks")
        remaining = git.list_refs(f"refs/heads/{paths.AO_REF_NAMESPACE}/run-1/")
        assert remaining == {}
        remaining_run_refs = git.list_refs(f"refs/{paths.AO_REF_NAMESPACE}/runs/run-1/")
        assert remaining_run_refs == {}
        listing = _raw_git(["worktree", "list", "--porcelain"], repo)
        # Only the main worktree ("worktree <repo>") remains.
        worktree_lines = [line for line in listing.splitlines() if line.startswith("worktree ")]
        assert worktree_lines == [f"worktree {repo}"]

    def test_removes_squash_refs(self, tmp_path: Path) -> None:
        repo = make_repo(tmp_path / "repo")
        manager, iso_repo, head = _manager_for(repo, "run-1", tmp_path / "hooks")
        git = GitRepo(str(repo), hooks_dir=tmp_path / "hooks")
        squash_ref = paths.squash_ref("run-1", "task-a", 1)
        git.create_ref(squash_ref, head)
        assert git.list_refs(f"refs/{paths.AO_REF_NAMESPACE}/runs/run-1/") != {}

        manager.gc_run("run-1")

        assert git.list_refs(f"refs/{paths.AO_REF_NAMESPACE}/runs/run-1/") == {}

    def test_locked_worktree_keeps_its_branch_ref(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """C-2: `worktree_remove` returning `"locked"` (never raising) must be honoured
        exactly like `reconcile()`'s `remaining_branches` guard -- the branch ref survives,
        reported, never silently orphaned. Real git has no simple portable way to leave a
        *locked* worktree in a state `--force` still refuses (newer git needs `--force
        --force` to override a lock), so this is scripted with a `RecordingFakeRunner`
        exactly like `TestRelease`'s and `TestReconcile`'s GitError proofs.
        """
        workspace_root = str(tmp_path / "ws")
        run_id = "run-1"
        task_id = "orphan-task"
        common_dir = str(tmp_path / "repo" / ".git")
        isolated_repo = IsolatedRepo(
            key="core-abcd1234",
            toplevel=str(tmp_path / "repo"),
            common_dir=common_dir,
            members=[RepoMember(repo_id="core", rel="")],
        )
        expected_path = paths.worktree_root(workspace_root, run_id, task_id, isolated_repo.key)
        branch = paths.task_branch(run_id, task_id)
        expected_ref = f"refs/heads/{branch}"
        porcelain_locked = (
            f"worktree {expected_path}\nHEAD {'a' * 40}\nbranch {expected_ref}\nlocked\n\n"
        ).encode()
        common_dir_resp = ok(stdout=(common_dir + "\n").encode())
        locked_resp = ok(stdout=porcelain_locked)
        runner = RecordingFakeRunner(
            responses=[
                common_dir_resp,  # 1: gc_run's own worktree_list -> rev-parse
                locked_resp,  # 2: ...                             -> list --porcelain
                common_dir_resp,  # 3: worktree_remove's internal worktree_list -> rev-parse
                locked_resp,  # 4: ... -> list --porcelain (entry.locked -> "locked", no 3rd call)
                common_dir_resp,  # 5: prune_worktrees_scoped's worktree_list -> rev-parse
                locked_resp,  # 6: ...                                        -> list --porcelain
                ok(stdout=b""),  # 7: worktree prune (no foreign prunable)
                ok(stdout=f"{expected_ref} {'a' * 40}\n".encode()),  # 8: list_refs(ref_prefix)
                ok(stdout=b""),  # 9: list_refs(runs_prefix) -- empty
            ]
        )
        manager = WorktreeManager(
            workspace_root=workspace_root,
            run_id=run_id,
            repos=[isolated_repo],
            integration_heads={isolated_repo.key: "a" * 40},
            runner=runner,
            hooks_dir=tmp_path / "hooks",
        )

        with caplog.at_level("WARNING"):
            manager.gc_run(run_id)  # must not raise

        assert any("worktree.remove_failed" in r.message for r in caplog.records)
        for call in runner.calls:
            argv = call["argv"]
            assert argv[-3:] != ["update-ref", "-d", expected_ref], (
                "the branch ref must survive a locked/in_use worktree removal"
            )

    def test_git_error_during_removal_also_keeps_its_branch_ref(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """C-2: the other non-removal outcome -- `worktree_remove` RAISING `GitError`
        (rather than returning `locked`/`in_use`) -- must be honoured identically."""
        workspace_root = str(tmp_path / "ws")
        run_id = "run-1"
        task_id = "orphan-task"
        common_dir = str(tmp_path / "repo" / ".git")
        isolated_repo = IsolatedRepo(
            key="core-abcd1234",
            toplevel=str(tmp_path / "repo"),
            common_dir=common_dir,
            members=[RepoMember(repo_id="core", rel="")],
        )
        expected_path = paths.worktree_root(workspace_root, run_id, task_id, isolated_repo.key)
        branch = paths.task_branch(run_id, task_id)
        expected_ref = f"refs/heads/{branch}"
        porcelain = (
            f"worktree {expected_path}\nHEAD {'a' * 40}\nbranch {expected_ref}\n\n"
        ).encode()
        common_dir_resp = ok(stdout=(common_dir + "\n").encode())
        porcelain_resp = ok(stdout=porcelain)
        runner = RecordingFakeRunner(
            responses=[
                common_dir_resp,  # 1: gc_run's own worktree_list -> rev-parse
                porcelain_resp,  # 2: ...                          -> list --porcelain
                common_dir_resp,  # 3: worktree_remove's internal worktree_list -> rev-parse
                porcelain_resp,  # 4: ...                                        -> list --porcelain
                ok(stdout=b"", stderr=b"fatal: boom", returncode=128),  # 5: worktree remove fails
                common_dir_resp,  # 6: prune_worktrees_scoped's worktree_list -> rev-parse
                porcelain_resp,  # 7: ...                                     -> list --porcelain
                ok(stdout=b""),  # 8: worktree prune (no foreign prunable)
                ok(stdout=f"{expected_ref} {'a' * 40}\n".encode()),  # 9: list_refs(ref_prefix)
                ok(stdout=b""),  # 10: list_refs(runs_prefix) -- empty
            ]
        )
        manager = WorktreeManager(
            workspace_root=workspace_root,
            run_id=run_id,
            repos=[isolated_repo],
            integration_heads={isolated_repo.key: "a" * 40},
            runner=runner,
            hooks_dir=tmp_path / "hooks",
        )

        with caplog.at_level("WARNING"):
            manager.gc_run(run_id)  # must not raise

        assert any("worktree.remove_failed" in r.message for r in caplog.records)
        for call in runner.calls:
            argv = call["argv"]
            assert argv[-3:] != ["update-ref", "-d", expected_ref]


# ---------------------------------------------------------------------------------------
# .gitignore'd content is never materialized in a fresh worktree (AC-14 / NFR-6)
# ---------------------------------------------------------------------------------------


class TestGitignoredContentNeverMaterializes:
    def test_ignored_directory_absent_from_worktree(self, tmp_path: Path) -> None:
        repo = make_repo(tmp_path / "repo", files={".gitignore": "ignored/\n", "f.txt": "a\n"})
        ignored_dir = repo / "ignored"
        ignored_dir.mkdir()
        (ignored_dir / "big.bin").write_bytes(b"x" * (1024 * 1024))

        manager, iso_repo, head = _manager_for(repo, "run-1", tmp_path / "hooks")
        iso = manager.ensure("task-a", 1, [])

        wt_path = Path(iso.repos[0].worktree_root)
        assert not (wt_path / "ignored").exists()


# ---------------------------------------------------------------------------------------
# TaskIsolation holds no reference to mutable run state (R-20/NFR-3, AC-19)
# ---------------------------------------------------------------------------------------


class TestTaskIsolationIsolatedFromRunState:
    def test_module_never_imports_run_state_types(self) -> None:
        """No `import`/`from ... import` in this module names `RunState` or `TaskSpec`
        (R-20/NFR-3) -- checked structurally over the AST's import nodes so the module's
        own docstrings (which discuss the invariant by name) don't trip a naive substring
        scan.
        """
        source = inspect.getsource(worktrees)
        tree = ast.parse(source)
        imported_names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                imported_names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.Import):
                imported_names.update(alias.name for alias in node.names)
        assert "RunState" not in imported_names
        assert "TaskSpec" not in imported_names

    def test_fields_are_plain_data(self) -> None:
        import dataclasses

        field_names = {f.name for f in dataclasses.fields(TaskIsolation)}
        assert field_names == {"task_id", "cycle", "declared_outputs", "workspace_root", "repos"}


# ---------------------------------------------------------------------------------------
# S-9: every directory created under $AO_STATE_DIR is mode 0700
# ---------------------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file mode bits only")
class TestDirectoryPermissions:
    def test_worktree_parent_directories_are_0700(self, tmp_path: Path) -> None:
        repo = make_repo(tmp_path / "repo")
        manager, iso_repo, head = _manager_for(repo, "run-1", tmp_path / "hooks")

        iso = manager.ensure("task-a", 1, [])

        wt_path = Path(iso.repos[0].worktree_root)
        checked = 0
        current = wt_path.parent
        state_dir = paths.state_dir()
        while current != state_dir and state_dir in current.parents:
            assert stat.S_IMODE(current.stat().st_mode) == 0o700, current
            checked += 1
            current = current.parent
        assert checked >= 2  # at least workspace_key and run_id levels were checked
