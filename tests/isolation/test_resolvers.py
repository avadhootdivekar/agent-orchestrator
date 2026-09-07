"""Tests for `isolation/resolvers.py` (E-Wk9Tz3 T-Rm2Lx7, HLD §11 M6).

Real git fixtures throughout (never mocked git) -- `make_conflict_repo`'s six locked
conflict kinds (`tests/isolation/conftest.py`, T-Gt4Pw8) plus a couple of locally-built
repos for regenerate/rerere scenarios `make_conflict_repo` doesn't cover (a chosen
conflicting path name, a taught-then-replayed rerere resolution). All identities/dates are
pinned by `conftest.py`'s `_isolated_git_env`/`_commit_env` so nothing depends on the real
`~`/`$AO_STATE_DIR` or wall-clock time.
"""

from __future__ import annotations

import ast
import inspect
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

from agent_orchestrator.isolation.git import GitRepo
from agent_orchestrator.isolation.integrator import ResolverHook
from agent_orchestrator.isolation.resolvers import (
    RESOLVER_REGENERATE,
    RESOLVER_REGISTRY,
    RESOLVER_RERERE,
    RESOLVER_UNION,
    MechanicalResolver,
    ResolutionPlan,
    ResolutionStep,
    apply_plan,
    plan_resolution,
    resolve_mechanically,
)
from agent_orchestrator.models import RegenerateRule, ResolverConfig

from .conftest import (
    CONFLICT_KINDS,
    GIT_AUTHOR_EMAIL,
    GIT_AUTHOR_NAME,
    make_conflict_repo,
    make_repo,
)

RESOLVERS_SRC = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "agent_orchestrator"
    / "isolation"
    / "resolvers.py"
)


def _git_repo(repo: Path, *, rerere: bool = True) -> GitRepo:
    return GitRepo(str(repo), rerere=rerere)


def _rebase_conflict(repo: Path, base_sha: str, ours: str, theirs: str, *, rerere: bool = True):
    """Real `git rebase --onto theirs base ours` against *repo*'s main working tree (no
    isolated worktree needed -- `make_conflict_repo` builds a plain repo). Returns
    `(git, outcome)`.
    """
    git = _git_repo(repo, rerere=rerere)
    outcome = git.rebase_onto(str(repo), theirs, base_sha, ours)
    return git, outcome


# ---------------------------------------------------------------------------------------
# plan_resolution -- pure, table-driven
# ---------------------------------------------------------------------------------------


