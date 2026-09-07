"""Tests for `agent_orchestrator.isolation.integrator` (E-Wk9Tz3 T-Ib5Qy9).

Real `git init` temp repos drive every scenario (via `conftest.py`'s `make_repo`/
`make_conflict_repo` plus this module's own small fixture helpers, which mirror
`test_worktrees.py`'s established pattern). `resolver_hook`/`escalation_hook` are always
test-local stubs -- `T-Rm2Lx7`/`T-Lr6Ka3` are not implemented yet and this module must not
depend on them.
"""

from __future__ import annotations

import logging
import os
import subprocess
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Literal

import pytest

from agent_orchestrator.isolation import paths
from agent_orchestrator.isolation.git import GitRepo
from agent_orchestrator.isolation.integrator import (
    CAS_RETRY_BOUND,
    REASON_DENYLISTED_PATH,
    REASON_LOCK_TIMEOUT,
    REASON_REF_RACE,
    STATUS_EMPTY,
    STATUS_FAILED,
    STATUS_INTEGRATED,
    IntegrationResult,
    Integrator,
    ResolverHook,
    RunIntegrationSnapshot,
)
from agent_orchestrator.isolation.worktrees import (
    IsolatedRepo,
    TaskIsolation,
    WorktreeManager,
    group_repos,
)
from agent_orchestrator.models import (
    ON_DENYLISTED_ALLOW,
    ON_DENYLISTED_WARN,
    TIER_AUTO,
    IntegrationSpec,
    ResolverConfig,
    TaskIntegrationState,
)

from .conftest import CONFLICT_KINDS, make_conflict_repo, make_repo

_CONFLICTING_KINDS = [k for k in CONFLICT_KINDS if k != "clean"]


# ---------------------------------------------------------------------------------------
# Fixture helpers (mirror `test_worktrees.py`'s `_raw_git`/`_isolated_repo_for`/
# `_manager_for` -- never the thing under test)
# ---------------------------------------------------------------------------------------


def _raw_git(args: list[str], cwd: Path) -> str:
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
    repos: dict[str, Path],
    run_id: str,
    hooks_dir: Path,
    *,
    workspace_root: str | None = None,
    heads: dict[str, str] | None = None,
) -> tuple[WorktreeManager, dict[str, IsolatedRepo]]:
    iso_repos: dict[str, IsolatedRepo] = {
        rid: _isolated_repo_for(p, rid) for rid, p in repos.items()
    }
    integration_heads = heads or {
        iso.key: _raw_git(["rev-parse", "HEAD"], repos[rid]).strip()
        for rid, iso in iso_repos.items()
    }
    manager = WorktreeManager(
        workspace_root=workspace_root or str(next(iter(repos.values()))),
        run_id=run_id,
        repos=list(iso_repos.values()),
        integration_heads=integration_heads,
        hooks_dir=hooks_dir,
    )
    return manager, iso_repos


def _sibling_commit(repo: Path, base_sha: str, filename: str, content: str) -> str:
    """Fixture-only: a commit on a throwaway branch from *base_sha*, simulating a sibling
    task that has already landed on the shared integration ref by the time this test's
    task tries to land. Restores the main checkout afterward."""
    branch = f"sibling-{filename.replace('/', '-')}"
    _raw_git(["checkout", "-q", "-b", branch, base_sha], repo)
    (repo / filename).write_text(content)
    _raw_git(["add", "-A"], repo)
    _raw_git(["commit", "-q", "-m", f"sibling: {filename}"], repo)
    sha = _raw_git(["rev-parse", "HEAD"], repo).strip()
    _raw_git(["checkout", "-q", "main"], repo)
    _raw_git(["branch", "-D", branch], repo)
    return sha


def _set_integration_ref(repo: Path, run_id: str, sha: str) -> None:
    ref = f"refs/heads/{paths.integration_branch(run_id)}"
    _raw_git(["update-ref", ref, sha], repo)


def _fixed_clock(dt: datetime) -> Callable[[], datetime]:
    return lambda: dt


def _adapter() -> logging.LoggerAdapter:
    """`get_run_logger` (the codebase's own convention -- `engine.py` uses it identically)
    rather than a bare `logging.LoggerAdapter`: the stdlib adapter's default `process()`
    unconditionally REPLACES `extra=` with its own (empty) dict, discarding the
    `event`/`task_id`/`at` fields every `Integrator._log_event` call site passes -- exactly
    the bug `logging_setup._MergingAdapter` exists to fix."""
    from agent_orchestrator.logging_setup import get_run_logger

    return get_run_logger("run-1")


def _never_resolves(
    conflicted_paths: list[str],
    *,
    worktree: str,
    git: GitRepo,
    config: ResolverConfig,
    env: dict[str, str],
) -> list[str]:
    """`ResolverHook` stub: resolves nothing, returns the input unchanged (AC-7)."""
    return list(conflicted_paths)


def _make_escalation_stub(
    status: Literal["conflict_resolver", "conflict_rerun", "failed"] = "conflict_resolver",
    reason: str | None = None,
) -> Callable[[TaskIntegrationState, IntegrationSpec, str], IntegrationResult]:
    calls: list[tuple[TaskIntegrationState, str]] = []

    def _hook(
        task_integration: TaskIntegrationState, spec: IntegrationSpec, cause: str
    ) -> IntegrationResult:
        calls.append((task_integration, cause))
        return IntegrationResult(
            status=status,
            reason=reason,
            conflicted_paths=list(task_integration.conflicted_paths),
            tier_reached=task_integration.tier_reached,
        )

    _hook.calls = calls  # type: ignore[attr-defined]
    return _hook


def _integrator(
    spec: IntegrationSpec,
    hooks_dir: Path,
    *,
    resolver_hook: ResolverHook = _never_resolves,
    escalation_hook: Callable | None = None,
    runner: object | None = None,
    clock: Callable[[], datetime] | None = None,
) -> Integrator:
    return Integrator(
        spec,
        _adapter(),
        clock or _fixed_clock(datetime(2020, 1, 1)),
        resolver_hook,
        escalation_hook or _make_escalation_stub(),
        runner=runner,
        hooks_dir=hooks_dir,
    )


def _run_snapshot(run_id: str, run_dir: Path) -> RunIntegrationSnapshot:
    return RunIntegrationSnapshot(
        run_id=run_id, branch=paths.integration_branch(run_id), run_dir=str(run_dir)
    )


# ---------------------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------------------


class TestHappyPath:
    def test_single_repo_fast_path_no_sibling(self, tmp_path: Path) -> None:
        repo = make_repo(tmp_path / "repo", files={"f.txt": "a\nb\nc\n"})
        manager, isos = _manager_for({"core": repo}, "run-1", tmp_path / "hooks")
        task_iso = manager.ensure("task-a", 1, [])

        wt = Path(task_iso.repos[0].worktree_root)
        (wt / "new.txt").write_text("agent work\n")
        _raw_git(["add", "-A"], wt)
        _raw_git(["commit", "-q", "-m", "agent work"], wt)

        integrator = _integrator(IntegrationSpec(), tmp_path / "hooks")
        result = integrator.integrate(
            task_iso,
            _run_snapshot("run-1", tmp_path / "rundir"),
            TaskIntegrationState(),
            1,
            agent_id="agent-1",
        )

        assert result.status == STATUS_INTEGRATED
        assert result.tier_reached == TIER_AUTO
        assert result.verify_status == "passed"
        landed_sha = result.heads[isos["core"].key]
        assert (
            _raw_git(["rev-parse", f"refs/heads/{paths.integration_branch('run-1')}"], repo).strip()
            == landed_sha
        )
        assert (
            _raw_git(
                ["rev-list", "--count", f"{task_iso.repos[0].base}..{landed_sha}"], repo
            ).strip()
            == "1"
        )

    def test_rebase_clean_with_disjoint_hunks(self, tmp_path: Path) -> None:
        repo, base_sha, ours, theirs = make_conflict_repo(tmp_path / "repo", "clean")
        manager, isos = _manager_for(
            {"core": repo},
            "run-1",
            tmp_path / "hooks",
            heads={_isolated_repo_for(repo).key: base_sha},
        )
        task_iso = manager.ensure("task-a", 1, [])
        ours_tip = _raw_git(["rev-parse", ours], repo).strip()
        theirs_tip = _raw_git(["rev-parse", theirs], repo).strip()

        wt = Path(task_iso.repos[0].worktree_root)
        _raw_git(["cherry-pick", ours_tip], wt)
        _set_integration_ref(repo, "run-1", theirs_tip)

        integrator = _integrator(IntegrationSpec(), tmp_path / "hooks")
        result = integrator.integrate(
            task_iso,
            _run_snapshot("run-1", tmp_path / "rundir"),
            TaskIntegrationState(),
            1,
            agent_id="agent-1",
        )

        assert result.status == STATUS_INTEGRATED
        assert result.tier_reached == TIER_AUTO
        assert isos["core"].key in result.heads
        content = (wt / "f.txt").read_text()
        assert "a-ours" in content and "c-theirs" in content

    def test_multi_repo_two_repos_land_both(self, tmp_path: Path) -> None:
        repo_a = make_repo(tmp_path / "repo-a", files={"a.txt": "a\n"})
        repo_b = make_repo(tmp_path / "repo-b", files={"b.txt": "b\n"})
        manager, isos = _manager_for({"a": repo_a, "b": repo_b}, "run-1", tmp_path / "hooks")
        task_iso = manager.ensure("task-a", 1, [])
        assert len(task_iso.repos) == 2

        for ri in task_iso.repos:
            wt = Path(ri.worktree_root)
            (wt / "out.txt").write_text(f"work in {ri.key}\n")
            _raw_git(["add", "-A"], wt)
            _raw_git(["commit", "-q", "-m", "work"], wt)

        integrator = _integrator(IntegrationSpec(), tmp_path / "hooks")
        result = integrator.integrate(
            task_iso,
            _run_snapshot("run-1", tmp_path / "rundir"),
            TaskIntegrationState(),
            1,
            agent_id="agent-1",
        )

        assert result.status == STATUS_INTEGRATED
        assert set(result.heads) == {isos["a"].key, isos["b"].key}
        for rid, repo_path in (("a", repo_a), ("b", repo_b)):
            key = isos[rid].key
            assert (
                _raw_git(
                    ["rev-parse", f"refs/heads/{paths.integration_branch('run-1')}"], repo_path
                ).strip()
                == result.heads[key]
            )

    def test_nested_reporef_one_repo(self, tmp_path: Path) -> None:
        repo = make_repo(tmp_path / "repo", files={"f.txt": "x\n"})
        (repo / "docs-md").mkdir()
        (repo / "docs-md" / "readme.md").write_text("hi\n")
        _raw_git(["add", "-A"], repo)
        _raw_git(["commit", "-q", "-m", "add docs"], repo)

        groups, skipped = group_repos({"core": str(repo), "docs": str(repo / "docs-md")})
        assert skipped == []
        assert len(groups) == 1
        head = _raw_git(["rev-parse", "HEAD"], repo).strip()
        manager = WorktreeManager(
            workspace_root=str(repo),
            run_id="run-1",
            repos=groups,
            integration_heads={groups[0].key: head},
            hooks_dir=tmp_path / "hooks",
        )
        task_iso = manager.ensure("task-a", 1, [])
        assert len(task_iso.repos) == 1

        wt = Path(task_iso.repos[0].worktree_root)
        (wt / "docs-md" / "readme.md").write_text("updated\n")
        _raw_git(["add", "-A"], wt)
        _raw_git(["commit", "-q", "-m", "update docs"], wt)

        integrator = _integrator(IntegrationSpec(), tmp_path / "hooks")
        result = integrator.integrate(
            task_iso,
            _run_snapshot("run-1", tmp_path / "rundir"),
            TaskIntegrationState(),
            1,
            agent_id="agent-1",
        )
        assert result.status == STATUS_INTEGRATED


