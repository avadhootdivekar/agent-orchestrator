"""Tests for `agent_orchestrator.isolation.git` (E-Wk9Tz3 T-Gt4Pw8).

Two families:

- Unit tests over `RecordingFakeRunner` (no real git): argv construction, the
  forbidden-subcommand guard, timeout/unavailable-binary mapping, and the structural
  no-network-verb sweep over every public method (AC-2, AC-8, AC-9).
- Integration tests over real `git init` repos under `tmp_path` (`make_repo`/
  `make_conflict_repo`/`make_hooked_repo` from `conftest.py`): worktrees, the S-1
  planted-hook gate, refs/commits/status, and rebase.
"""

from __future__ import annotations

import inspect
import os
import subprocess
import time
from pathlib import Path

import pytest

import agent_orchestrator.isolation.git as git
from agent_orchestrator.errors import (
    GitError,
    GitForbiddenCommandError,
    GitTimeoutError,
    GitUnavailableError,
)
from agent_orchestrator.isolation.git import (
    FORBIDDEN_SUBCOMMANDS,
    GIT_MERGE_TREE_MIN_VERSION,
    SAFETY_ARGS,
    GitRepo,
    parse_worktree_list,
)

from .conftest import RecordingFakeRunner, make_conflict_repo, make_hooked_repo, make_repo, ok

# ---------------------------------------------------------------------------------------
# Unit tests: argv construction, forbidden guard, timeout/unavailable mapping
# ---------------------------------------------------------------------------------------


class TestRunArgvConstruction:
    def test_run_rejects_empty_args(self) -> None:
        fake = RecordingFakeRunner()
        repo = GitRepo("/repo", runner=fake)
        with pytest.raises(ValueError):
            repo._run([])

    def test_safety_args_precede_subcommand(self) -> None:
        fake = RecordingFakeRunner()
        repo = GitRepo("/repo", runner=fake, rerere=False)
        repo._run(["status", "--porcelain"])
        argv = fake.calls[0]["argv"]
        assert argv[0] == "git"
        assert argv[1] == "--no-pager"
        # hooksPath comes first, then SAFETY_ARGS verbatim, then the subcommand.
        assert argv[2] == "-c"
        assert argv[3].startswith("core.hooksPath=")
        assert argv[4 : 4 + len(SAFETY_ARGS)] == SAFETY_ARGS
        assert argv[4 + len(SAFETY_ARGS) :] == ["status", "--porcelain"]

    def test_rerere_args_included_when_enabled(self) -> None:
        fake = RecordingFakeRunner()
        repo = GitRepo("/repo", runner=fake, rerere=True)
        repo._run(["status"])
        argv = fake.calls[0]["argv"]
        assert "rerere.enabled=true" in argv
        assert "rerere.autoupdate=true" in argv

    def test_rerere_args_excluded_when_disabled(self) -> None:
        fake = RecordingFakeRunner()
        repo = GitRepo("/repo", runner=fake, rerere=False)
        repo._run(["status"])
        argv = fake.calls[0]["argv"]
        assert "rerere.enabled=true" not in argv

    def test_forced_child_env(self) -> None:
        fake = RecordingFakeRunner()
        repo = GitRepo("/repo", runner=fake)
        repo._run(["status"])
        env = fake.calls[0]["env"]
        assert env["LC_ALL"] == "C"
        assert env["GIT_EDITOR"] == "true"
        assert env["GIT_TERMINAL_PROMPT"] == "0"
        assert env["GIT_ASKPASS"] == ""

    def test_cwd_defaults_to_repo_path(self) -> None:
        fake = RecordingFakeRunner()
        repo = GitRepo("/my/repo", runner=fake)
        repo._run(["status"])
        assert fake.calls[0]["cwd"] == "/my/repo"

    def test_explicit_cwd_overrides_default(self) -> None:
        fake = RecordingFakeRunner()
        repo = GitRepo("/my/repo", runner=fake)
        repo._run(["status"], cwd="/other/worktree")
        assert fake.calls[0]["cwd"] == "/other/worktree"

    def test_check_false_returns_without_raising_on_nonzero_exit(self) -> None:
        fake = RecordingFakeRunner(responses=[ok(returncode=1, stderr=b"boom")])
        repo = GitRepo("/repo", runner=fake)
        cp = repo._run(["status"], check=False)
        assert cp.returncode == 1

    def test_check_true_raises_git_error_on_nonzero_exit(self) -> None:
        fake = RecordingFakeRunner(responses=[ok(returncode=128, stderr=b"fatal: boom")])
        repo = GitRepo("/repo", runner=fake)
        with pytest.raises(GitError) as excinfo:
            repo._run(["status"])
        assert excinfo.value.exit_code == 128
        assert "boom" in excinfo.value.stderr_tail

    def test_stderr_tail_truncated_to_4kib(self) -> None:
        long_stderr = ("x" * 10_000).encode()
        fake = RecordingFakeRunner(responses=[ok(returncode=1, stderr=long_stderr)])
        repo = GitRepo("/repo", runner=fake)
        with pytest.raises(GitError) as excinfo:
            repo._run(["status"])
        assert len(excinfo.value.stderr_tail.encode("utf-8")) <= git.GIT_STDERR_TAIL_BYTES