class TestPlanResolution:
    def test_already_resolved_wins_over_everything(self) -> None:
        cfg = ResolverConfig(
            union=["*.txt"], regenerate=[RegenerateRule(glob="*.txt", command=["true"])]
        )
        plan = plan_resolution(["a.txt"], cfg, already_resolved={"a.txt"})
        assert plan.steps == [ResolutionStep("a.txt", RESOLVER_RERERE, None)]
        assert plan.unresolved == []

    def test_regenerate_wins_over_union_when_both_match(self) -> None:
        cfg = ResolverConfig(
            union=["*.lock"], regenerate=[RegenerateRule(glob="*.lock", command=["true"])]
        )
        plan = plan_resolution(["uv.lock"], cfg, already_resolved=set())
        assert plan.steps == [ResolutionStep("uv.lock", RESOLVER_REGENERATE, "*.lock")]

    def test_first_matching_regenerate_rule_wins_in_declared_order(self) -> None:
        cfg = ResolverConfig(
            regenerate=[
                RegenerateRule(glob="*.lock", command=["first"]),
                RegenerateRule(glob="uv.lock", command=["second"]),
            ]
        )
        plan = plan_resolution(["uv.lock"], cfg, already_resolved=set())
        assert plan.steps == [ResolutionStep("uv.lock", RESOLVER_REGENERATE, "*.lock")]

    def test_union_used_when_no_regenerate_rule_matches(self) -> None:
        cfg = ResolverConfig(union=["mod.rs"])
        plan = plan_resolution(["mod.rs"], cfg, already_resolved=set())
        assert plan.steps == [ResolutionStep("mod.rs", RESOLVER_UNION, "mod.rs")]

    def test_first_matching_union_glob_wins_in_declared_order(self) -> None:
        cfg = ResolverConfig(union=["*.rs", "mod.rs"])
        plan = plan_resolution(["mod.rs"], cfg, already_resolved=set())
        assert plan.steps == [ResolutionStep("mod.rs", RESOLVER_UNION, "*.rs")]

    def test_no_match_is_unresolved(self) -> None:
        plan = plan_resolution(["src/apis/accounts.rs"], ResolverConfig(), already_resolved=set())
        assert plan.steps == []
        assert plan.unresolved == ["src/apis/accounts.rs"]

    def test_mixed_paths_each_take_their_own_branch(self) -> None:
        cfg = ResolverConfig(
            union=["mod.rs"], regenerate=[RegenerateRule(glob="*.lock", command=["true"])]
        )
        plan = plan_resolution(
            ["uv.lock", "mod.rs", "already.txt", "orphan.py"],
            cfg,
            already_resolved={"already.txt"},
        )
        assert plan.steps == [
            ResolutionStep("already.txt", RESOLVER_RERERE, None),
            ResolutionStep("mod.rs", RESOLVER_UNION, "mod.rs"),
            ResolutionStep("uv.lock", RESOLVER_REGENERATE, "*.lock"),
        ]
        assert plan.unresolved == ["orphan.py"]

    def test_deterministic_regardless_of_input_order(self) -> None:
        cfg = ResolverConfig(
            union=["mod.rs"], regenerate=[RegenerateRule(glob="*.lock", command=["true"])]
        )
        paths = ["z.py", "uv.lock", "mod.rs", "a.py"]
        already = {"z.py"}
        plan_a = plan_resolution(paths, cfg, already)
        plan_b = plan_resolution(list(reversed(paths)), cfg, already)
        plan_c = plan_resolution(["mod.rs", "a.py", "z.py", "uv.lock"], cfg, already)
        assert plan_a == plan_b == plan_c

    def test_default_union_globs_are_empty(self) -> None:
        """AC-8: shipping a non-empty default would silently union-merge the wrong file."""
        assert ResolverConfig().union == []


# ---------------------------------------------------------------------------------------
# UnionResolver -- handled vs refused, per `make_conflict_repo` kind
# ---------------------------------------------------------------------------------------


class TestUnionResolverConflictKinds:
    """Each locked `CONFLICT_KINDS` entry -> expected union outcome, with `cfg.union`
    configured to match the conflicting path so precedence never masks the case (AC-3).
    """

    @pytest.mark.parametrize(
        ("kind", "path", "expect_resolved"),
        [
            ("union", "f.txt", True),
            ("true_conflict", "f.txt", False),  # non-additive: same line rewritten both sides
            ("add_add", "new.txt", False),  # no common base
            ("delete_modify", "f.txt", False),  # one side has no version at all
            ("binary", "bin.dat", False),
        ],
    )
    def test_kind_outcome(
        self, tmp_path: Path, kind: str, path: str, expect_resolved: bool
    ) -> None:
        repo, base_sha, ours, theirs = make_conflict_repo(tmp_path, kind)
        git, outcome = _rebase_conflict(repo, base_sha, ours, theirs)
        assert outcome.clean is False
        assert outcome.paths == [path]
        # Whatever git itself left in the worktree for this conflict shape (markers for a
        # true textual conflict; one side's plain content for delete/modify, which has no
        # markers to begin with) -- captured BEFORE resolution so the refused-case
        # assertion below proves byte-for-byte untouched, not just "still has markers".
        before = (repo / path).read_bytes() if (repo / path).exists() else None

        unresolved = resolve_mechanically(
            list(outcome.paths),
            worktree=str(repo),
            git=git,
            config=ResolverConfig(union=[path]),
            env=_env(),
        )
        if expect_resolved:
            assert unresolved == []
            assert git.conflicted_paths(str(repo)) == []
        else:
            assert unresolved == [path]
            # Markers intact, unstaged -- untouched for T2/T3 escalation (AC).
            assert git.conflicted_paths(str(repo)) == [path]
            after = (repo / path).read_bytes() if (repo / path).exists() else None
            assert after == before

    def test_clean_kind_never_conflicts(self, tmp_path: Path) -> None:
        repo, base_sha, ours, theirs = make_conflict_repo(tmp_path, "clean")
        git, outcome = _rebase_conflict(repo, base_sha, ours, theirs)
        assert outcome.clean is True
        assert outcome.paths == []

    def test_all_conflict_kinds_are_covered(self) -> None:
        # Documentation guard: fails loudly if `conftest.py` grows a 7th kind this test
        # doesn't yet have an outcome row for.
        assert set(CONFLICT_KINDS) == {
            "clean",
            "union",
            "true_conflict",
            "add_add",
            "delete_modify",
            "binary",
        }