# ---------------------------------------------------------------------------------------
# Conflicts (every `make_conflict_repo` kind)
# ---------------------------------------------------------------------------------------


class TestConflicts:
    @pytest.mark.parametrize("kind", _CONFLICTING_KINDS)
    def test_conflict_kind_reports_correct_paths_and_leaves_worktree_mid_rebase(
        self, tmp_path: Path, kind: str
    ) -> None:
        repo, base_sha, ours, theirs = make_conflict_repo(tmp_path / "repo", kind)
        isolated = _isolated_repo_for(repo)
        manager, isos = _manager_for(
            {"core": repo}, "run-1", tmp_path / "hooks", heads={isolated.key: base_sha}
        )
        task_iso = manager.ensure("task-a", 1, [])
        ours_tip = _raw_git(["rev-parse", ours], repo).strip()
        theirs_tip = _raw_git(["rev-parse", theirs], repo).strip()

        wt = Path(task_iso.repos[0].worktree_root)
        _raw_git(["cherry-pick", ours_tip], wt)
        _set_integration_ref(repo, "run-1", theirs_tip)

        escalation = _make_escalation_stub(status="conflict_resolver")
        integrator = _integrator(IntegrationSpec(), tmp_path / "hooks", escalation_hook=escalation)
        result = integrator.integrate(
            task_iso,
            _run_snapshot("run-1", tmp_path / "rundir"),
            TaskIntegrationState(),
            1,
            agent_id="agent-1",
        )

        assert result.status == "conflict_resolver"
        assert result.conflicted_paths  # non-empty, real paths
        git = GitRepo(str(repo), hooks_dir=tmp_path / "hooks")
        assert git.rebase_in_progress(str(wt)) is True
        assert git.conflicted_paths(str(wt)) == sorted(result.conflicted_paths) or set(
            result.conflicted_paths
        ) == set(git.conflicted_paths(str(wt)))
        # Escalation hook was actually invoked with the observed conflicted paths.
        assert escalation.calls[0][1] == "conflict"
        assert set(escalation.calls[0][0].conflicted_paths) == set(result.conflicted_paths)


# ---------------------------------------------------------------------------------------
# C-2 (review): the T1 "resolver actually resolves" path
# ---------------------------------------------------------------------------------------


def _two_file_conflict(tmp_path: Path) -> tuple[Path, str, str, str]:
    """A repo with TWO files that both conflict between `ours`/`theirs` (unlike
    `make_conflict_repo`, whose kinds each shape exactly one conflicting file)."""
    repo = make_repo(tmp_path / "repo", files={"a.txt": "a\n", "b.txt": "b\n"})
    base_sha = _raw_git(["rev-parse", "HEAD"], repo).strip()

    def _branch(name: str, a_content: str, b_content: str) -> None:
        _raw_git(["checkout", "-q", "-b", name, base_sha], repo)
        (repo / "a.txt").write_text(a_content)
        (repo / "b.txt").write_text(b_content)
        _raw_git(["add", "-A"], repo)
        _raw_git(["commit", "-q", "-m", name], repo)

    ours, theirs = "ours", "theirs"
    _branch(ours, "a-ours\n", "b-ours\n")
    _branch(theirs, "a-theirs\n", "b-theirs\n")
    _raw_git(["checkout", "-q", "main"], repo)
    return repo, base_sha, ours, theirs


class TestMechanicalResolution:
    def test_resolver_resolves_one_path_leaves_another_for_escalation(self, tmp_path: Path) -> None:
        repo, base_sha, ours, theirs = _two_file_conflict(tmp_path)
        isolated = _isolated_repo_for(repo)
        manager, _isos = _manager_for(
            {"core": repo}, "run-1", tmp_path / "hooks", heads={isolated.key: base_sha}
        )
        task_iso = manager.ensure("task-a", 1, [])
        ours_tip = _raw_git(["rev-parse", ours], repo).strip()
        theirs_tip = _raw_git(["rev-parse", theirs], repo).strip()
        wt = Path(task_iso.repos[0].worktree_root)
        _raw_git(["cherry-pick", ours_tip], wt)
        _set_integration_ref(repo, "run-1", theirs_tip)

        seen_paths: list[list[str]] = []

        def partial_resolver(
            conflicted_paths: list[str],
            *,
            worktree: str,
            git: GitRepo,
            config: ResolverConfig,
            env: dict[str, str],
        ) -> list[str]:
            seen_paths.append(list(conflicted_paths))
            (Path(worktree) / "a.txt").write_text("resolved-a\n")
            git.add_paths(worktree, ["a.txt"])
            return [p for p in conflicted_paths if p != "a.txt"]

        escalation = _make_escalation_stub(status="conflict_resolver")
        integrator = _integrator(
            IntegrationSpec(),
            tmp_path / "hooks",
            resolver_hook=partial_resolver,
            escalation_hook=escalation,
        )
        result = integrator.integrate(
            task_iso,
            _run_snapshot("run-1", tmp_path / "rundir"),
            TaskIntegrationState(),
            1,
            agent_id="agent-1",
        )

        assert seen_paths == [["a.txt", "b.txt"]]
        assert result.status == "conflict_resolver"
        assert result.conflicted_paths == ["b.txt"]
        assert result.tier_reached == "mechanical"
        # The escalation hook received the RESOLVER's output, not the raw rebase conflict.
        assert escalation.calls[0][0].conflicted_paths == ["b.txt"]
        assert escalation.calls[0][0].tier_reached == "mechanical"
        git = GitRepo(str(repo), hooks_dir=tmp_path / "hooks")
        assert git.rebase_in_progress(str(wt)) is True
        assert git.conflicted_paths(str(wt)) == ["b.txt"]
        # a.txt's resolution really is staged in the worktree.
        assert (wt / "a.txt").read_text() == "resolved-a\n"

    def test_rerere_only_resolution_lands_without_ever_calling_the_resolver_hook(
        self, tmp_path: Path
    ) -> None:
        """S-5: a conflict git's own rerere fully replays and stages is still tier
        `mechanical` -- and the injected `resolver_hook` (T1's OWN separate mechanism) is
        never even invoked, since `outcome.paths` already comes back empty from
        `rebase_onto` by the time this module would call it.

        One repo, ONE conflict shape, used twice: first as a real rebase (teaching rerere
        the resolution, via `GitRepo` directly -- `RERERE_ARGS` is on every call), captured
        by its PRE-rebase shas; then the task's own worktree replays the IDENTICAL
        (pre-rebase) `ours`/`theirs` shas as the real integration scenario, so rerere's
        cache entry (keyed by conflict-hunk content, not commit identity) matches and
        auto-resolves again.
        """
        repo, base_sha, ours, theirs = _two_file_conflict(tmp_path)
        ours_tip = _raw_git(["rev-parse", ours], repo).strip()
        theirs_tip = _raw_git(["rev-parse", theirs], repo).strip()

        # Teach rerere the resolution for this exact conflict shape via a real rebase
        # (on a throwaway branch -- the task's own worktree replays from the captured,
        # still-unrebased `ours_tip`/`theirs_tip` shas below, unaffected by this rebase
        # moving the `ours` branch ref itself).
        g = GitRepo(str(repo), hooks_dir=tmp_path / "hooks")
        _raw_git(["checkout", "-q", ours], repo)
        outcome = g.rebase_onto(str(repo), theirs_tip, base_sha, ours)
        assert outcome.clean is False
        (repo / "a.txt").write_text("resolved-a\n")
        (repo / "b.txt").write_text("resolved-b\n")
        g.add_all(str(repo))
        cont = g.rebase_continue(str(repo))
        assert cont.clean is True
        _raw_git(["checkout", "-q", "main"], repo)

        isolated = _isolated_repo_for(repo)
        manager, _isos = _manager_for(
            {"core": repo}, "run-1", tmp_path / "hooks", heads={isolated.key: base_sha}
        )
        task_iso = manager.ensure("task-a", 1, [])
        wt = Path(task_iso.repos[0].worktree_root)
        _raw_git(["cherry-pick", ours_tip], wt)
        _set_integration_ref(repo, "run-1", theirs_tip)

        resolver_calls: list[list[str]] = []

        def never_called(
            conflicted_paths: list[str],
            *,
            worktree: str,
            git: GitRepo,
            config: ResolverConfig,
            env: dict[str, str],
        ) -> list[str]:
            resolver_calls.append(list(conflicted_paths))
            return list(conflicted_paths)

        integrator = _integrator(IntegrationSpec(), tmp_path / "hooks", resolver_hook=never_called)
        result = integrator.integrate(
            task_iso,
            _run_snapshot("run-1", tmp_path / "rundir"),
            TaskIntegrationState(),
            1,
            agent_id="agent-1",
        )

        assert resolver_calls == []
        assert result.status == STATUS_INTEGRATED
        assert result.tier_reached == "mechanical"