class TestForbiddenSubcommandGuard:
    @pytest.mark.parametrize("verb", sorted(FORBIDDEN_SUBCOMMANDS))
    def test_each_forbidden_verb_raises_before_any_subprocess(self, verb: str) -> None:
        fake = RecordingFakeRunner()
        repo = GitRepo("/repo", runner=fake)
        with pytest.raises(GitForbiddenCommandError):
            repo._run([verb, "origin"])
        assert fake.calls == []  # never reached the runner

    def test_allowed_subcommand_is_not_blocked(self) -> None:
        fake = RecordingFakeRunner()
        repo = GitRepo("/repo", runner=fake)
        repo._run(["status"])
        assert len(fake.calls) == 1

    def test_git_alias_defining_a_forbidden_verb_is_rejected(self) -> None:
        """C-8: `git -c alias.p=push p` must be refused wholesale -- the alias KEY
        (`alias.p`) is rejected regardless of what it points at, since a `-c
        alias.*=...` value can be arbitrarily shell-like and is not reliably parseable.
        """
        fake = RecordingFakeRunner()
        repo = GitRepo("/repo", runner=fake)
        with pytest.raises(GitForbiddenCommandError):
            repo._run(["-c", "alias.p=push", "p"])
        assert fake.calls == []

    def test_forbidden_verb_after_global_options_is_still_rejected(self) -> None:
        """C-8: `args[0]` alone is not `"push"` here (`args[0]` is `"-c"`) -- the guard
        must find the actual subcommand token past the global `-c key=value` option.
        """
        fake = RecordingFakeRunner()
        repo = GitRepo("/repo", runner=fake)
        with pytest.raises(GitForbiddenCommandError):
            repo._run(["-c", "foo.bar=baz", "push", "origin"])
        assert fake.calls == []

    def test_worktree_add_with_track_flag_is_not_rejected(self) -> None:
        """C-8 false-positive guard: an ordinary flag that is neither `-c`/`-C`/
        `--git-dir` (here `--track`) must not be mistaken for a subcommand token or
        otherwise trip the forbidden-verb guard.
        """
        fake = RecordingFakeRunner()
        repo = GitRepo("/repo", runner=fake)
        repo._run(["worktree", "add", "--track", "-b", "br", "/wt", "HEAD"])
        assert len(fake.calls) == 1

    def test_global_dash_c_option_value_is_skipped_not_treated_as_subcommand(self) -> None:
        fake = RecordingFakeRunner()
        repo = GitRepo("/repo", runner=fake)
        repo._run(["-c", "foo.bar=baz", "status"])
        assert len(fake.calls) == 1


class TestFindSubcommandAndAliasGuard:
    """Unit tests for the pure helpers `_find_subcommand`/`_forbidden_alias_key`
    directly (C-8) -- no runner involved."""

    def test_find_subcommand_skips_dash_c(self) -> None:
        assert git._find_subcommand(["-c", "foo.bar=baz", "status"]) == "status"

    def test_find_subcommand_skips_dash_capital_c(self) -> None:
        assert git._find_subcommand(["-C", "/some/path", "status"]) == "status"

    def test_find_subcommand_skips_git_dir_two_token_form(self) -> None:
        assert git._find_subcommand(["--git-dir", "/some/.git", "status"]) == "status"

    def test_find_subcommand_skips_git_dir_equals_form(self) -> None:
        assert git._find_subcommand(["--git-dir=/some/.git", "status"]) == "status"

    def test_find_subcommand_skips_ordinary_flags(self) -> None:
        assert git._find_subcommand(["--no-pager", "status"]) == "status"

    def test_find_subcommand_returns_none_when_only_global_options(self) -> None:
        assert git._find_subcommand(["-c", "foo.bar=baz"]) is None

    def test_forbidden_alias_key_detects_alias_definition(self) -> None:
        assert git._forbidden_alias_key(["-c", "alias.p=push", "p"]) == "alias.p"

    def test_forbidden_alias_key_ignores_non_alias_dash_c(self) -> None:
        assert git._forbidden_alias_key(["-c", "foo.bar=baz", "status"]) is None


class TestTimeoutAndUnavailableMapping:
    def test_timeout_expired_maps_to_git_timeout_error(self) -> None:
        fake = RecordingFakeRunner(
            responses=[subprocess.TimeoutExpired(cmd=["git", "status"], timeout=5)]
        )
        repo = GitRepo("/repo", runner=fake, timeout=5)
        with pytest.raises(GitTimeoutError) as excinfo:
            repo._run(["status"])
        assert excinfo.value.exit_code is None

    def test_missing_git_binary_maps_to_git_unavailable_error(self) -> None:
        fake = RecordingFakeRunner(responses=[FileNotFoundError("git: not found")])
        repo = GitRepo("/repo", runner=fake)
        with pytest.raises(GitUnavailableError):
            repo._run(["status"])

    def test_version_returns_none_when_git_absent_from_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        empty_bin = tmp_path / "empty-bin"
        empty_bin.mkdir()
        monkeypatch.setenv("PATH", str(empty_bin))
        assert GitRepo.version() is None

    def test_version_parses_real_git(self) -> None:
        version = GitRepo.version()
        assert version is not None
        assert version >= git.GIT_MIN_VERSION