class TestUnionOrderingAndDeduping:
    def test_both_appends_kept_in_deterministic_stage_order(self, tmp_path: Path) -> None:
        """Deterministic, not "ours-branch-name-first": `_rebase_conflict` rebases the
        `ours` branch ONTO `theirs` -- and git's rebase machinery swaps the meaning of
        ours/theirs relative to a merge (index stage 2 = the ONTO target = `theirs`
        branch content; stage 3 = the branch being replayed = `ours` branch content, see
        `RegenerateResolver`'s docstring for the same gotcha). `git merge-file --union`
        always emits stage-2 content before stage-3 -- so here that is `theirs`-branch's
        line first. What matters for this test is that the order is FIXED given fixed
        inputs, proven by re-running and comparing.
        """
        repo, base_sha, ours, theirs = make_conflict_repo(tmp_path, "union")
        git, outcome = _rebase_conflict(repo, base_sha, ours, theirs)
        resolve_mechanically(
            list(outcome.paths),
            worktree=str(repo),
            git=git,
            config=ResolverConfig(union=["f.txt"]),
            env=_env(),
        )
        merged = (repo / "f.txt").read_text()
        assert merged == "a\nb\nc\ntheirs-entry\nours-entry\n"

        # Re-derive the identical conflict from scratch and confirm the SAME order comes
        # out again -- the determinism claim, not just a hardcoded expectation.
        repo2, base_sha2, ours2, theirs2 = make_conflict_repo(tmp_path, "union")
        git2, outcome2 = _rebase_conflict(repo2, base_sha2, ours2, theirs2)
        resolve_mechanically(
            list(outcome2.paths),
            worktree=str(repo2),
            git=git2,
            config=ResolverConfig(union=["f.txt"]),
            env=_env(),
        )
        assert (repo2 / "f.txt").read_text() == merged

    def test_shared_prefix_in_a_conflicting_append_is_deduped(self, tmp_path: Path) -> None:
        """A line IDENTICAL on both sides never conflicts by itself (git's own 3-way merge
        auto-merges an identical addition with no conflict at all -- T0, before this
        module is ever invoked). The dedup guarantee that matters is narrower: within a
        hunk that DOES conflict (because the two sides diverge elsewhere in it), any line
        the two sides happen to share is still emitted once, not twice.

        Order note: same rebase-swap gotcha as the test above -- stage 2 ("ours") is the
        `theirs`-named branch (the rebase onto-target), stage 3 ("theirs") is the
        `ours`-named branch (the branch being replayed), so the shared `x` is followed by
        `theirs` branch's own line (`z`) before `ours` branch's (`y`).
        """
        repo = make_repo(tmp_path, files={"f.txt": "a\nb\nc\n"})
        base_sha = _raw_git(["rev-parse", "HEAD"], repo)
        _branch(repo, "ours", {"f.txt": "a\nb\nc\nx\ny\n"}, "ours append x,y")
        _checkout(repo, "main")
        _branch(repo, "theirs", {"f.txt": "a\nb\nc\nx\nz\n"}, "theirs append x,z")
        _checkout(repo, "main")
        git, outcome = _rebase_conflict(repo, base_sha, "ours", "theirs")
        assert outcome.paths == ["f.txt"]
        unresolved = resolve_mechanically(
            list(outcome.paths),
            worktree=str(repo),
            git=git,
            config=ResolverConfig(union=["f.txt"]),
            env=_env(),
        )
        assert unresolved == []
        merged = (repo / "f.txt").read_text()
        assert merged == "a\nb\nc\nx\nz\ny\n"
        assert merged.count("x\n") == 1  # the shared line appears exactly once