# ---------------------------------------------------------------------------------------
# Empty task
# ---------------------------------------------------------------------------------------


class TestEmpty:
    def test_task_that_only_writes_outside_repo_is_empty(self, tmp_path: Path) -> None:
        repo = make_repo(tmp_path / "repo")
        manager, _isos = _manager_for({"core": repo}, "run-1", tmp_path / "hooks")
        task_iso = manager.ensure("task-a", 1, [])
        # No changes at all in the worktree.
        integrator = _integrator(IntegrationSpec(), tmp_path / "hooks")
        result = integrator.integrate(
            task_iso,
            _run_snapshot("run-1", tmp_path / "rundir"),
            TaskIntegrationState(),
            1,
            agent_id="agent-1",
        )
        assert result.status == STATUS_EMPTY
        assert result.heads == {}
        # The integration ref was never even created (lazy creation, ADR-0013 D4) --
        # `rev-parse --verify --quiet` on a nonexistent ref exits non-zero, so this checks
        # the raw exit code rather than `_raw_git`'s always-successful helper.
        cp = subprocess.run(
            [
                "git",
                "rev-parse",
                "--verify",
                "--quiet",
                f"refs/heads/{paths.integration_branch('run-1')}",
            ],
            cwd=repo,
            capture_output=True,
            text=True,
            check=False,
        )
        assert cp.returncode != 0

    def test_gitignored_declared_output_is_not_empty_and_reported(self, tmp_path: Path) -> None:
        repo = make_repo(tmp_path / "repo", files={".gitignore": "build/\n", "f.txt": "a\n"})
        manager, _isos = _manager_for({"core": repo}, "run-1", tmp_path / "hooks")
        task_iso = manager.ensure("task-a", 1, ["build/out.bin"])

        wt = Path(task_iso.repos[0].worktree_root)
        (wt / "build").mkdir()
        (wt / "build" / "out.bin").write_bytes(b"\x00\x01")
        # No commit -- the output is deliberately left untracked + gitignored.

        integrator = _integrator(IntegrationSpec(), tmp_path / "hooks")
        result = integrator.integrate(
            task_iso,
            _run_snapshot("run-1", tmp_path / "rundir"),
            TaskIntegrationState(),
            1,
            agent_id="agent-1",
        )
        assert result.status != STATUS_EMPTY
        assert "build/out.bin" in result.untracked_outputs

    def test_auto_commit_false_with_dirty_tracked_change_is_not_empty(self, tmp_path: Path) -> None:
        """`auto_commit: false` never runs `git add`, so a TRACKED file the agent edited
        but never committed itself leaves `tip == base` -- but the worktree is not clean,
        so this must still not be reported as Empty (R-8's clean condition)."""
        repo = make_repo(tmp_path / "repo", files={"f.txt": "a\n"})
        manager, _isos = _manager_for({"core": repo}, "run-1", tmp_path / "hooks")
        task_iso = manager.ensure("task-a", 1, [])
        wt = Path(task_iso.repos[0].worktree_root)
        (wt / "f.txt").write_text("b\n")  # tracked, modified, never committed

        spec = IntegrationSpec(auto_commit=False)
        integrator = _integrator(spec, tmp_path / "hooks")
        result = integrator.integrate(
            task_iso,
            _run_snapshot("run-1", tmp_path / "rundir"),
            TaskIntegrationState(),
            1,
            agent_id="agent-1",
        )
        assert result.status != STATUS_EMPTY


# ---------------------------------------------------------------------------------------
# CAS: win / loss+retry / exceed bound / already-landed
# ---------------------------------------------------------------------------------------