class TestEmptyHooksDirResolution:
    """`hooks_dir` injection (architect Phase-2 routing): `GitRepo` must never require
    touching a real `$AO_STATE_DIR`/`~` in tests, either via direct injection or via the
    default `AO_STATE_DIR`-honoring resolution the autouse env fixture already redirects.
    """

    def test_default_resolution_honors_ao_state_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        state_dir = tmp_path / "custom-ao-state"
        monkeypatch.setenv("AO_STATE_DIR", str(state_dir))
        fake = RecordingFakeRunner()
        GitRepo("/repo", runner=fake)  # no explicit hooks_dir -> falls through to AO_STATE_DIR
        expected = state_dir / "empty-hooks"
        assert expected.is_dir()
        assert oct(expected.stat().st_mode)[-3:] == "700"

    def test_explicit_hooks_dir_overrides_env_resolution(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Point AO_STATE_DIR somewhere this test never touches, to prove the explicit
        # override wins and env resolution is never consulted.
        monkeypatch.setenv("AO_STATE_DIR", str(tmp_path / "unused-state-dir"))
        injected = tmp_path / "injected-hooks"
        fake = RecordingFakeRunner()
        repo = GitRepo("/repo", runner=fake, hooks_dir=injected)
        assert injected.is_dir()
        assert not (tmp_path / "unused-state-dir").exists()
        repo._run(["status"])
        argv = fake.calls[0]["argv"]
        assert f"core.hooksPath={injected}" in argv

    def test_non_empty_hooks_dir_raises(self, tmp_path: Path) -> None:
        suspicious = tmp_path / "suspicious-hooks"
        suspicious.mkdir()
        (suspicious / "pre-commit").write_text("#!/bin/sh\nexit 0\n")
        with pytest.raises(RuntimeError):
            git.resolve_empty_hooks_dir(suspicious)


class TestStructuralNoNetworkSurface:
    """AC-9: drive every public method once against the fake runner and assert no
    recorded argv ever contains a forbidden verb -- the structural half of S-1 that
    survives future edits (a new method calling a forbidden verb fails this test even
    if nobody writes a dedicated per-method test for it).
    """

    def test_public_method_surface_never_reaches_a_forbidden_verb(self) -> None:
        fake = RecordingFakeRunner(
            responses=[
                ok(stdout=b"true\nfalse\n.git\n"),  # probe-shaped, unused here
            ]
            * 200
        )
        repo = GitRepo("/repo", runner=fake)

        public_methods = [
            name
            for name, member in inspect.getmembers(GitRepo, predicate=inspect.isfunction)
            if not name.startswith("_")
        ]
        # Sanity: this test only means something if it actually covers methods.
        assert len(public_methods) >= 20

        # Drive each with plausible arguments; only argv-recording matters here, not
        # semantic correctness (each nonstandard/parsing failure is swallowed -- a
        # missing/malformed response never should surface a forbidden verb either way).
        call_args: dict[str, tuple[tuple, dict]] = {
            "version": ((), {}),
            "probe": (("/repo",), {}),
            "is_dirty": ((), {}),
            "current_branch": ((), {}),
            "rev_parse": (("HEAD",), {}),
            "is_ancestor": (("a", "b"), {}),
            "merge_tree_probe": (("a", "b"), {}),
            "worktree_add": (("/wt", "ao/x", "HEAD"), {}),
            "worktree_list": ((), {}),
            "worktree_remove": (("/wt",), {}),
            "prune_worktrees_scoped": (("/prefix",), {}),
            "update_ref_cas": (("refs/heads/x", "new", "old"), {}),
            "create_ref": (("refs/heads/x", "sha"), {}),
            "delete_ref": (("refs/heads/x",), {}),
            "branch_exists": (("x",), {}),
            "delete_branch": (("x",), {}),
            "list_refs": (("refs/heads/",), {}),
            "commit_tree": (("tree", "parent", "msg"), {}),
            "add_paths": (("/wt", ["a.txt"]), {}),
            "add_all": (("/wt",), {}),
            "status_porcelain": (("/wt",), {}),
            "commit": (("/wt", "msg"), {}),
            "reset_hard": (("/wt", "HEAD"), {}),
            "diff_names": (("/wt", "a", "b"), {}),
            "is_tracked": (("/wt", "f.txt"), {}),
            "ls_files_untracked_ignored": (("/wt", ["f.txt"]), {}),
            "rebase_onto": (("/wt", "onto", "upstream", "branch"), {}),
            "rebase_continue": (("/wt",), {}),
            "rebase_abort": (("/wt",), {}),
            "rebase_in_progress": (("/wt",), {}),
            "conflicted_paths": (("/wt",), {}),
            "show_stage": (("/wt", 1, "f.txt"), {}),
        }
        assert set(call_args) >= set(public_methods), (
            f"new public method(s) not exercised by this structural test: "
            f"{set(public_methods) - set(call_args)}"
        )

        for name in public_methods:
            method = getattr(repo, name)
            args, kwargs = call_args[name]
            try:
                method(*args, **kwargs)
            except Exception:  # noqa: BLE001 -- only argv capture matters, not the result
                pass

        # Simplest robust check: no forbidden verb ever appears anywhere in any recorded
        # argv at all (stronger than checking just the subcommand position).
        for argv in fake.argvs():
            for verb in FORBIDDEN_SUBCOMMANDS:
                assert verb not in argv, f"forbidden verb {verb!r} reachable via argv {argv}"


# ---------------------------------------------------------------------------------------
# Unit tests: parse_worktree_list (pure, over captured porcelain text)
# ---------------------------------------------------------------------------------------


class TestParseWorktreeList:
    def test_main_worktree_has_no_admin_dir(self) -> None:
        text = "worktree /repo\nHEAD abc123\nbranch refs/heads/main\n"
        entries = parse_worktree_list(text, "/repo/.git")
        assert len(entries) == 1
        assert entries[0].admin_dir is None
        assert entries[0].branch == "refs/heads/main"
        assert not entries[0].detached
        assert not entries[0].bare

    def test_linked_worktree_gets_derived_admin_dir(self) -> None:
        text = (
            "worktree /repo\nHEAD abc123\nbranch refs/heads/main\n"
            "\n"
            "worktree /wts/task1\nHEAD def456\nbranch refs/heads/ao/x\n"
        )
        entries = parse_worktree_list(text, "/repo/.git")
        assert entries[1].admin_dir == str(Path("/repo/.git") / "worktrees" / "task1")

    def test_detached_head_entry(self) -> None:
        text = (
            "worktree /repo\nHEAD abc123\nbranch refs/heads/main\n"
            "\n"
            "worktree /wts/detached\nHEAD def456\ndetached\n"
        )
        entries = parse_worktree_list(text, "/repo/.git")
        assert entries[1].detached is True
        assert entries[1].branch is None

    def test_locked_entry(self) -> None:
        text = (
            "worktree /repo\nHEAD abc123\nbranch refs/heads/main\n"
            "\n"
            "worktree /wts/locked\nHEAD def456\nbranch refs/heads/x\nlocked reason text\n"
        )
        entries = parse_worktree_list(text, "/repo/.git")
        assert entries[1].locked is True

    def test_prunable_entry(self) -> None:
        text = (
            "worktree /repo\nHEAD abc123\nbranch refs/heads/main\n"
            "\n"
            "worktree /wts/gone\nHEAD def456\ndetached\n"
            "prunable gitdir file points to non-existent location\n"
        )
        entries = parse_worktree_list(text, "/repo/.git")
        assert entries[1].prunable is True

    def test_bare_entry(self) -> None:
        text = "worktree /bare-repo.git\nbare\n"
        entries = parse_worktree_list(text, "/bare-repo.git")
        assert entries[0].bare is True
        assert entries[0].head is None
        assert entries[0].branch is None

    def test_empty_text_yields_no_entries(self) -> None:
        assert parse_worktree_list("", "/repo/.git") == []


# ---------------------------------------------------------------------------------------
# Integration tests: real git init repos under tmp_path
# ---------------------------------------------------------------------------------------


class TestProbe:
    def test_non_repo_directory_returns_none(self, tmp_path: Path) -> None:
        not_a_repo = tmp_path / "plain-dir"
        not_a_repo.mkdir()
        assert GitRepo.probe(str(not_a_repo)) is None

    def test_main_repo(self, tmp_path: Path) -> None:
        repo = make_repo(tmp_path)
        result = GitRepo.probe(str(repo))
        assert result is not None
        assert result.bare is False
        assert result.is_worktree is False
        assert os.path.normpath(result.toplevel) == os.path.normpath(str(repo))

    def test_bare_repo(self, tmp_path: Path) -> None:
        bare = tmp_path / "bare.git"
        subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
        result = GitRepo.probe(str(bare))
        assert result is not None
        assert result.bare is True
        assert result.is_worktree is False

    def test_linked_worktree_shares_common_dir_with_main(self, tmp_path: Path) -> None:
        repo = make_repo(tmp_path)
        main_probe = GitRepo.probe(str(repo))
        assert main_probe is not None
        g = GitRepo(str(repo))
        wt_path = tmp_path / "wt1"
        g.worktree_add(str(wt_path), "feature1", "HEAD")
        wt_probe = GitRepo.probe(str(wt_path))
        assert wt_probe is not None
        assert wt_probe.is_worktree is True
        assert os.path.normpath(wt_probe.toplevel) == os.path.normpath(str(wt_path))
        assert os.path.normpath(wt_probe.common_dir) == os.path.normpath(main_probe.common_dir)


class TestWorktreeOps:
    def test_add_list_remove_happy_path(self, tmp_path: Path) -> None:
        repo = make_repo(tmp_path)
        g = GitRepo(str(repo))
        wt_path = tmp_path / "wt1"
        g.worktree_add(str(wt_path), "ao/task1", "HEAD")

        entries = g.worktree_list()
        wt_norm = os.path.normpath(str(wt_path))
        assert any(os.path.normpath(e.path) == wt_norm for e in entries)
        entry = next(e for e in entries if os.path.normpath(e.path) == wt_norm)
        assert entry.branch == "refs/heads/ao/task1"
        assert entry.admin_dir is not None
        assert Path(entry.admin_dir).is_dir()

        outcome = g.worktree_remove(str(wt_path))
        assert outcome == "removed"
        assert not wt_path.exists()

    def test_admin_dir_resolution_survives_a_basename_collision(self, tmp_path: Path) -> None:
        """C-7 regression: `parse_worktree_list`'s pure basename-guess for `admin_dir`
        would be WRONG for the second of two worktrees sharing a basename under
        different parent dirs -- git disambiguates the on-disk admin dir with a numeric
        suffix (`repoA`, `repoA1`) but the naive guess can't know that from porcelain
        text alone. `_correct_admin_dirs` must fix it against the real
        `.../worktrees/*/gitdir` records before `prune_worktrees_scoped`'s destructive
        `rmtree` ever consumes it -- getting this wrong would delete the wrong repo's
        worktree registration.
        """
        repo = make_repo(tmp_path)
        g = GitRepo(str(repo))
        wt_a = tmp_path / "task1" / "repoA"
        wt_b = tmp_path / "task2" / "repoA"  # same basename, different parent
        g.worktree_add(str(wt_a), "ao/task1", "HEAD")
        g.worktree_add(str(wt_b), "ao/task2", "HEAD")

        entries = {os.path.normpath(e.path): e for e in g.worktree_list()}
        entry_a = entries[os.path.normpath(str(wt_a))]
        entry_b = entries[os.path.normpath(str(wt_b))]

        assert entry_a.admin_dir is not None
        assert entry_b.admin_dir is not None
        assert entry_a.admin_dir != entry_b.admin_dir
        # Each resolved admin dir's own `gitdir` record must point back at the correct
        # worktree -- proves the correction, not just "they differ".
        assert Path(entry_a.admin_dir, "gitdir").read_text().strip() == str(wt_a / ".git")
        assert Path(entry_b.admin_dir, "gitdir").read_text().strip() == str(wt_b / ".git")

    def test_remove_already_absent_never_raises(self, tmp_path: Path) -> None:
        repo = make_repo(tmp_path)
        g = GitRepo(str(repo))
        outcome = g.worktree_remove(str(tmp_path / "never-existed"))
        assert outcome == "already_absent"

    def test_remove_locked_never_raises(self, tmp_path: Path) -> None:
        repo = make_repo(tmp_path)
        g = GitRepo(str(repo))
        wt_path = tmp_path / "wt-locked"
        g.worktree_add(str(wt_path), "ao/locked", "HEAD")
        g._run(["worktree", "lock", str(wt_path)])
        assert g.worktree_remove(str(wt_path)) == "locked"

    def test_remove_in_use_when_dirty_without_force(self, tmp_path: Path) -> None:
        repo = make_repo(tmp_path)
        g = GitRepo(str(repo))
        wt_path = tmp_path / "wt-dirty"
        g.worktree_add(str(wt_path), "ao/dirty", "HEAD")
        (wt_path / "uncommitted.txt").write_text("dirty\n")
        assert g.worktree_remove(str(wt_path)) == "in_use"
        # force removes it
        assert g.worktree_remove(str(wt_path), force=True) == "removed"

    def test_remove_deleted_directory_with_force(self, tmp_path: Path) -> None:
        import shutil

        repo = make_repo(tmp_path)
        g = GitRepo(str(repo))
        wt_path = tmp_path / "wt-gone"
        g.worktree_add(str(wt_path), "ao/gone", "HEAD")
        shutil.rmtree(wt_path)
        assert g.worktree_remove(str(wt_path), force=True) == "removed"

    def test_remove_raises_for_unexpected_failure(self) -> None:
        porcelain = "worktree /repo/wt1\nHEAD abc123\nbranch refs/heads/x\n"
        fake = RecordingFakeRunner(
            responses=[
                ok(stdout=b".git\n"),  # rev-parse --git-common-dir
                ok(stdout=porcelain.encode()),  # worktree list --porcelain
                ok(returncode=1, stderr=b"fatal: something unexpected"),  # worktree remove
            ]
        )
        repo = GitRepo("/repo", runner=fake)
        with pytest.raises(GitError):
            repo.worktree_remove("/repo/wt1")

    def test_prune_scoped_global_mode_when_no_foreign_prunable(self, tmp_path: Path) -> None:
        import shutil

        repo = make_repo(tmp_path)
        g = GitRepo(str(repo))
        ours_root = tmp_path / "ao-worktrees"
        wt_path = ours_root / "task1"
        g.worktree_add(str(wt_path), "ao/task1", "HEAD")
        shutil.rmtree(wt_path)  # now prunable

        report = g.prune_worktrees_scoped(str(ours_root))
        assert report.mode == "global"
        assert report.skipped_foreign == []
        wt_norm = os.path.normpath(str(wt_path))
        remaining = [e for e in g.worktree_list() if os.path.normpath(e.path) == wt_norm]
        assert remaining == []

    def test_prune_scoped_never_deregisters_foreign_worktree(self, tmp_path: Path) -> None:
        """The R-6 gate: a foreign (user-created) worktree must survive
        `prune_worktrees_scoped`, even when it is itself prunable."""
        import shutil

        repo = make_repo(tmp_path)
        g = GitRepo(str(repo))

        ours_root = tmp_path / "ao-worktrees"
        ao_wt = ours_root / "task1"
        g.worktree_add(str(ao_wt), "ao/task1", "HEAD")
        shutil.rmtree(ao_wt)

        foreign_root = tmp_path / "user-worktrees"
        foreign_wt = foreign_root / "manual"
        g.worktree_add(str(foreign_wt), "manual-branch", "HEAD")
        shutil.rmtree(foreign_wt)

        report = g.prune_worktrees_scoped(str(ours_root))
        assert report.mode == "scoped"
        assert os.path.normpath(str(foreign_wt)) in [
            os.path.normpath(p) for p in report.skipped_foreign
        ]

        remaining = g.worktree_list()
        remaining_paths = [os.path.normpath(e.path) for e in remaining]
        assert os.path.normpath(str(ao_wt)) not in remaining_paths  # ao's own entry is gone
        assert os.path.normpath(str(foreign_wt)) in remaining_paths  # foreign entry survives

    def test_worktree_path_with_space_and_non_ascii_char(self, tmp_path: Path) -> None:
        """C-10: argv is always a list, never a shell (`noqa: S603`), so this is expected
        to just work -- a real task/run id could plausibly produce such a path.
        """
        repo = make_repo(tmp_path)
        g = GitRepo(str(repo))
        wt_path = tmp_path / "task ünïcödé 1"
        g.worktree_add(str(wt_path), "ao/space-unicode", "HEAD")

        entries = {os.path.normpath(e.path): e for e in g.worktree_list()}
        assert os.path.normpath(str(wt_path)) in entries

        assert g.worktree_remove(str(wt_path)) == "removed"
        assert not wt_path.exists()


class TestBranchOps:
    def test_branch_exists_true_and_false(self, tmp_path: Path) -> None:
        repo = make_repo(tmp_path)
        g = GitRepo(str(repo))
        g.create_ref("refs/heads/exists-branch", g.rev_parse("HEAD"))
        assert g.branch_exists("exists-branch") is True
        assert g.branch_exists("does-not-exist") is False

    def test_delete_branch_outcomes(self, tmp_path: Path) -> None:
        repo = make_repo(tmp_path)
        g = GitRepo(str(repo))
        g.create_ref("refs/heads/to-delete", g.rev_parse("HEAD"))
        assert g.delete_branch("to-delete") is True
        assert g.branch_exists("to-delete") is False
        # Deleting an absent branch never raises.
        assert g.delete_branch("to-delete") is False
        assert g.delete_branch("never-existed") is False

    def test_delete_branch_raises_for_unexpected_failure(self) -> None:
        # exists -> True, `branch -d` fails for an unrelated reason, still exists after
        # (not a race) -> a genuinely unexpected failure must raise, not silently return
        # False (which would misreport it as "never existed").
        fake = RecordingFakeRunner(
            responses=[
                ok(returncode=0),
                ok(returncode=1, stderr=b"error: unexpected failure"),
                ok(returncode=0),
            ]
        )
        repo = GitRepo("/repo", runner=fake)
        with pytest.raises(GitError):
            repo.delete_branch("some-branch")


class TestRefsAndCommits:
    def test_update_ref_cas_success(self, tmp_path: Path) -> None:
        repo = make_repo(tmp_path)
        g = GitRepo(str(repo))
        base = g.rev_parse("HEAD")
        g.create_ref("refs/heads/cas-ref", base)
        (repo / "f.txt").write_text("changed\n")
        g.add_all(str(repo))
        new = g.commit(str(repo), "second")
        assert new is not None
        assert g.update_ref_cas("refs/heads/cas-ref", new, base) is True
        assert g.rev_parse("refs/heads/cas-ref") == new

    def test_update_ref_cas_fails_when_ref_moved(self, tmp_path: Path) -> None:
        repo = make_repo(tmp_path)
        g = GitRepo(str(repo))
        base = g.rev_parse("HEAD")
        g.create_ref("refs/heads/cas-target", base)

        (repo / "f.txt").write_text("changed\n")
        g.add_all(str(repo))
        new = g.commit(str(repo), "second")
        assert new is not None
        # Move the ref out from under a stale reader.
        assert g.update_ref_cas("refs/heads/cas-target", new, base) is True

        # Now attempt a CAS using the now-stale `base` as expected_old -> loss, not error.
        (repo / "f.txt").write_text("changed again\n")
        g.add_all(str(repo))
        newer = g.commit(str(repo), "third")
        assert newer is not None
        assert g.update_ref_cas("refs/heads/cas-target", newer, base) is False
        # The ref did not move.
        assert g.rev_parse("refs/heads/cas-target") == new

    def test_update_ref_cas_raises_for_unexpected_failure(self) -> None:
        fake = RecordingFakeRunner(
            responses=[ok(returncode=1, stderr=b"error: something else entirely")]
        )
        repo = GitRepo("/repo", runner=fake)
        with pytest.raises(GitError):
            repo.update_ref_cas("refs/heads/x", "new", "old")

    def test_commit_tree_is_deterministic_with_pinned_dates(self, tmp_path: Path) -> None:
        repo = make_repo(tmp_path)
        g = GitRepo(str(repo))
        tree = g._run(["write-tree"]).stdout.decode().strip()
        parent = g.rev_parse("HEAD")
        assert parent is not None
        sha1 = g.commit_tree(tree, parent, "squash message")
        sha2 = g.commit_tree(tree, parent, "squash message")
        assert sha1 == sha2

    def test_commit_tree_deterministic_across_a_wallclock_second_boundary(
        self, tmp_path: Path
    ) -> None:
        """C-1 regression: `commit_tree` used to embed the current wall-clock second
        into the commit object, so two calls straddling a one-second boundary could
        yield different shas for identical (tree, parent, message) -- reproduced by the
        review with a 1.1s delay between calls. Deterministic BY CONSTRUCTION now (dates
        pinned from `parent`'s own recorded committer date), so this must hold
        regardless of real elapsed time between the two calls.
        """
        repo = make_repo(tmp_path)
        g = GitRepo(str(repo))
        tree = g._run(["write-tree"]).stdout.decode().strip()
        parent = g.rev_parse("HEAD")
        assert parent is not None
        sha1 = g.commit_tree(tree, parent, "squash message")
        time.sleep(1.1)  # straddle a real wall-clock second boundary
        sha2 = g.commit_tree(tree, parent, "squash message")
        assert sha1 == sha2

    def test_commit_tree_explicit_identity_and_date_override_the_default(
        self, tmp_path: Path
    ) -> None:
        repo = make_repo(tmp_path)
        g = GitRepo(str(repo))
        tree = g._run(["write-tree"]).stdout.decode().strip()
        parent = g.rev_parse("HEAD")
        assert parent is not None
        sha = g.commit_tree(
            tree,
            parent,
            "squash message",
            author_name="custom",
            author_email="custom@example.invalid",
            author_date="2020-06-01T00:00:00Z",
            committer_date="2020-06-01T00:00:00Z",
        )
        show = g._run(["show", "-s", "--format=%an <%ae> %aI", sha]).stdout.decode()
        assert "custom <custom@example.invalid>" in show
        assert "2020-06-01" in show

    def test_commit_returns_none_when_nothing_staged(self, tmp_path: Path) -> None:
        repo = make_repo(tmp_path)
        g = GitRepo(str(repo))
        assert g.commit(str(repo), "no-op") is None

    def test_commit_returns_none_for_tracked_but_unstaged_change(self, tmp_path: Path) -> None:
        """C-2 regression: `commit()` used to check `status_porcelain(untracked=False)`,
        which reports every tracked delta from HEAD whether or not it was staged -- a
        tracked file modified but never `git add`-ed looked like "something to commit"
        and `commit()` would attempt (and `git commit` itself would then reject) an
        empty-index commit, raising `GitError` instead of returning `None`.
        """
        repo = make_repo(tmp_path, files={"f.txt": "base\n"})
        g = GitRepo(str(repo))
        (repo / "f.txt").write_text("changed but never staged\n")
        assert g.commit(str(repo), "no-op") is None
        # The file is genuinely still unstaged -- confirms this isn't accidentally
        # passing because there was nothing to stage in the first place.
        entries = {e.path: e for e in g.status_porcelain(str(repo))}
        assert entries["f.txt"].worktree == "M"
        assert entries["f.txt"].index == " "

    def test_commit_allow_empty_creates_commit(self, tmp_path: Path) -> None:
        repo = make_repo(tmp_path)
        g = GitRepo(str(repo))
        sha = g.commit(str(repo), "empty ok", allow_empty=True)
        assert sha is not None
        assert sha != g.rev_parse("HEAD~1")

    def test_add_all_respects_gitignore(self, tmp_path: Path) -> None:
        repo = make_repo(tmp_path, files={"f.txt": "base\n", ".gitignore": "ignored.txt\n"})
        g = GitRepo(str(repo))
        (repo / "ignored.txt").write_text("secret\n")
        (repo / "tracked-change.txt").write_text("new\n")
        g.add_all(str(repo))
        entries = g.status_porcelain(str(repo))
        paths = {e.path for e in entries}
        assert "tracked-change.txt" in paths
        assert "ignored.txt" not in paths

    def test_add_paths_stages_exactly_given_paths(self, tmp_path: Path) -> None:
        repo = make_repo(tmp_path, files={"f.txt": "base\n"})
        g = GitRepo(str(repo))
        (repo / "a.txt").write_text("a\n")
        (repo / "b.txt").write_text("b\n")
        g.add_paths(str(repo), ["a.txt"])
        entries = {e.path: e for e in g.status_porcelain(str(repo))}
        assert entries["a.txt"].index == "A"
        # b.txt remains untracked -- never staged.
        assert entries["b.txt"].index == "?"
        assert entries["b.txt"].worktree == "?"

    def test_add_paths_with_empty_list_is_a_noop(self) -> None:
        fake = RecordingFakeRunner()
        repo = GitRepo("/repo", runner=fake)
        repo.add_paths("/wt", [])
        assert fake.calls == []

    def test_status_porcelain_distinguishes_states(self, tmp_path: Path) -> None:
        repo = make_repo(tmp_path, files={"f.txt": "base\n", "to_delete.txt": "x\n"})
        g = GitRepo(str(repo))
        g._run(["mv", "f.txt", "renamed.txt"])
        (repo / "to_delete.txt").unlink()
        (repo / "untracked.txt").write_text("new\n")
        g.add_all(str(repo))
        # add_all won't re-add the deletion of an already-removed file without -A having
        # staged it -- it does, since `-A` stages deletions too.
        entries = {e.path: e for e in g.status_porcelain(str(repo))}
        assert "renamed.txt" in entries
        assert entries["renamed.txt"].index == "R"

    def test_status_porcelain_untracked_toggle(self, tmp_path: Path) -> None:
        repo = make_repo(tmp_path, files={"f.txt": "base\n"})
        g = GitRepo(str(repo))
        (repo / "f.txt").write_text("changed\n")
        (repo / "untracked.txt").write_text("new\n")
        with_untracked = g.status_porcelain(str(repo), untracked=True)
        without_untracked = g.status_porcelain(str(repo), untracked=False)
        assert any(e.path == "untracked.txt" for e in with_untracked)
        assert not any(e.path == "untracked.txt" for e in without_untracked)
        assert any(e.path == "f.txt" for e in without_untracked)

    def test_is_dirty_ignores_untracked(self, tmp_path: Path) -> None:
        repo = make_repo(tmp_path)
        g = GitRepo(str(repo))
        (repo / "untracked.txt").write_text("new\n")
        assert g.is_dirty() is False
        (repo / "f.txt").write_text("changed\n")
        assert g.is_dirty() is True

    def test_list_refs(self, tmp_path: Path) -> None:
        repo = make_repo(tmp_path)
        g = GitRepo(str(repo))
        head = g.rev_parse("HEAD")
        g.create_ref("refs/ao/runs/run1/task1/squash-1", head)
        refs = g.list_refs("refs/ao/runs/run1/")
        assert refs["refs/ao/runs/run1/task1/squash-1"] == head

    def test_diff_names_and_is_tracked(self, tmp_path: Path) -> None:
        repo = make_repo(tmp_path, files={"f.txt": "base\n"})
        g = GitRepo(str(repo))
        base = g.rev_parse("HEAD")
        (repo / "f.txt").write_text("changed\n")
        g.add_all(str(repo))
        new = g.commit(str(repo), "change")
        assert new is not None
        assert g.diff_names(str(repo), base, new) == ["f.txt"]
        assert g.is_tracked(str(repo), "f.txt") is True
        assert g.is_tracked(str(repo), "nope.txt") is False

    def test_ls_files_untracked_ignored(self, tmp_path: Path) -> None:
        repo = make_repo(tmp_path, files={"f.txt": "base\n", ".gitignore": "secret.env\n"})
        g = GitRepo(str(repo))
        (repo / "secret.env").write_text("shh\n")
        (repo / "plain.txt").write_text("not ignored\n")
        result = g.ls_files_untracked_ignored(str(repo), ["secret.env", "plain.txt"])
        assert result == {"secret.env"}

    def test_ls_files_untracked_ignored_with_empty_list_is_a_noop(self) -> None:
        fake = RecordingFakeRunner()
        repo = GitRepo("/repo", runner=fake)
        assert repo.ls_files_untracked_ignored("/wt", []) == set()
        assert fake.calls == []

    def test_reset_hard(self, tmp_path: Path) -> None:
        repo = make_repo(tmp_path, files={"f.txt": "base\n"})
        g = GitRepo(str(repo))
        base = g.rev_parse("HEAD")
        (repo / "f.txt").write_text("changed\n")
        g.add_all(str(repo))
        g.commit(str(repo), "change")
        g.reset_hard(str(repo), base)
        assert g.rev_parse("HEAD") == base
        assert (repo / "f.txt").read_text() == "base\n"


class TestRebase:
    def test_clean_rebase(self, tmp_path: Path) -> None:
        repo, base_sha, ours, theirs = make_conflict_repo(tmp_path, "clean")
        g = GitRepo(str(repo))
        g._run(["checkout", ours])
        outcome = g.rebase_onto(str(repo), theirs, base_sha, ours)
        assert outcome.clean is True
        assert outcome.paths == []
        assert g.rebase_in_progress(str(repo)) is False

    def test_true_conflict_rebase(self, tmp_path: Path) -> None:
        repo, base_sha, ours, theirs = make_conflict_repo(tmp_path, "true_conflict")
        g = GitRepo(str(repo))
        g._run(["checkout", ours])
        outcome = g.rebase_onto(str(repo), theirs, base_sha, ours)
        assert outcome.clean is False
        assert outcome.paths == ["f.txt"]
        assert g.rebase_in_progress(str(repo)) is True
        assert g.conflicted_paths(str(repo)) == ["f.txt"]
        g.rebase_abort(str(repo))
        assert g.rebase_in_progress(str(repo)) is False

    def test_rebase_continue_resolves_and_completes(self, tmp_path: Path) -> None:
        repo, base_sha, ours, theirs = make_conflict_repo(tmp_path, "true_conflict")
        g = GitRepo(str(repo))
        g._run(["checkout", ours])
        outcome = g.rebase_onto(str(repo), theirs, base_sha, ours)
        assert outcome.clean is False
        (repo / "f.txt").write_text("resolved\nb\nc\n")
        g.add_all(str(repo))
        result = g.rebase_continue(str(repo))
        assert result.clean is True
        assert g.rebase_in_progress(str(repo)) is False

    def test_rebase_onto_raises_git_error_for_non_conflict_failure(self, tmp_path: Path) -> None:
        repo = make_repo(tmp_path)
        g = GitRepo(str(repo))
        with pytest.raises(GitError):
            g.rebase_onto(str(repo), "not-a-real-ref", "also-not-real", "main")

    def test_rebase_continue_raises_git_error_when_no_rebase_in_progress(
        self, tmp_path: Path
    ) -> None:
        repo = make_repo(tmp_path)
        g = GitRepo(str(repo))
        with pytest.raises(GitError):
            g.rebase_continue(str(repo))

    def test_show_stage_returns_bytes_for_existing_stage_and_none_for_absent(
        self, tmp_path: Path
    ) -> None:
        repo, base_sha, ours, theirs = make_conflict_repo(tmp_path, "true_conflict")
        g = GitRepo(str(repo))
        g._run(["checkout", ours])
        g.rebase_onto(str(repo), theirs, base_sha, ours)
        assert g.show_stage(str(repo), 1, "f.txt") == b"a\nb\nc\n"
        assert g.show_stage(str(repo), 1, "does-not-exist.txt") is None
        g.rebase_abort(str(repo))

    def test_show_stage_delete_modify_conflict(self, tmp_path: Path) -> None:
        repo, base_sha, ours, theirs = make_conflict_repo(tmp_path, "delete_modify")
        g = GitRepo(str(repo))
        g._run(["checkout", ours])
        outcome = g.rebase_onto(str(repo), theirs, base_sha, ours)
        assert outcome.clean is False
        # During a rebase, stage 2 is the tree being rebased ONTO (here: `theirs`, which
        # modified f.txt) and stage 3 is the replayed commit (here: `ours`, which
        # deleted it) -- git's rebase labeling of stage 2/3 is the inverse of a plain
        # merge's "ours"/"theirs".
        assert g.show_stage(str(repo), 2, "f.txt") is not None
        assert g.show_stage(str(repo), 3, "f.txt") is None
        g.rebase_abort(str(repo))


class TestMergeTreeProbeVersionGate:
    """C-3 review fix: AC-22's "returns `None` when `git < 2.38`" branch, deliberately
    asserted (not just incidentally exercised by an unrelated structural test). Uses the
    fake runner so this holds regardless of the real installed git's version.
    """

    def test_returns_none_below_min_version(self) -> None:
        fake = RecordingFakeRunner(responses=[ok(stdout=b"git version 2.37.0\n")])
        repo = GitRepo("/repo", runner=fake)
        assert repo.merge_tree_probe("a", "b") is None
        # Only the version probe ran -- `merge-tree` itself must never be invoked.
        assert len(fake.calls) == 1
        assert fake.calls[0]["argv"] == ["git", "--version"]


@pytest.mark.skipif(
    GitRepo.version() is None or GitRepo.version() < GIT_MERGE_TREE_MIN_VERSION,
    reason=f"requires git >= {GIT_MERGE_TREE_MIN_VERSION}",
)
class TestMergeTreeProbe:
    def test_clean_probe(self, tmp_path: Path) -> None:
        repo, base_sha, ours, theirs = make_conflict_repo(tmp_path, "clean")
        g = GitRepo(str(repo))
        probe = g.merge_tree_probe(f"refs/heads/{ours}", f"refs/heads/{theirs}")
        assert probe is not None
        assert probe.clean is True

    def test_conflicting_probe(self, tmp_path: Path) -> None:
        repo, base_sha, ours, theirs = make_conflict_repo(tmp_path, "true_conflict")
        g = GitRepo(str(repo))
        probe = g.merge_tree_probe(f"refs/heads/{ours}", f"refs/heads/{theirs}")
        assert probe is not None
        assert probe.clean is False
        assert probe.paths == ["f.txt"]


class TestHooksAndSafetyS1:
    """The S-1 blocking gate: no repo-local hook ever fires from an engine-issued call,
    proven non-vacuous by showing the same raw commands DO fire the hooks.
    """

    def test_planted_hooks_never_fire_through_gitrepo(self, tmp_path: Path) -> None:
        repo = make_hooked_repo(tmp_path)
        sentinel_dir = repo / ".hook-sentinels"
        g = GitRepo(str(repo))

        wt_path = tmp_path / "wt-hooks"
        g.worktree_add(str(wt_path), "ao/hooks", "HEAD")  # fires post-checkout if unsafe

        (wt_path / "f.txt").write_text("changed\n")
        g.add_all(str(wt_path))
        sha = g.commit(str(wt_path), "change")  # fires pre-commit/commit-msg/post-commit
        assert sha is not None

        base = g.rev_parse("HEAD")
        outcome = g.rebase_onto(str(wt_path), base, base, "ao/hooks")  # no-op rebase
        assert outcome.clean is True

        assert not sentinel_dir.exists() or list(sentinel_dir.iterdir()) == []

    def test_raw_subprocess_proves_fixture_is_non_vacuous(self, tmp_path: Path) -> None:
        """Same repo, same class of commands, but WITHOUT GitRepo -- the sentinels must
        appear, or the planted-hook fixture (and therefore the test above) is worthless.
        """
        repo = make_hooked_repo(tmp_path)
        sentinel_dir = repo / ".hook-sentinels"

        def _raw(args: list[str]) -> None:
            subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=False)

        _raw(["checkout", "-q", "-b", "raw-branch"])  # post-checkout
        (repo / "f.txt").write_text("raw change\n")
        _raw(["add", "-A"])
        _raw(["commit", "-q", "-m", "raw commit"])  # pre-commit/commit-msg/post-commit

        assert sentinel_dir.is_dir()
        fired = {p.name for p in sentinel_dir.iterdir()}
        assert "post-checkout" in fired
        assert "pre-commit" in fired
        assert "commit-msg" in fired
        assert "post-commit" in fired

    def test_no_config_mutation_after_full_sequence(self, tmp_path: Path) -> None:
        repo = make_repo(tmp_path)
        g = GitRepo(str(repo))
        wt_path = tmp_path / "wt-config"
        g.worktree_add(str(wt_path), "ao/config", "HEAD")
        (wt_path / "f.txt").write_text("changed\n")
        g.add_all(str(wt_path))
        g.commit(str(wt_path), "change")
        base = g.rev_parse("HEAD")
        g.rebase_onto(str(wt_path), base, base, "ao/config")
        g.update_ref_cas("refs/heads/config-check", base, base)

        cp = subprocess.run(
            ["git", "config", "--local", "--get-regexp", ".*"],
            cwd=repo,
            capture_output=True,
            text=True,
            check=False,
        )
        forbidden_keys = (
            "rerere.enabled",
            "core.hookspath",
            "commit.gpgsign",
            "core.editor",
            "gc.auto",
        )
        local_config = cp.stdout.lower()
        for key in forbidden_keys:
            assert key not in local_config

    def test_signing_configured_repo_does_not_hang_or_fail(self, tmp_path: Path) -> None:
        repo = make_repo(tmp_path)
        subprocess.run(["git", "config", "commit.gpgsign", "true"], cwd=repo, check=True)
        subprocess.run(["git", "config", "gpg.program", "/bin/false"], cwd=repo, check=True)
        g = GitRepo(str(repo), timeout=10)
        (repo / "f.txt").write_text("changed\n")
        g.add_all(str(repo))
        sha = g.commit(str(repo), "signed?")
        assert sha is not None

    def _plant_hostile_hook(self, hooks_dir: Path, sentinel_path: Path) -> None:
        hooks_dir.mkdir(parents=True, exist_ok=True)
        hook = hooks_dir / "post-checkout"
        hook.write_text(f"#!/bin/sh\ntouch '{sentinel_path}'\nexit 0\n")
        hook.chmod(0o755)

    def test_safety_args_hookspath_wins_over_repo_local_hostile_config(
        self, tmp_path: Path
    ) -> None:
        """C-4 review fix: `-c core.hooksPath=<empty dir>` must win even when the repo
        ALREADY has a (hostile) repo-local `core.hooksPath` pointing at a real hook.
        """
        repo = make_repo(tmp_path)
        sentinel = tmp_path / "HOSTILE-LOCAL-FIRED"
        self._plant_hostile_hook(tmp_path / "hostile-local-hooks", sentinel)
        subprocess.run(
            ["git", "config", "core.hooksPath", str(tmp_path / "hostile-local-hooks")],
            cwd=repo,
            check=True,
        )

        g = GitRepo(str(repo))
        g.worktree_add(str(tmp_path / "wt-hostile-local"), "ao/hostile-local", "HEAD")
        assert not sentinel.exists()

    def test_safety_args_hookspath_wins_over_global_hostile_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Same guarantee, but the hostile `core.hooksPath` is set in the GLOBAL config
        (`GIT_CONFIG_GLOBAL` pointed at a tmp file, per the review's exact scenario) --
        proves `-c` wins over a global config too, not only a repo-local one.
        """
        repo = make_repo(tmp_path)
        sentinel = tmp_path / "HOSTILE-GLOBAL-FIRED"
        self._plant_hostile_hook(tmp_path / "hostile-global-hooks", sentinel)
        global_config = tmp_path / "hostile-gitconfig-global"
        global_config.write_text(f"[core]\n\thooksPath = {tmp_path / 'hostile-global-hooks'}\n")
        monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_config))

        g = GitRepo(str(repo))
        g.worktree_add(str(tmp_path / "wt-hostile-global"), "ao/hostile-global", "HEAD")
        assert not sentinel.exists()