# ---------------------------------------------------------------------------------------
# RegenerateResolver -- success / failure / timeout, own bound (S-6)
# ---------------------------------------------------------------------------------------


def _write_regen_script(tmp_path: Path, body: str) -> list[str]:
    script = tmp_path / f"regen-{uuid.uuid4().hex[:8]}.py"
    script.write_text(body)
    return [sys.executable, str(script)]


class TestRegenerateResolver:
    def test_stale_rule_reference_declines(self, tmp_path: Path) -> None:
        """Defensive branch: a `ResolutionStep.rule` that no longer matches any rule in
        `cfg.regenerate` (e.g. config changed between planning and applying) is declined,
        not crashed on.
        """
        repo, base_sha, ours, theirs = make_conflict_repo(tmp_path, "binary")
        git, outcome = _rebase_conflict(repo, base_sha, ours, theirs)
        plan = ResolutionPlan(
            steps=[ResolutionStep("bin.dat", RESOLVER_REGENERATE, "*.no-such-rule")]
        )
        unresolved = apply_plan(plan, str(repo), git, _env(), ResolverConfig())
        assert unresolved == ["bin.dat"]

    def test_missing_side_declines_without_crashing(self, tmp_path: Path) -> None:
        """A `delete_modify` conflict has no content on one side for the configured
        `take` -- `git.show_stage` returns `None` for it, and the resolver must decline
        cleanly rather than writing a missing/garbage file.
        """
        repo, base_sha, ours, theirs = make_conflict_repo(tmp_path, "delete_modify")
        git, outcome = _rebase_conflict(repo, base_sha, ours, theirs)
        assert outcome.paths == ["f.txt"]
        cfg = ResolverConfig(
            regenerate=[RegenerateRule(glob="*.txt", command=["true"], take="theirs")]
        )
        unresolved = resolve_mechanically(
            list(outcome.paths), worktree=str(repo), git=git, config=cfg, env=_env()
        )
        assert unresolved == ["f.txt"]

    def test_success_regenerates_and_stages(self, tmp_path: Path) -> None:
        repo, base_sha, ours, theirs = make_conflict_repo(tmp_path, "binary")
        git, outcome = _rebase_conflict(repo, base_sha, ours, theirs)
        assert outcome.paths == ["bin.dat"]
        command = _write_regen_script(
            tmp_path,
            "import pathlib, sys\n"
            "pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else 'bin.dat')"
            ".write_bytes(b'regenerated')\n",
        )
        cfg = ResolverConfig(
            regenerate=[RegenerateRule(glob="*.dat", command=command, timeout_seconds=5)]
        )
        unresolved = resolve_mechanically(
            list(outcome.paths), worktree=str(repo), git=git, config=cfg, env=_env()
        )
        assert unresolved == []
        assert (repo / "bin.dat").read_bytes() == b"regenerated"
        assert git.conflicted_paths(str(repo)) == []

    def test_nonzero_exit_marks_unresolved(self, tmp_path: Path) -> None:
        repo, base_sha, ours, theirs = make_conflict_repo(tmp_path, "binary")
        git, outcome = _rebase_conflict(repo, base_sha, ours, theirs)
        command = _write_regen_script(tmp_path, "import sys\nsys.exit(1)\n")
        cfg = ResolverConfig(
            regenerate=[RegenerateRule(glob="*.dat", command=command, timeout_seconds=5)]
        )
        unresolved = resolve_mechanically(
            list(outcome.paths), worktree=str(repo), git=git, config=cfg, env=_env()
        )
        assert unresolved == ["bin.dat"]
        assert git.conflicted_paths(str(repo)) == ["bin.dat"]

    def test_hang_is_killed_at_its_own_rule_timeout_not_the_lock_timeout(
        self, tmp_path: Path
    ) -> None:
        """S-6 amendment: the command is bounded by `rule.timeout_seconds`, never
        `integration.lock_timeout_seconds` (1800s default) -- assert elapsed time is near
        the RULE's own small timeout.
        """
        repo, base_sha, ours, theirs = make_conflict_repo(tmp_path, "binary")
        git, outcome = _rebase_conflict(repo, base_sha, ours, theirs)
        command = _write_regen_script(tmp_path, "import time\ntime.sleep(30)\n")
        cfg = ResolverConfig(
            regenerate=[RegenerateRule(glob="*.dat", command=command, timeout_seconds=1)]
        )
        started = time.monotonic()
        unresolved = resolve_mechanically(
            list(outcome.paths), worktree=str(repo), git=git, config=cfg, env=_env()
        )
        elapsed = time.monotonic() - started
        assert unresolved == ["bin.dat"]
        assert elapsed < 10, f"expected a fast fail near the rule's 1s timeout, took {elapsed}s"

    def test_take_ours_selects_the_rebase_onto_target_stage(self, tmp_path: Path) -> None:
        """`rule.take` passes straight through to `git checkout --ours/--theirs`, which
        during a REBASE means: "ours" = the branch being rebased ONTO (here, the branch
        named `theirs` -- see `RegenerateResolver`'s docstring for why). This test's
        naming intentionally keeps the branches named `ours`/`theirs` to make that swap
        legible rather than hiding it behind different branch names.
        """
        repo = make_repo(tmp_path, files={"gen.lock": "base\n"})
        base_sha = _raw_git(["rev-parse", "HEAD"], repo)
        _branch(repo, "ours", {"gen.lock": "ours-branch-source\n"}, "ours edit")
        _checkout(repo, "main")
        _branch(repo, "theirs", {"gen.lock": "theirs-branch-source\n"}, "theirs edit")
        _checkout(repo, "main")
        # Rebase `ours` onto `theirs`: git's internal stage-2 ("--ours") is `theirs`
        # branch's content; stage-3 ("--theirs") is `ours` branch's content.
        git, outcome = _rebase_conflict(repo, base_sha, "ours", "theirs")
        assert outcome.paths == ["gen.lock"]
        command = _write_regen_script(
            tmp_path,
            "import pathlib\n"
            "src = pathlib.Path('gen.lock').read_text()\n"
            "pathlib.Path('gen.lock').write_text('regenerated-from:' + src)\n",
        )
        cfg = ResolverConfig(
            regenerate=[
                RegenerateRule(glob="*.lock", command=command, take="ours", timeout_seconds=5)
            ]
        )
        unresolved = resolve_mechanically(
            list(outcome.paths), worktree=str(repo), git=git, config=cfg, env=_env()
        )
        assert unresolved == []
        assert (repo / "gen.lock").read_text() == "regenerated-from:theirs-branch-source\n"

    def test_requery_reflects_truth_not_apply_plans_own_bookkeeping(self, tmp_path: Path) -> None:
        """AC-6: `apply_plan`'s returned unresolved list comes from a fresh
        `git.conflicted_paths()` call, not from tracking "which steps succeeded" itself.
        Two files conflict in the same rebase: `f.dat` has a matching (successful)
        regenerate rule; `g.txt` has no matching rule at all (falls into
        `plan.unresolved`, never even attempted). The correct final answer -- only
        `g.txt` remains -- falls straight out of the re-query without `apply_plan` ever
        having to reconcile "steps I applied" against "paths nobody touched" itself.
        """
        repo = make_repo(tmp_path, files={"f.dat": "base\n", "g.txt": "base\n"})
        base_sha = _raw_git(["rev-parse", "HEAD"], repo)
        _branch(repo, "ours", {"f.dat": "ours-f\n", "g.txt": "ours-g\n"}, "ours edits both")
        _checkout(repo, "main")
        _branch(repo, "theirs", {"f.dat": "theirs-f\n", "g.txt": "theirs-g\n"}, "theirs edits both")
        _checkout(repo, "main")
        git, outcome = _rebase_conflict(repo, base_sha, "ours", "theirs")
        assert sorted(outcome.paths) == ["f.dat", "g.txt"]

        command = _write_regen_script(
            tmp_path, "import pathlib\npathlib.Path('f.dat').write_text('regenerated\\n')\n"
        )
        cfg = ResolverConfig(
            regenerate=[RegenerateRule(glob="*.dat", command=command, timeout_seconds=5)]
        )
        unresolved = resolve_mechanically(
            list(outcome.paths), worktree=str(repo), git=git, config=cfg, env=_env()
        )
        assert unresolved == ["g.txt"]
        assert (repo / "f.dat").read_text() == "regenerated\n"
        assert "<<<<<<<" in (repo / "g.txt").read_text()