class TestCas:
    def _setup_single_task(self, tmp_path: Path) -> tuple[Path, TaskIsolation, str]:
        repo = make_repo(tmp_path / "repo", files={"f.txt": "a\n"})
        base_sha = _raw_git(["rev-parse", "HEAD"], repo).strip()
        manager, _isos = _manager_for(
            {"core": repo},
            "run-1",
            tmp_path / "hooks",
            heads={_isolated_repo_for(repo).key: base_sha},
        )
        task_iso = manager.ensure("task-a", 1, [])
        wt = Path(task_iso.repos[0].worktree_root)
        (wt / "task_file.txt").write_text("task work\n")
        _raw_git(["add", "-A"], wt)
        _raw_git(["commit", "-q", "-m", "task work"], wt)
        return repo, task_iso, base_sha

    def test_cas_loss_then_bounded_retry_succeeds(self, tmp_path: Path) -> None:
        repo, task_iso, base_sha = self._setup_single_task(tmp_path)
        interloper = _sibling_commit(repo, base_sha, "sibling.txt", "sibling\n")
        ref = f"refs/heads/{paths.integration_branch('run-1')}"

        moved = {"count": 0}

        def racer(argv: list[str], *, cwd: str, env: dict | None, timeout: float):
            if "update-ref" in argv:
                idx = argv.index("update-ref")
                rest = argv[idx:]
                if len(rest) == 4 and rest[1] == ref and moved["count"] == 0:
                    moved["count"] += 1
                    subprocess.run(
                        ["git", "update-ref", ref, interloper],
                        cwd=repo,
                        capture_output=True,
                        check=True,
                    )
            return subprocess.run(
                argv, cwd=cwd, env=env, timeout=timeout, capture_output=True, check=False
            )

        integrator = _integrator(IntegrationSpec(), tmp_path / "hooks", runner=racer)
        result = integrator.integrate(
            task_iso,
            _run_snapshot("run-1", tmp_path / "rundir"),
            TaskIntegrationState(),
            1,
            agent_id="agent-1",
        )

        assert result.status == STATUS_INTEGRATED
        assert moved["count"] == 1
        key = task_iso.repos[0].key
        assert _raw_git(["rev-parse", ref], repo).strip() == result.heads[key]
        # The interloper's commit is an ancestor of the final head (rebased onto it) --
        # `_raw_git` itself asserts a zero exit code, so this line fails directly if not.
        _raw_git(["merge-base", "--is-ancestor", interloper, result.heads[key]], repo)

    def test_cas_loss_exceeding_bound_fails_ref_race(self, tmp_path: Path) -> None:
        repo, task_iso, base_sha = self._setup_single_task(tmp_path)
        ref = f"refs/heads/{paths.integration_branch('run-1')}"
        counter = {"n": 0}

        def persistent_racer(argv: list[str], *, cwd: str, env: dict | None, timeout: float):
            if "update-ref" in argv:
                idx = argv.index("update-ref")
                rest = argv[idx:]
                if len(rest) == 4 and rest[1] == ref:
                    counter["n"] += 1
                    interloper = _sibling_commit(repo, base_sha, f"sib-{counter['n']}.txt", "x\n")
                    subprocess.run(
                        ["git", "update-ref", ref, interloper],
                        cwd=repo,
                        capture_output=True,
                        check=True,
                    )
            return subprocess.run(
                argv, cwd=cwd, env=env, timeout=timeout, capture_output=True, check=False
            )

        integrator = _integrator(IntegrationSpec(), tmp_path / "hooks", runner=persistent_racer)
        result = integrator.integrate(
            task_iso,
            _run_snapshot("run-1", tmp_path / "rundir"),
            TaskIntegrationState(),
            1,
            agent_id="agent-1",
        )

        assert result.status == STATUS_FAILED
        assert result.reason == REASON_REF_RACE
        assert counter["n"] == CAS_RETRY_BOUND + 1

    def test_multi_repo_cas_exceed_bound_reports_partial_landing(self, tmp_path: Path) -> None:
        """R-7(a)/(c): the repo whose key sorts FIRST lands normally (CAS runs in sorted
        key order, so it always gets its uncontested attempt first); the repo whose key
        sorts SECOND keeps losing its CAS past the retry bound. The result names the first
        repo's already-landed head, and that repo is never re-squashed or re-CAS'd again."""
        repo_a = make_repo(tmp_path / "repo-a", files={"a.txt": "a\n"})
        repo_b = make_repo(tmp_path / "repo-b", files={"b.txt": "b\n"})
        manager, isos = _manager_for({"a": repo_a, "b": repo_b}, "run-1", tmp_path / "hooks")
        task_iso = manager.ensure("task-a", 1, [])
        for ri in task_iso.repos:
            wt = Path(ri.worktree_root)
            (wt / "task.txt").write_text("work\n")
            _raw_git(["add", "-A"], wt)
            _raw_git(["commit", "-q", "-m", "work"], wt)

        # `make_repo` names each repo's toplevel with a random uuid suffix, so which of
        # "a"/"b" sorts first by repo KEY is not fixed -- determine it directly rather than
        # assuming, and target the SECOND-sorted repo's own path/base for the race so the
        # first-sorted one always gets an uncontested CAS.
        by_path = {isos["a"].key: repo_a, isos["b"].key: repo_b}
        first_key, second_key = sorted(by_path)
        losing_repo_path = by_path[second_key]
        losing_base = _raw_git(["rev-parse", "HEAD"], losing_repo_path).strip()

        ref = f"refs/heads/{paths.integration_branch('run-1')}"
        counter = {"n": 0}

        def racer(argv: list[str], *, cwd: str, env: dict | None, timeout: float):
            targets_losing_repo = "update-ref" in argv and os.path.normpath(
                cwd
            ) == os.path.normpath(str(losing_repo_path))
            if targets_losing_repo:
                idx = argv.index("update-ref")
                rest = argv[idx:]
                if len(rest) == 4 and rest[1] == ref:
                    counter["n"] += 1
                    interloper = _sibling_commit(
                        losing_repo_path, losing_base, f"sib-{counter['n']}.txt", "x\n"
                    )
                    subprocess.run(
                        ["git", "update-ref", ref, interloper],
                        cwd=losing_repo_path,
                        capture_output=True,
                        check=True,
                    )
            return subprocess.run(
                argv, cwd=cwd, env=env, timeout=timeout, capture_output=True, check=False
            )

        integrator = _integrator(IntegrationSpec(), tmp_path / "hooks", runner=racer)
        result = integrator.integrate(
            task_iso,
            _run_snapshot("run-1", tmp_path / "rundir"),
            TaskIntegrationState(),
            1,
            agent_id="agent-1",
        )

        assert result.status == STATUS_FAILED
        assert result.reason == REASON_REF_RACE
        assert first_key in result.heads
        assert second_key not in result.heads
        winner_path = by_path[first_key]
        assert _raw_git(["rev-parse", ref], winner_path).strip() == result.heads[first_key]
        # The winning repo's landed ref is untouched by the losing repo's retry loop --
        # only ONE commit landed on top of its base, and its own CAS was never re-attempted.
        winner_iso = next(r for r in task_iso.repos if r.key == first_key)
        assert (
            _raw_git(
                ["rev-list", "--count", f"{winner_iso.base}..{result.heads[first_key]}"],
                winner_path,
            ).strip()
            == "1"
        )

    def test_cas_loss_retry_hits_a_genuine_conflict(self, tmp_path: Path) -> None:
        """The CAS-loss RETRY re-rebases onto whatever the ref moved to -- if that new
        head genuinely conflicts with the task's own work, the retry escalates exactly
        like a first-pass conflict would, rather than looping or raising."""
        repo, base_sha, ours, theirs = make_conflict_repo(tmp_path / "repo", "true_conflict")
        isolated = _isolated_repo_for(repo)
        manager, _isos = _manager_for(
            {"core": repo}, "run-1", tmp_path / "hooks", heads={isolated.key: base_sha}
        )
        task_iso = manager.ensure("task-a", 1, [])
        ours_tip = _raw_git(["rev-parse", ours], repo).strip()
        theirs_tip = _raw_git(["rev-parse", theirs], repo).strip()
        wt = Path(task_iso.repos[0].worktree_root)
        _raw_git(["cherry-pick", ours_tip], wt)
        # Integration ref does not exist yet -- the first attempt takes the fast path.

        ref = f"refs/heads/{paths.integration_branch('run-1')}"
        state = {"raced": False}

        def racer(argv: list[str], *, cwd: str, env: dict | None, timeout: float):
            if "update-ref" in argv and not state["raced"]:
                idx = argv.index("update-ref")
                rest = argv[idx:]
                if len(rest) == 4 and rest[1] == ref:
                    state["raced"] = True
                    # A sibling lands the genuinely-conflicting "theirs" commit right
                    # before this integrator's own (lazy-creation) CAS attempt.
                    subprocess.run(
                        ["git", "update-ref", ref, theirs_tip],
                        cwd=repo,
                        capture_output=True,
                        check=True,
                    )
            return subprocess.run(
                argv, cwd=cwd, env=env, timeout=timeout, capture_output=True, check=False
            )

        escalation = _make_escalation_stub(status="conflict_resolver")
        integrator = _integrator(
            IntegrationSpec(), tmp_path / "hooks", escalation_hook=escalation, runner=racer
        )
        result = integrator.integrate(
            task_iso,
            _run_snapshot("run-1", tmp_path / "rundir"),
            TaskIntegrationState(),
            1,
            agent_id="agent-1",
        )

        assert result.status == "conflict_resolver"
        assert result.conflicted_paths
        assert state["raced"] is True
        git = GitRepo(str(repo), hooks_dir=tmp_path / "hooks")
        assert git.rebase_in_progress(str(wt)) is True

    def test_already_landed_short_circuit(self, tmp_path: Path) -> None:
        """A foreign process lands EXACTLY this task's own (deterministic) squash sha
        moments before this integrator's own CAS attempt -- e.g. a crashed-and-restarted
        `ao resume` racing the still-live original. The CAS naturally loses (`expected_old`
        no longer matches), but `is_ancestor(candidate, head_now)` is true (the two shas
        are equal), so the result is `integrated` without a second retry attempt."""
        repo, task_iso, base_sha = self._setup_single_task(tmp_path)
        ref = f"refs/heads/{paths.integration_branch('run-1')}"
        key = task_iso.repos[0].key
        state = {"landed": False, "cas_calls": 0}

        def racer(argv: list[str], *, cwd: str, env: dict | None, timeout: float):
            if "update-ref" in argv:
                idx = argv.index("update-ref")
                rest = argv[idx:]
                if len(rest) == 4 and rest[1] == ref:
                    state["cas_calls"] += 1
                    if not state["landed"]:
                        # A foreign process lands the IDENTICAL (deterministic) squash sha
                        # this integrator is about to CAS to, right BEFORE the real CAS
                        # call below runs -- so that call observes a genuine mismatch.
                        state["landed"] = True
                        new_sha = rest[2]
                        subprocess.run(
                            ["git", "update-ref", ref, new_sha],
                            cwd=repo,
                            capture_output=True,
                            check=True,
                        )
            return subprocess.run(
                argv, cwd=cwd, env=env, timeout=timeout, capture_output=True, check=False
            )

        integrator = _integrator(IntegrationSpec(), tmp_path / "hooks", runner=racer)
        result = integrator.integrate(
            task_iso,
            _run_snapshot("run-1", tmp_path / "rundir"),
            TaskIntegrationState(),
            1,
            agent_id="agent-1",
        )

        assert result.status == STATUS_INTEGRATED
        assert state["cas_calls"] == 1  # short-circuited -- no restaging retry needed
        assert _raw_git(["rev-parse", ref], repo).strip() == result.heads[key]


# ---------------------------------------------------------------------------------------
# Verify
# ---------------------------------------------------------------------------------------


class TestVerify:
    def test_structural_check_passes_on_clean_tree(self, tmp_path: Path) -> None:
        repo = make_repo(tmp_path / "repo", files={"f.txt": "a\n"})
        manager, _isos = _manager_for({"core": repo}, "run-1", tmp_path / "hooks")
        task_iso = manager.ensure("task-a", 1, [])
        wt = Path(task_iso.repos[0].worktree_root)
        (wt / "g.txt").write_text("clean\n")
        _raw_git(["add", "-A"], wt)
        _raw_git(["commit", "-q", "-m", "clean work"], wt)

        integrator = _integrator(IntegrationSpec(), tmp_path / "hooks")
        result = integrator.integrate(
            task_iso,
            _run_snapshot("run-1", tmp_path / "rundir"),
            TaskIntegrationState(),
            1,
            agent_id="agent-1",
        )
        assert result.status == STATUS_INTEGRATED
        assert result.verify_status == "passed"

    def test_structural_check_fails_on_leftover_conflict_marker(self, tmp_path: Path) -> None:
        repo = make_repo(tmp_path / "repo", files={"f.txt": "a\n"})
        manager, _isos = _manager_for({"core": repo}, "run-1", tmp_path / "hooks")
        task_iso = manager.ensure("task-a", 1, [])
        wt = Path(task_iso.repos[0].worktree_root)
        (wt / "f.txt").write_text("<<<<<<< HEAD\nours\n=======\ntheirs\n>>>>>>> branch\n")
        _raw_git(["add", "-A"], wt)
        _raw_git(["commit", "-q", "-m", "oops, left markers"], wt)

        escalation = _make_escalation_stub(status="conflict_rerun", reason="verify_failed")
        integrator = _integrator(IntegrationSpec(), tmp_path / "hooks", escalation_hook=escalation)
        result = integrator.integrate(
            task_iso,
            _run_snapshot("run-1", tmp_path / "rundir"),
            TaskIntegrationState(),
            1,
            agent_id="agent-1",
        )
        assert result.status == "conflict_rerun"
        assert result.verify_status == "failed"
        assert escalation.calls[0][1] == "verify"

    def test_structural_check_fails_on_trailing_whitespace_via_diff_check(
        self, tmp_path: Path
    ) -> None:
        """Distinct from the conflict-marker test above: no marker is present, so `git
        grep -l` finds nothing, and only `git diff --check` (trailing whitespace) catches
        it -- exercising that check's own failure branch specifically."""
        repo = make_repo(tmp_path / "repo", files={"f.txt": "a\nb\nc\n"})
        manager, _isos = _manager_for({"core": repo}, "run-1", tmp_path / "hooks")
        task_iso = manager.ensure("task-a", 1, [])
        wt = Path(task_iso.repos[0].worktree_root)
        (wt / "f.txt").write_text("a\nb   \nc\n")  # trailing whitespace, no markers
        _raw_git(["add", "-A"], wt)
        _raw_git(["commit", "-q", "-m", "trailing whitespace"], wt)

        escalation = _make_escalation_stub(status="conflict_rerun", reason="verify_failed")
        integrator = _integrator(IntegrationSpec(), tmp_path / "hooks", escalation_hook=escalation)
        result = integrator.integrate(
            task_iso,
            _run_snapshot("run-1", tmp_path / "rundir"),
            TaskIntegrationState(),
            1,
            agent_id="agent-1",
        )
        assert result.status == "conflict_rerun"
        assert result.verify_status == "failed"

    def test_grep_conflict_check_git_error_propagates_as_git_error_failure(
        self, tmp_path: Path
    ) -> None:
        """A genuinely broken `git grep` invocation (never a routine "no match") must
        surface as `GitError`, caught by `integrate()`'s own outer handler -- never a
        silent verify pass."""
        repo = make_repo(tmp_path / "repo", files={"f.txt": "a\n"})
        manager, _isos = _manager_for({"core": repo}, "run-1", tmp_path / "hooks")
        task_iso = manager.ensure("task-a", 1, [])
        wt = Path(task_iso.repos[0].worktree_root)
        (wt / "g.txt").write_text("x\n")
        _raw_git(["add", "-A"], wt)
        _raw_git(["commit", "-q", "-m", "work"], wt)

        real = subprocess.run

        def failing_grep_runner(argv: list[str], *, cwd: str, env: dict | None, timeout: float):
            if "grep" in argv:
                return subprocess.CompletedProcess(
                    args=argv, returncode=128, stdout=b"", stderr=b"fatal: injected failure\n"
                )
            return real(argv, cwd=cwd, env=env, timeout=timeout, capture_output=True, check=False)

        integrator = _integrator(IntegrationSpec(), tmp_path / "hooks", runner=failing_grep_runner)
        result = integrator.integrate(
            task_iso,
            _run_snapshot("run-1", tmp_path / "rundir"),
            TaskIntegrationState(),
            1,
            agent_id="agent-1",
        )
        assert result.status == STATUS_FAILED
        assert result.reason == "git_error"

    def test_diff_check_git_error_propagates_as_git_error_failure(self, tmp_path: Path) -> None:
        repo = make_repo(tmp_path / "repo", files={"f.txt": "a\n"})
        manager, _isos = _manager_for({"core": repo}, "run-1", tmp_path / "hooks")
        task_iso = manager.ensure("task-a", 1, [])
        wt = Path(task_iso.repos[0].worktree_root)
        (wt / "g.txt").write_text("x\n")
        _raw_git(["add", "-A"], wt)
        _raw_git(["commit", "-q", "-m", "work"], wt)

        real = subprocess.run

        def failing_diff_check_runner(
            argv: list[str], *, cwd: str, env: dict | None, timeout: float
        ):
            if "diff" in argv and "--check" in argv:
                return subprocess.CompletedProcess(
                    args=argv, returncode=128, stdout=b"", stderr=b"fatal: injected failure\n"
                )
            return real(argv, cwd=cwd, env=env, timeout=timeout, capture_output=True, check=False)

        integrator = _integrator(
            IntegrationSpec(), tmp_path / "hooks", runner=failing_diff_check_runner
        )
        result = integrator.integrate(
            task_iso,
            _run_snapshot("run-1", tmp_path / "rundir"),
            TaskIntegrationState(),
            1,
            agent_id="agent-1",
        )
        assert result.status == STATUS_FAILED
        assert result.reason == "git_error"

    def test_verify_command_pass(self, tmp_path: Path) -> None:
        repo = make_repo(tmp_path / "repo", files={"f.txt": "a\n"})
        manager, _isos = _manager_for({"core": repo}, "run-1", tmp_path / "hooks")
        task_iso = manager.ensure("task-a", 1, [])
        wt = Path(task_iso.repos[0].worktree_root)
        (wt / "g.txt").write_text("x\n")
        _raw_git(["add", "-A"], wt)
        _raw_git(["commit", "-q", "-m", "work"], wt)

        spec = IntegrationSpec(verify_command=["true"])
        integrator = _integrator(spec, tmp_path / "hooks")
        result = integrator.integrate(
            task_iso,
            _run_snapshot("run-1", tmp_path / "rundir"),
            TaskIntegrationState(),
            1,
            agent_id="agent-1",
        )
        assert result.status == STATUS_INTEGRATED
        assert result.verify_status == "passed"
        capture = tmp_path / "rundir" / "task-a" / "integration" / "attempt-1"
        assert (capture / "verify.exit").read_text().strip() == "0"

    def test_verify_command_fail(self, tmp_path: Path) -> None:
        repo = make_repo(tmp_path / "repo", files={"f.txt": "a\n"})
        manager, _isos = _manager_for({"core": repo}, "run-1", tmp_path / "hooks")
        task_iso = manager.ensure("task-a", 1, [])
        wt = Path(task_iso.repos[0].worktree_root)
        (wt / "g.txt").write_text("x\n")
        _raw_git(["add", "-A"], wt)
        _raw_git(["commit", "-q", "-m", "work"], wt)

        spec = IntegrationSpec(verify_command=["false"])
        escalation = _make_escalation_stub(status="failed", reason="verify_failed")
        integrator = _integrator(spec, tmp_path / "hooks", escalation_hook=escalation)
        result = integrator.integrate(
            task_iso,
            _run_snapshot("run-1", tmp_path / "rundir"),
            TaskIntegrationState(),
            1,
            agent_id="agent-1",
        )
        assert result.status == "failed"
        assert result.verify_status == "failed"

    def test_verify_command_timeout(self, tmp_path: Path) -> None:
        repo = make_repo(tmp_path / "repo", files={"f.txt": "a\n"})
        manager, _isos = _manager_for({"core": repo}, "run-1", tmp_path / "hooks")
        task_iso = manager.ensure("task-a", 1, [])
        wt = Path(task_iso.repos[0].worktree_root)
        (wt / "g.txt").write_text("x\n")
        _raw_git(["add", "-A"], wt)
        _raw_git(["commit", "-q", "-m", "work"], wt)

        spec = IntegrationSpec(verify_command=["sleep", "5"], verify_timeout_seconds=1)
        escalation = _make_escalation_stub(status="failed", reason="verify_failed")
        integrator = _integrator(spec, tmp_path / "hooks", escalation_hook=escalation)
        result = integrator.integrate(
            task_iso,
            _run_snapshot("run-1", tmp_path / "rundir"),
            TaskIntegrationState(),
            1,
            agent_id="agent-1",
        )
        assert result.status == "failed"
        assert result.verify_status == "failed"

    def test_verify_command_missing_binary_never_silently_passes(self, tmp_path: Path) -> None:
        repo = make_repo(tmp_path / "repo", files={"f.txt": "a\n"})
        manager, _isos = _manager_for({"core": repo}, "run-1", tmp_path / "hooks")
        task_iso = manager.ensure("task-a", 1, [])
        wt = Path(task_iso.repos[0].worktree_root)
        (wt / "g.txt").write_text("x\n")
        _raw_git(["add", "-A"], wt)
        _raw_git(["commit", "-q", "-m", "work"], wt)

        spec = IntegrationSpec(verify_command=["/no/such/binary-xyz"])
        escalation = _make_escalation_stub(status="failed", reason="verify_command_error")
        integrator = _integrator(spec, tmp_path / "hooks", escalation_hook=escalation)
        result = integrator.integrate(
            task_iso,
            _run_snapshot("run-1", tmp_path / "rundir"),
            TaskIntegrationState(),
            1,
            agent_id="agent-1",
        )
        assert result.status == "failed"
        assert result.verify_status == "failed"

    def test_structural_checker_never_opens_a_repo_file(self) -> None:
        """NFR-1: source-inspection proof, not just behavioral -- the structural verify
        checker never calls `open()`/`read_text()` on repository content."""
        import ast
        import inspect

        from agent_orchestrator.isolation import integrator as integrator_module

        source = inspect.getsource(integrator_module)
        tree = ast.parse(source)
        forbidden_calls: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
                if name in ("open", "read_text", "read_bytes"):
                    forbidden_calls.append(name)
        assert forbidden_calls == []


# ---------------------------------------------------------------------------------------
# S-3: auto-commit denylist screen
# ---------------------------------------------------------------------------------------