# ---------------------------------------------------------------------------------------
# rerere replay recognition
# ---------------------------------------------------------------------------------------


def _teach_then_replay(tmp_path: Path, *, rerere: bool) -> tuple[Path, GitRepo, list[str]]:
    """Builds one repo with a SINGLE-file conflict, teaches git's rerere the resolution
    via a throwaway branch, then reproduces the identical conflict on a fresh branch pair
    -- proving a REAL rerere replay, not a mocked one (AC-5).
    """
    repo = make_repo(tmp_path, files={"f.txt": "a\nb\nc\n"})
    base_sha = _raw_git(["rev-parse", "HEAD"], repo)
    _branch(repo, "ours", {"f.txt": "a-ours\nb\nc\n"}, "ours edit")
    _checkout(repo, "main")
    _branch(repo, "theirs", {"f.txt": "a-theirs\nb\nc\n"}, "theirs edit")
    _checkout(repo, "main")

    teach_git = _git_repo(repo, rerere=True)
    _branch(repo, "teach", {}, message=None, from_branch="ours")
    outcome = teach_git.rebase_onto(str(repo), "theirs", base_sha, "teach")
    assert outcome.clean is False
    (repo / "f.txt").write_text("a-resolved\nb\nc\n")
    teach_git.add_paths(str(repo), ["f.txt"])
    cont = teach_git.rebase_continue(str(repo))
    assert cont.clean is True
    _checkout(repo, "main")

    _branch(repo, "replay", {}, message=None, from_branch="ours")
    replay_git = _git_repo(repo, rerere=rerere)
    replay_outcome = replay_git.rebase_onto(str(repo), "theirs", base_sha, "replay")
    return repo, replay_git, list(replay_outcome.paths)