class TestDenylistScreen:
    def test_untracked_env_file_aborts_naming_the_path(self, tmp_path: Path) -> None:
        repo = make_repo(tmp_path / "repo")
        manager, _isos = _manager_for({"core": repo}, "run-1", tmp_path / "hooks")
        task_iso = manager.ensure("task-a", 1, [])
        wt = Path(task_iso.repos[0].worktree_root)
        (wt / ".env").write_text("SECRET=1\n")

        integrator = _integrator(IntegrationSpec(), tmp_path / "hooks")
        result = integrator.integrate(
            task_iso,
            _run_snapshot("run-1", tmp_path / "rundir"),
            TaskIntegrationState(),
            1,
            agent_id="agent-1",
        )
        assert result.status == STATUS_FAILED
        assert result.reason == REASON_DENYLISTED_PATH
        assert ".env" in result.conflicted_paths
        log = _raw_git(["log", "--oneline"], wt)
        assert "SECRET" not in log

    def test_already_tracked_denylisted_file_is_not_screened(self, tmp_path: Path) -> None:
        repo = make_repo(tmp_path / "repo", files={".env": "TRACKED=1\n", "f.txt": "a\n"})
        manager, _isos = _manager_for({"core": repo}, "run-1", tmp_path / "hooks")
        task_iso = manager.ensure("task-a", 1, [])
        wt = Path(task_iso.repos[0].worktree_root)
        (wt / "f.txt").write_text("b\n")
        _raw_git(["add", "-A"], wt)
        _raw_git(["commit", "-q", "-m", "update"], wt)

        integrator = _integrator(IntegrationSpec(), tmp_path / "hooks")
        result = integrator.integrate(
            task_iso,
            _run_snapshot("run-1", tmp_path / "rundir"),
            TaskIntegrationState(),
            1,
            agent_id="agent-1",
        )
        assert result.status == STATUS_INTEGRATED

    def test_warn_mode_proceeds_and_logs(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        repo = make_repo(tmp_path / "repo")
        manager, _isos = _manager_for({"core": repo}, "run-1", tmp_path / "hooks")
        task_iso = manager.ensure("task-a", 1, [])
        wt = Path(task_iso.repos[0].worktree_root)
        (wt / ".env").write_text("SECRET=1\n")

        spec = IntegrationSpec(on_denylisted_path=ON_DENYLISTED_WARN)
        integrator = _integrator(spec, tmp_path / "hooks")
        with caplog.at_level(logging.WARNING, logger="agent_orchestrator"):
            result = integrator.integrate(
                task_iso,
                _run_snapshot("run-1", tmp_path / "rundir"),
                TaskIntegrationState(),
                1,
                agent_id="agent-1",
            )
        assert result.status == STATUS_INTEGRATED
        assert any(r.event == "integration.denylisted_path" for r in caplog.records)  # type: ignore[attr-defined]

    def test_allow_mode_proceeds_silently(self, tmp_path: Path) -> None:
        repo = make_repo(tmp_path / "repo")
        manager, _isos = _manager_for({"core": repo}, "run-1", tmp_path / "hooks")
        task_iso = manager.ensure("task-a", 1, [])
        wt = Path(task_iso.repos[0].worktree_root)
        (wt / ".env").write_text("SECRET=1\n")

        spec = IntegrationSpec(on_denylisted_path=ON_DENYLISTED_ALLOW)
        integrator = _integrator(spec, tmp_path / "hooks")
        result = integrator.integrate(
            task_iso,
            _run_snapshot("run-1", tmp_path / "rundir"),
            TaskIntegrationState(),
            1,
            agent_id="agent-1",
        )
        assert result.status == STATUS_INTEGRATED

    def test_auto_commit_false_never_adds_and_integrates_agents_own_commit(
        self, tmp_path: Path
    ) -> None:
        repo = make_repo(tmp_path / "repo")
        manager, _isos = _manager_for({"core": repo}, "run-1", tmp_path / "hooks")
        task_iso = manager.ensure("task-a", 1, [])
        wt = Path(task_iso.repos[0].worktree_root)
        (wt / "own.txt").write_text("agent committed this itself\n")
        _raw_git(["add", "-A"], wt)
        _raw_git(["commit", "-q", "-m", "agent's own commit"], wt)
        own_sha = _raw_git(["rev-parse", "HEAD"], wt).strip()

        calls: list[list[str]] = []
        real = subprocess.run

        def spy(argv: list[str], *, cwd: str, env: dict | None, timeout: float):
            calls.append(argv)
            return real(argv, cwd=cwd, env=env, timeout=timeout, capture_output=True, check=False)

        spec = IntegrationSpec(auto_commit=False)
        integrator = _integrator(spec, tmp_path / "hooks", runner=spy)
        result = integrator.integrate(
            task_iso,
            _run_snapshot("run-1", tmp_path / "rundir"),
            TaskIntegrationState(),
            1,
            agent_id="agent-1",
        )
        assert result.status == STATUS_INTEGRATED
        # The landed sha is the SQUASH commit (a distinct sha from the agent's own commit),
        # but its TREE must be identical -- confirming auto_commit:false integrated exactly
        # the agent's own content, nothing more.
        own_tree = _raw_git(["rev-parse", f"{own_sha}^{{tree}}"], wt).strip()
        landed_sha = result.heads[task_iso.repos[0].key]
        landed_tree = _raw_git(["rev-parse", f"{landed_sha}^{{tree}}"], repo).strip()
        assert landed_tree == own_tree
        assert not any("add" in argv and "-A" in argv for argv in calls)


# ---------------------------------------------------------------------------------------
# Lock timeout (real second process)
# ---------------------------------------------------------------------------------------


class TestLockTimeout:
    def test_lock_held_by_real_process_returns_failed_lock_timeout(self, tmp_path: Path) -> None:
        import multiprocessing

        repo = make_repo(tmp_path / "repo")
        manager, _isos = _manager_for({"core": repo}, "run-1", tmp_path / "hooks")
        task_iso = manager.ensure("task-a", 1, [])
        wt = Path(task_iso.repos[0].worktree_root)
        (wt / "g.txt").write_text("x\n")
        _raw_git(["add", "-A"], wt)
        _raw_git(["commit", "-q", "-m", "work"], wt)

        common_dir = task_iso.repos[0].common_dir
        ctx = multiprocessing.get_context("fork")
        ready = ctx.Event()
        release = ctx.Event()

        def _hold(lock_path: str, ready_evt, release_evt) -> None:
            import fcntl
            import os as _os

            fd = _os.open(lock_path, _os.O_CREAT | _os.O_RDWR, 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX)
            ready_evt.set()
            release_evt.wait(timeout=30.0)
            fcntl.flock(fd, fcntl.LOCK_UN)
            _os.close(fd)

        from agent_orchestrator.isolation.locks import LOCK_FILENAME

        lock_path = str(Path(common_dir) / LOCK_FILENAME)
        proc = ctx.Process(target=_hold, args=(lock_path, ready, release))
        proc.start()
        try:
            assert ready.wait(timeout=10.0)
            spec = IntegrationSpec(lock_timeout_seconds=1)
            integrator = _integrator(spec, tmp_path / "hooks")
            result = integrator.integrate(
                task_iso,
                _run_snapshot("run-1", tmp_path / "rundir"),
                TaskIntegrationState(),
                1,
                agent_id="agent-1",
            )
            assert result.status == STATUS_FAILED
            assert result.reason == REASON_LOCK_TIMEOUT
        finally:
            release.set()
            proc.join(timeout=10.0)


# ---------------------------------------------------------------------------------------
# R-20 / NFR-3: no RunState reachable
# ---------------------------------------------------------------------------------------


class TestNoRunState:
    def test_integrator_holds_no_run_state(self, tmp_path: Path) -> None:
        integrator = _integrator(IntegrationSpec(), tmp_path / "hooks")
        for value in vars(integrator).values():
            assert type(value).__name__ != "RunState"

    def test_module_never_imports_run_state_or_calls_save(self) -> None:
        """AST-based, not a raw substring search: the module's own docstrings legitimately
        discuss `RunState` (explaining the R-20 invariant this test guards) -- what must
        never appear is an actual `import`/`from ... import` naming it, or a `.save(`
        method call anywhere in the code."""
        import ast
        import inspect

        from agent_orchestrator.isolation import integrator as integrator_module

        source = inspect.getsource(integrator_module)
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all("RunState" not in alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                assert all("RunState" not in alias.name for alias in node.names)
            elif isinstance(node, ast.Attribute) and node.attr == "save":
                raise AssertionError("a `.save(` call site reached isolation/integrator.py")


# ---------------------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------------------


class TestIdempotency:
    def test_integrate_twice_in_a_row_is_safe(self, tmp_path: Path) -> None:
        repo = make_repo(tmp_path / "repo")
        manager, _isos = _manager_for({"core": repo}, "run-1", tmp_path / "hooks")
        task_iso = manager.ensure("task-a", 1, [])
        wt = Path(task_iso.repos[0].worktree_root)
        (wt / "g.txt").write_text("x\n")
        _raw_git(["add", "-A"], wt)
        _raw_git(["commit", "-q", "-m", "work"], wt)

        integrator = _integrator(IntegrationSpec(), tmp_path / "hooks")
        first = integrator.integrate(
            task_iso,
            _run_snapshot("run-1", tmp_path / "rundir"),
            TaskIntegrationState(),
            1,
            agent_id="agent-1",
        )
        assert first.status == STATUS_INTEGRATED
        second = integrator.integrate(
            task_iso,
            _run_snapshot("run-1", tmp_path / "rundir"),
            TaskIntegrationState(),
            1,
            agent_id="agent-1",
        )
        # Idempotent: whether the second call reports "integrated" (a no-op re-land whose
        # candidate already equals the current head) or "empty" (nothing left to squash),
        # the ref itself never moves and no duplicate commit is created.
        assert second.status in (STATUS_INTEGRATED, STATUS_EMPTY)
        assert second.reason is None
        ref = f"refs/heads/{paths.integration_branch('run-1')}"
        landed_sha = first.heads[task_iso.repos[0].key]
        assert _raw_git(["rev-parse", ref], repo).strip() == landed_sha
        assert (
            _raw_git(
                ["rev-list", "--count", f"{task_iso.repos[0].base}..{landed_sha}"], repo
            ).strip()
            == "1"
        )


# ---------------------------------------------------------------------------------------
# resume_integration
# ---------------------------------------------------------------------------------------


class TestResumeIntegration:
    def test_resume_after_hand_resolving_conflict(self, tmp_path: Path) -> None:
        repo, base_sha, ours, theirs = make_conflict_repo(tmp_path / "repo", "true_conflict")
        isolated = _isolated_repo_for(repo)
        manager, _isos = _manager_for(
            {"core": repo}, "run-1", tmp_path / "hooks", heads={isolated.key: base_sha}
        )
        task_iso = manager.ensure("task-a", 1, [])
        ours_tip = _raw_git(["rev-parse", ours], repo).strip()
        theirs_tip = _raw_git(["rev-parse", theirs], repo).strip()
        wt = Path(task_iso.repos[0].worktree_root)
        _raw_git(["cherry-pick", ours_tip], wt)
        _set_integration_ref(repo, "run-1", theirs_tip)

        escalation = _make_escalation_stub(status="conflict_resolver")
        integrator = _integrator(IntegrationSpec(), tmp_path / "hooks", escalation_hook=escalation)
        first = integrator.integrate(
            task_iso,
            _run_snapshot("run-1", tmp_path / "rundir"),
            TaskIntegrationState(),
            1,
            agent_id="agent-1",
        )
        assert first.status == "conflict_resolver"

        # Hand-resolve, exactly as an operator (or, later, T2's resolver agent) would.
        (wt / "f.txt").write_text("resolved\n")
        _raw_git(["add", "-A"], wt)

        ti = TaskIntegrationState(conflicted_paths=first.conflicted_paths)
        second = integrator.resume_integration(
            task_iso, _run_snapshot("run-1", tmp_path / "rundir"), ti, 1
        )
        assert second.status == STATUS_INTEGRATED
        assert second.verify_status == "passed"
        git = GitRepo(str(repo), hooks_dir=tmp_path / "hooks")
        assert git.rebase_in_progress(str(wt)) is False

    def test_resume_never_drops_a_sibling_landed_during_the_t2_window(self, tmp_path: Path) -> None:
        """C-1 (review, blocking) regression: a second, unrelated task lands a commit into
        the SAME repo between `integrate()`'s conflict escalation and the eventual
        `resume_integration()` call (the T2 resolver-dispatch window, during which the
        per-repo lock is released). The sibling's commit must remain an ancestor of the
        final integration head, and the resumed task's own (hand-resolved) change must be
        present -- neither silently overwritten by a stale, pre-round-trip candidate sha.
        """
        repo, base_sha, ours, theirs = make_conflict_repo(tmp_path / "repo", "true_conflict")
        isolated = _isolated_repo_for(repo)
        manager, _isos = _manager_for(
            {"core": repo}, "run-1", tmp_path / "hooks", heads={isolated.key: base_sha}
        )
        task_iso = manager.ensure("task-a", 1, [])
        ours_tip = _raw_git(["rev-parse", ours], repo).strip()
        theirs_tip = _raw_git(["rev-parse", theirs], repo).strip()
        wt = Path(task_iso.repos[0].worktree_root)
        _raw_git(["cherry-pick", ours_tip], wt)
        _set_integration_ref(repo, "run-1", theirs_tip)

        escalation = _make_escalation_stub(status="conflict_resolver")
        integrator = _integrator(IntegrationSpec(), tmp_path / "hooks", escalation_hook=escalation)
        first = integrator.integrate(
            task_iso,
            _run_snapshot("run-1", tmp_path / "rundir"),
            TaskIntegrationState(),
            1,
            agent_id="agent-1",
        )
        assert first.status == "conflict_resolver"

        # A sibling task lands into this SAME repo while this task's T2 resolver is
        # (conceptually) still running -- moving the integration ref past `theirs_tip`.
        sibling_sha = _sibling_commit(repo, theirs_tip, "sibling.txt", "sibling work\n")
        _set_integration_ref(repo, "run-1", sibling_sha)

        # Hand-resolve, exactly as an operator (or T2's resolver agent) would.
        (wt / "f.txt").write_text("resolved\n")
        _raw_git(["add", "-A"], wt)

        ti = TaskIntegrationState(conflicted_paths=first.conflicted_paths)
        second = integrator.resume_integration(
            task_iso, _run_snapshot("run-1", tmp_path / "rundir"), ti, 1
        )

        assert second.status == STATUS_INTEGRATED
        final_head = second.heads[task_iso.repos[0].key]
        ref = f"refs/heads/{paths.integration_branch('run-1')}"
        assert _raw_git(["rev-parse", ref], repo).strip() == final_head
        # The sibling's commit was NOT dropped -- `_raw_git` itself asserts a zero exit
        # code, so a non-ancestor relationship fails this line directly.
        _raw_git(["merge-base", "--is-ancestor", sibling_sha, final_head], repo)
        # The resumed task's own resolution IS present.
        show = _raw_git(["show", f"{final_head}:sibling.txt"], repo)
        assert show == "sibling work\n"

    def test_resume_multi_repo_only_one_repo_moved_during_t2_window(self, tmp_path: Path) -> None:
        """C-1 multi-repo variant: two repos, only ONE of which receives a sibling landing
        during the T2 window. The untouched repo lands its own (unaffected) work
        correctly; the moved repo's sibling commit is not dropped."""
        conflict_repo, base_sha, ours, theirs = make_conflict_repo(
            tmp_path / "repo-conflict", "true_conflict"
        )
        clean_repo = make_repo(tmp_path / "repo-clean", files={"c.txt": "c\n"})
        conflict_iso = _isolated_repo_for(conflict_repo, "conflict")
        clean_iso = _isolated_repo_for(clean_repo, "clean")
        clean_base = _raw_git(["rev-parse", "HEAD"], clean_repo).strip()
        manager = WorktreeManager(
            workspace_root=str(tmp_path),
            run_id="run-1",
            repos=[conflict_iso, clean_iso],
            integration_heads={conflict_iso.key: base_sha, clean_iso.key: clean_base},
            hooks_dir=tmp_path / "hooks",
        )
        task_iso = manager.ensure("task-a", 1, [])
        repos_by_key = {ri.key: ri for ri in task_iso.repos}

        conflict_wt = Path(repos_by_key[conflict_iso.key].worktree_root)
        ours_tip = _raw_git(["rev-parse", ours], conflict_repo).strip()
        theirs_tip = _raw_git(["rev-parse", theirs], conflict_repo).strip()
        _raw_git(["cherry-pick", ours_tip], conflict_wt)
        _set_integration_ref(conflict_repo, "run-1", theirs_tip)

        clean_wt = Path(repos_by_key[clean_iso.key].worktree_root)
        (clean_wt / "own.txt").write_text("clean task work\n")
        _raw_git(["add", "-A"], clean_wt)
        _raw_git(["commit", "-q", "-m", "clean work"], clean_wt)

        escalation = _make_escalation_stub(status="conflict_resolver")
        integrator = _integrator(IntegrationSpec(), tmp_path / "hooks", escalation_hook=escalation)
        first = integrator.integrate(
            task_iso,
            _run_snapshot("run-1", tmp_path / "rundir"),
            TaskIntegrationState(),
            1,
            agent_id="agent-1",
        )
        assert first.status == "conflict_resolver"

        # Only the CONFLICTING repo receives a sibling landing during the T2 window.
        sibling_sha = _sibling_commit(conflict_repo, theirs_tip, "sibling.txt", "sib\n")
        _set_integration_ref(conflict_repo, "run-1", sibling_sha)

        (conflict_wt / "f.txt").write_text("resolved\n")
        _raw_git(["add", "-A"], conflict_wt)

        ti = TaskIntegrationState(conflicted_paths=first.conflicted_paths)
        second = integrator.resume_integration(
            task_iso, _run_snapshot("run-1", tmp_path / "rundir"), ti, 1
        )

        assert second.status == STATUS_INTEGRATED
        conflict_head = second.heads[conflict_iso.key]
        conflict_ref = f"refs/heads/{paths.integration_branch('run-1')}"
        _raw_git(["merge-base", "--is-ancestor", sibling_sha, conflict_head], conflict_repo)
        assert _raw_git(["rev-parse", conflict_ref], conflict_repo).strip() == conflict_head

        clean_head = second.heads[clean_iso.key]
        clean_ref = f"refs/heads/{paths.integration_branch('run-1')}"
        assert _raw_git(["rev-parse", clean_ref], clean_repo).strip() == clean_head
        show = _raw_git(["show", f"{clean_head}:own.txt"], clean_repo)
        assert show == "clean task work\n"

    def test_resume_multi_repo_lands_the_non_conflicted_repo_too(self, tmp_path: Path) -> None:
        """Multi-repo: one repo conflicts (mid-rebase after the first `integrate()` call),
        the other never needed a rebase at all. `resume_integration` must land BOTH --
        exercising the "not `rebase_in_progress`" branch for the second repo."""
        clean_repo = make_repo(tmp_path / "repo-clean", files={"c.txt": "c\n"})
        conflict_repo, base_sha, ours, theirs = make_conflict_repo(
            tmp_path / "repo-conflict", "true_conflict"
        )
        clean_iso = _isolated_repo_for(clean_repo, "clean")
        conflict_iso = _isolated_repo_for(conflict_repo, "conflict")
        clean_base = _raw_git(["rev-parse", "HEAD"], clean_repo).strip()
        manager = WorktreeManager(
            workspace_root=str(tmp_path),
            run_id="run-1",
            repos=[clean_iso, conflict_iso],
            integration_heads={clean_iso.key: clean_base, conflict_iso.key: base_sha},
            hooks_dir=tmp_path / "hooks",
        )
        task_iso = manager.ensure("task-a", 1, [])
        repos_by_key = {ri.key: ri for ri in task_iso.repos}

        clean_wt = Path(repos_by_key[clean_iso.key].worktree_root)
        (clean_wt / "extra.txt").write_text("extra\n")
        _raw_git(["add", "-A"], clean_wt)
        _raw_git(["commit", "-q", "-m", "clean work"], clean_wt)

        conflict_wt = Path(repos_by_key[conflict_iso.key].worktree_root)
        ours_tip = _raw_git(["rev-parse", ours], conflict_repo).strip()
        theirs_tip = _raw_git(["rev-parse", theirs], conflict_repo).strip()
        _raw_git(["cherry-pick", ours_tip], conflict_wt)
        _set_integration_ref(conflict_repo, "run-1", theirs_tip)

        escalation = _make_escalation_stub(status="conflict_resolver")
        integrator = _integrator(IntegrationSpec(), tmp_path / "hooks", escalation_hook=escalation)
        first = integrator.integrate(
            task_iso,
            _run_snapshot("run-1", tmp_path / "rundir"),
            TaskIntegrationState(),
            1,
            agent_id="agent-1",
        )
        assert first.status == "conflict_resolver"

        (conflict_wt / "f.txt").write_text("resolved\n")
        _raw_git(["add", "-A"], conflict_wt)

        ti = TaskIntegrationState(conflicted_paths=first.conflicted_paths)
        second = integrator.resume_integration(
            task_iso, _run_snapshot("run-1", tmp_path / "rundir"), ti, 1
        )
        assert second.status == STATUS_INTEGRATED
        assert set(second.heads) == {clean_iso.key, conflict_iso.key}
        clean_ref = f"refs/heads/{paths.integration_branch('run-1')}"
        assert _raw_git(["rev-parse", clean_ref], clean_repo).strip() == second.heads[clean_iso.key]

    def test_resume_lock_timeout(self, tmp_path: Path) -> None:
        import multiprocessing

        from agent_orchestrator.isolation.locks import LOCK_FILENAME

        repo, base_sha, ours, theirs = make_conflict_repo(tmp_path / "repo", "true_conflict")
        isolated = _isolated_repo_for(repo)
        manager, _isos = _manager_for(
            {"core": repo}, "run-1", tmp_path / "hooks", heads={isolated.key: base_sha}
        )
        task_iso = manager.ensure("task-a", 1, [])
        wt = Path(task_iso.repos[0].worktree_root)
        ours_tip = _raw_git(["rev-parse", ours], repo).strip()
        theirs_tip = _raw_git(["rev-parse", theirs], repo).strip()
        _raw_git(["cherry-pick", ours_tip], wt)
        _set_integration_ref(repo, "run-1", theirs_tip)

        escalation = _make_escalation_stub(status="conflict_resolver")
        integrator = _integrator(IntegrationSpec(), tmp_path / "hooks", escalation_hook=escalation)
        first = integrator.integrate(
            task_iso,
            _run_snapshot("run-1", tmp_path / "rundir"),
            TaskIntegrationState(),
            1,
            agent_id="agent-1",
        )
        assert first.status == "conflict_resolver"
        (wt / "f.txt").write_text("resolved\n")
        _raw_git(["add", "-A"], wt)

        ctx = multiprocessing.get_context("fork")
        ready = ctx.Event()
        release = ctx.Event()

        def _hold(lock_path: str, ready_evt, release_evt) -> None:
            import fcntl
            import os as _os

            fd = _os.open(lock_path, _os.O_CREAT | _os.O_RDWR, 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX)
            ready_evt.set()
            release_evt.wait(timeout=30.0)
            fcntl.flock(fd, fcntl.LOCK_UN)
            _os.close(fd)

        lock_path = str(Path(task_iso.repos[0].common_dir) / LOCK_FILENAME)
        proc = ctx.Process(target=_hold, args=(lock_path, ready, release))
        proc.start()
        try:
            assert ready.wait(timeout=10.0)
            slow_spec = IntegrationSpec(lock_timeout_seconds=1)
            slow_integrator = _integrator(slow_spec, tmp_path / "hooks", escalation_hook=escalation)
            ti = TaskIntegrationState(conflicted_paths=first.conflicted_paths)
            result = slow_integrator.resume_integration(
                task_iso, _run_snapshot("run-1", tmp_path / "rundir"), ti, 1
            )
            assert result.status == STATUS_FAILED
            assert result.reason == REASON_LOCK_TIMEOUT
        finally:
            release.set()
            proc.join(timeout=10.0)

    def test_resume_verify_failure_invokes_escalation_with_verify_cause(
        self, tmp_path: Path
    ) -> None:
        repo, base_sha, ours, theirs = make_conflict_repo(tmp_path / "repo", "true_conflict")
        isolated = _isolated_repo_for(repo)
        manager, _isos = _manager_for(
            {"core": repo}, "run-1", tmp_path / "hooks", heads={isolated.key: base_sha}
        )
        task_iso = manager.ensure("task-a", 1, [])
        wt = Path(task_iso.repos[0].worktree_root)
        ours_tip = _raw_git(["rev-parse", ours], repo).strip()
        theirs_tip = _raw_git(["rev-parse", theirs], repo).strip()
        _raw_git(["cherry-pick", ours_tip], wt)
        _set_integration_ref(repo, "run-1", theirs_tip)

        escalation = _make_escalation_stub(status="conflict_resolver")
        integrator = _integrator(IntegrationSpec(), tmp_path / "hooks", escalation_hook=escalation)
        first = integrator.integrate(
            task_iso,
            _run_snapshot("run-1", tmp_path / "rundir"),
            TaskIntegrationState(),
            1,
            agent_id="agent-1",
        )
        assert first.status == "conflict_resolver"

        # "Resolve" by leaving a conflict marker behind -- the structural verify check
        # must catch it.
        (wt / "f.txt").write_text("<<<<<<< HEAD\nx\n=======\ny\n>>>>>>> branch\n")
        _raw_git(["add", "-A"], wt)

        verify_escalation = _make_escalation_stub(status="conflict_rerun", reason="verify_failed")
        integrator2 = _integrator(
            IntegrationSpec(), tmp_path / "hooks", escalation_hook=verify_escalation
        )
        ti = TaskIntegrationState(conflicted_paths=first.conflicted_paths)
        second = integrator2.resume_integration(
            task_iso, _run_snapshot("run-1", tmp_path / "rundir"), ti, 1
        )
        assert second.status == "conflict_rerun"
        assert second.verify_status == "failed"
        assert verify_escalation.calls[0][1] == "verify"

    def test_resume_cas_ref_race_fails_without_retry(self, tmp_path: Path) -> None:
        repo, base_sha, ours, theirs = make_conflict_repo(tmp_path / "repo", "true_conflict")
        isolated = _isolated_repo_for(repo)
        manager, _isos = _manager_for(
            {"core": repo}, "run-1", tmp_path / "hooks", heads={isolated.key: base_sha}
        )
        task_iso = manager.ensure("task-a", 1, [])
        wt = Path(task_iso.repos[0].worktree_root)
        ours_tip = _raw_git(["rev-parse", ours], repo).strip()
        theirs_tip = _raw_git(["rev-parse", theirs], repo).strip()
        _raw_git(["cherry-pick", ours_tip], wt)
        _set_integration_ref(repo, "run-1", theirs_tip)

        escalation = _make_escalation_stub(status="conflict_resolver")
        integrator = _integrator(IntegrationSpec(), tmp_path / "hooks", escalation_hook=escalation)
        first = integrator.integrate(
            task_iso,
            _run_snapshot("run-1", tmp_path / "rundir"),
            TaskIntegrationState(),
            1,
            agent_id="agent-1",
        )
        assert first.status == "conflict_resolver"
        (wt / "f.txt").write_text("resolved\n")
        _raw_git(["add", "-A"], wt)

        ref = f"refs/heads/{paths.integration_branch('run-1')}"

        def racer(argv: list[str], *, cwd: str, env: dict | None, timeout: float):
            if "update-ref" in argv:
                idx = argv.index("update-ref")
                rest = argv[idx:]
                if len(rest) == 4 and rest[1] == ref:
                    interloper = _sibling_commit(repo, theirs_tip, "sib.txt", "x\n")
                    subprocess.run(
                        ["git", "update-ref", ref, interloper],
                        cwd=repo,
                        capture_output=True,
                        check=True,
                    )
            return subprocess.run(
                argv, cwd=cwd, env=env, timeout=timeout, capture_output=True, check=False
            )

        racy_integrator = _integrator(
            IntegrationSpec(), tmp_path / "hooks", escalation_hook=escalation, runner=racer
        )
        ti = TaskIntegrationState(conflicted_paths=first.conflicted_paths)
        second = racy_integrator.resume_integration(
            task_iso, _run_snapshot("run-1", tmp_path / "rundir"), ti, 1
        )
        assert second.status == STATUS_FAILED
        assert second.reason == REASON_REF_RACE