class TestRerereReplay:
    def test_replayed_resolution_is_recognized_and_evented_as_t1(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        repo, git, outcome_paths = _teach_then_replay(tmp_path, rerere=True)
        # The single-conflict rebase stops (non-clean exit) even though rerere already
        # staged the full resolution (git-verified empirically) -- `conflicted_paths()`
        # is already empty at this point (S-5's "resolver_hook not even called" case);
        # feed it back in anyway to exercise the `already_resolved` recognition path a
        # real caller could still hit with a broader/defensive path list (see module
        # docstring's `already_resolved` note).
        with caplog.at_level("INFO", logger="agent_orchestrator.isolation.resolvers"):
            unresolved = resolve_mechanically(
                ["f.txt"], worktree=str(repo), git=git, config=ResolverConfig(), env=_env()
            )
        assert unresolved == []
        assert outcome_paths == []  # sanity: rerere really did fully stage it already
        assert (repo / "f.txt").read_text() == "a-resolved\nb\nc\n"
        events = [r for r in caplog.records if getattr(r, "event", None) == "integration.resolved"]
        assert len(events) == 1
        assert events[0].resolver == RESOLVER_RERERE
        assert events[0].tier == "mechanical"
        assert events[0].path == "f.txt"

    def test_rerere_false_disables_replay_and_never_writes_git_config(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        repo, git, outcome_paths = _teach_then_replay(tmp_path, rerere=False)
        # With rerere disabled on this GitRepo instance, replay never happens: the
        # rebase stops with the file genuinely, still conflicted.
        assert outcome_paths == ["f.txt"]
        config_path = repo / ".git" / "config"
        before = config_path.read_bytes()
        with caplog.at_level("INFO", logger="agent_orchestrator.isolation.resolvers"):
            unresolved = resolve_mechanically(
                outcome_paths,
                worktree=str(repo),
                git=git,
                config=ResolverConfig(rerere=False),
                env=_env(),
            )
        assert unresolved == ["f.txt"]
        assert config_path.read_bytes() == before  # never persisted anything
        rerere_events = [
            r
            for r in caplog.records
            if getattr(r, "event", None) == "integration.resolved" and r.resolver == RESOLVER_RERERE
        ]
        assert rerere_events == []


# ---------------------------------------------------------------------------------------
# Non-conflicted files are never touched
# ---------------------------------------------------------------------------------------


class TestNoCollateralDamage:
    def test_bystander_file_untouched(self, tmp_path: Path) -> None:
        repo, base_sha, ours, theirs = make_conflict_repo(tmp_path, "union")
        # Deliberately untracked and uncommitted: `_rebase_conflict` below checks out the
        # `ours` branch (neither branch has this path in its history), and an untracked
        # file survives a `git checkout` between branches unless a checked-out branch
        # itself introduces the same path.
        (repo / "bystander.txt").write_text("untouched\n")
        before_hash = _hash(repo / "bystander.txt")
        git, outcome = _rebase_conflict(repo, base_sha, ours, theirs)
        resolve_mechanically(
            list(outcome.paths),
            worktree=str(repo),
            git=git,
            config=ResolverConfig(union=["f.txt"]),
            env=_env(),
        )
        assert _hash(repo / "bystander.txt") == before_hash


# ---------------------------------------------------------------------------------------
# Registry extension
# ---------------------------------------------------------------------------------------


class _EchoResolver(MechanicalResolver):
    name = "echo-test-only"

    def apply(self, step, *, worktree, git, env, cfg) -> bool:  # type: ignore[override]
        (Path(worktree) / step.path).write_text("echoed\n")
        git.add_paths(worktree, [step.path])
        return True


class TestRegistryExtension:
    def test_new_resolver_registers_and_dispatches_without_core_change(
        self, tmp_path: Path
    ) -> None:
        from agent_orchestrator.isolation.resolvers import register_resolver

        assert "echo-test-only" not in RESOLVER_REGISTRY
        register_resolver(_EchoResolver)
        try:
            assert RESOLVER_REGISTRY["echo-test-only"] is _EchoResolver
            repo, base_sha, ours, theirs = make_conflict_repo(tmp_path, "union")
            git, outcome = _rebase_conflict(repo, base_sha, ours, theirs)
            plan = ResolutionPlan(steps=[ResolutionStep("f.txt", "echo-test-only", None)])
            unresolved = apply_plan(plan, str(repo), git, _env(), ResolverConfig())
            assert unresolved == []
            assert (repo / "f.txt").read_text() == "echoed\n"
        finally:
            del RESOLVER_REGISTRY["echo-test-only"]

    def test_register_resolver_rejects_unnamed_class(self) -> None:
        from agent_orchestrator.isolation.resolvers import register_resolver

        class _Nameless(MechanicalResolver):
            name = ""

            def apply(self, step, *, worktree, git, env, cfg) -> bool:  # type: ignore[override]
                return False

        with pytest.raises(ValueError, match="name"):
            register_resolver(_Nameless)


# ---------------------------------------------------------------------------------------
# ResolverHook contract conformance (structural/typing)
# ---------------------------------------------------------------------------------------


class TestResolverHookConformance:
    def test_signature_matches_resolver_hook_protocol(self) -> None:
        hook_params = list(inspect.signature(ResolverHook.__call__).parameters.values())[
            1:
        ]  # drop self
        fn_params = list(inspect.signature(resolve_mechanically).parameters.values())
        assert [p.name for p in fn_params] == [p.name for p in hook_params]
        assert [p.kind for p in fn_params] == [p.kind for p in hook_params]

    def test_mypy_checked_assignment_is_valid_at_runtime_too(self) -> None:
        # This line is the real assertion: it is checked statically by `mypy` (a
        # `ResolverHook`-typed variable assigned a non-conforming callable is a type
        # error) every time `mypy src` runs over this file. The runtime half just proves
        # the assignment doesn't also blow up some other way.
        typed_hook: ResolverHook = resolve_mechanically
        assert typed_hook is resolve_mechanically

    def test_empty_conflicted_paths_is_a_trivial_noop(self) -> None:
        """review C-7: closes the one non-subprocess-exception coverage gap STATUS.md's
        prior characterization missed -- the empty-input guard, not a defensive branch."""
        assert (
            resolve_mechanically(
                [], worktree="/does-not-matter", git=None, config=ResolverConfig(), env={}
            )
            == []
        )  # type: ignore[arg-type]


# ---------------------------------------------------------------------------------------
# NFR-1: no direct repository-file reads
# ---------------------------------------------------------------------------------------


class TestNoDirectFileReads:
    def test_no_open_or_read_text_calls_in_source(self) -> None:
        tree = ast.parse(RESOLVERS_SRC.read_text())
        banned = {"open", "read_text"}
        offenders = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                name = None
                if isinstance(func, ast.Name):
                    name = func.id
                elif isinstance(func, ast.Attribute):
                    name = func.attr
                if name in banned:
                    offenders.append((name, node.lineno))
        assert offenders == [], f"forbidden direct file read(s) in resolvers.py: {offenders}"


# ---------------------------------------------------------------------------------------
# Local test-only git helpers (mirrors `conftest.py`'s own `_git`/`_commit_env` pattern --
# kept private to this file rather than touching the locked `conftest.py` builders)
# ---------------------------------------------------------------------------------------


def _env() -> dict[str, str]:
    return {"PATH": os.environ.get("PATH", "")}


def _commit_env() -> dict[str, str]:
    return {
        "GIT_AUTHOR_NAME": GIT_AUTHOR_NAME,
        "GIT_AUTHOR_EMAIL": GIT_AUTHOR_EMAIL,
        "GIT_COMMITTER_NAME": GIT_AUTHOR_NAME,
        "GIT_COMMITTER_EMAIL": GIT_AUTHOR_EMAIL,
        "GIT_AUTHOR_DATE": "2020-01-01T00:00:00Z",
        "GIT_COMMITTER_DATE": "2020-01-01T00:00:00Z",
    }


def _raw_git(args: list[str], cwd: Path, env: dict[str, str] | None = None) -> str:
    full_env = {**os.environ, **(env or {})}
    result = subprocess.run(
        [
            "git",
            "-c",
            f"user.name={GIT_AUTHOR_NAME}",
            "-c",
            f"user.email={GIT_AUTHOR_EMAIL}",
            "-c",
            "core.autocrlf=false",
            *args,
        ],
        cwd=cwd,
        capture_output=True,
        text=True,
        env=full_env,
        check=False,
    )
    assert result.returncode == 0, f"git {args} failed (exit {result.returncode}): {result.stderr}"
    return result.stdout.strip()


def _checkout(repo: Path, branch: str) -> None:
    _raw_git(["checkout", "-q", branch], repo)


def _branch(
    repo: Path,
    name: str,
    files: dict[str, str],
    message: str | None,
    *,
    from_branch: str = "main",
) -> None:
    _checkout(repo, from_branch)
    _raw_git(["checkout", "-q", "-b", name], repo)
    for rel_path, content in files.items():
        (repo / rel_path).write_text(content)
    if message is not None:
        _raw_git(["add", "-A"], repo)
        _raw_git(["commit", "-q", "-m", message], repo, env=_commit_env())


def _hash(path: Path) -> bytes:
    import hashlib

    return hashlib.sha256(path.read_bytes()).digest()
