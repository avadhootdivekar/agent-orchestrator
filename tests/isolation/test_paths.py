"""Tests for `agent_orchestrator.isolation.paths` (E-Wk9Tz3 T-Wk3Nv6).

Three families:
- Pure unit tests (no git, no filesystem) over naming/key functions and `effective_path`.
- A property-style corpus test pinning `sanitize_ref_component` against the real
  `git check-ref-format --allow-onelevel` (AC-2).
- A small integration test proving `task_branch`/`integration_branch` coexist as real refs
  with no git dir/file (D/F) ref conflict (AC-4).
"""

from __future__ import annotations

import importlib
import string
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from agent_orchestrator.isolation import paths
from agent_orchestrator.isolation.git import GitRepo

from .conftest import make_repo


@pytest.fixture(autouse=True)
def _isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No test touches a real `~`/`$AO_STATE_DIR` (TASK.md AC-5)."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("AO_STATE_DIR", raising=False)
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.delenv("AO_WORKTREE_ROOT", raising=False)


# ---------------------------------------------------------------------------------------
# Module import-safety (AC-1)
# ---------------------------------------------------------------------------------------


class TestImportSafety:
    def test_import_safe_without_git_on_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """paths.py must be importable even when the `git` binary is not on PATH."""
        monkeypatch.setenv("PATH", "")
        for mod_name in list(sys.modules):
            if mod_name == "agent_orchestrator.isolation.paths":
                del sys.modules[mod_name]
        reloaded = importlib.import_module("agent_orchestrator.isolation.paths")
        assert reloaded.RESERVED_SHARED_PREFIXES == {".orchestrator", ".ao"}


# ---------------------------------------------------------------------------------------
# sanitize_ref_component (AC-2, AC-3)
# ---------------------------------------------------------------------------------------


def _generate_corpus() -> list[str]:
    """>= 200 strings incl. unicode, `..`, `.lock`, `//`, empty, 300 chars,
    leading/trailing `-`/`.` (AC-2)."""
    corpus: list[str] = [
        "",
        ".",
        "..",
        "...",
        "-",
        "--",
        ".lock",
        "foo.lock",
        "..foo",
        "foo..",
        "foo..bar",
        "//",
        "a//b",
        "/leading",
        "trailing/",
        "-leading",
        "trailing-",
        ".leading",
        "trailing.",
        "a" * 300,
        "-" * 300,
        "." * 300,
        "a" + "." * 300 + "b",
        "unicode-héllo",
        "unicode-éèê",
        "emoji-\U0001f600",
        "japanese-こんにちは",
        "null\x00byte",
        "tab\tchar",
        "newline\nchar",
        "@{upstream}",
        "a@{b",
        "colon:name",
        "question?name",
        "star*name",
        "bracket[name",
        "backslash\\name",
        "tilde~name",
        "caret^name",
        "space name",
        "integration",  # RESERVED_BRANCH_COMPONENTS -- still a valid ref shape on its own
        "x" * 79,
        "x" * 80,
        "x" * 81,
        ("a" * 79) + ".",
        ("a" * 79) + "-",
        ("a" * 76) + ".lock",
    ]
    # Fill out to >= 200 with a deterministic pseudo-random mix (fixed seed -- no real
    # `random` call on any run path; this is test-only corpus generation).
    import random

    rng = random.Random(1337)
    alphabet = string.ascii_letters + string.digits + "._-/@{}: \té\U0001f600\x00"
    while len(corpus) < 220:
        length = rng.randint(0, 40)
        corpus.append("".join(rng.choice(alphabet) for _ in range(length)))
    return corpus


_CORPUS = _generate_corpus()


class TestSanitizeRefComponent:
    @pytest.mark.parametrize("raw", _CORPUS)
    def test_output_is_valid_git_ref_component(self, raw: str) -> None:
        sanitized = paths.sanitize_ref_component(raw)
        result = subprocess.run(
            ["git", "check-ref-format", "--allow-onelevel", sanitized],
            capture_output=True,
        )
        assert result.returncode == 0, (
            f"sanitize_ref_component({raw!r}) -> {sanitized!r}, rejected by "
            f"git check-ref-format: {result.stderr!r}"
        )

    def test_corpus_has_at_least_200_entries(self) -> None:
        assert len(_CORPUS) >= 200

    def test_empty_string_falls_back_to_x(self) -> None:
        assert paths.sanitize_ref_component("") == "x"

    def test_truncates_to_80_chars(self) -> None:
        assert len(paths.sanitize_ref_component("a" * 300)) <= 80

    def test_lock_suffix_replaced(self) -> None:
        assert not paths.sanitize_ref_component("foo.lock").endswith(".lock")

    def test_double_dot_collapsed(self) -> None:
        assert ".." not in paths.sanitize_ref_component("foo..bar")

    def test_idempotent_on_already_clean_input(self) -> None:
        clean = "already-clean-123"
        assert paths.sanitize_ref_component(clean) == clean

    def test_two_100_char_ids_sharing_an_80_char_prefix_diverge(self) -> None:
        """C-1: a bare truncation would collide these onto the identical sanitized output;
        the hash suffix (derived from the full original value) must still diverge them."""
        common_prefix = "a" * 80
        id_a = common_prefix + "AAAAAAAAAAAAAAAAAAAA"
        id_b = common_prefix + "BBBBBBBBBBBBBBBBBBBB"
        sanitized_a = paths.sanitize_ref_component(id_a)
        sanitized_b = paths.sanitize_ref_component(id_b)
        assert sanitized_a != sanitized_b
        assert len(sanitized_a) <= 80
        assert len(sanitized_b) <= 80

    def test_hash_suffix_stable_and_deterministic(self) -> None:
        long_id = "x" * 200
        assert paths.sanitize_ref_component(long_id) == paths.sanitize_ref_component(long_id)


# ---------------------------------------------------------------------------------------
# task_branch / integration_branch / squash_ref (AC-3, AC-4)
# ---------------------------------------------------------------------------------------


class TestTaskBranch:
    def test_legal_id(self) -> None:
        assert paths.task_branch("run-1", "task-a") == "ao/run-1/task-a"

    def test_reserved_component_raises_assertion(self) -> None:
        # A task id that sanitizes to "integration" collides with the integration branch.
        with pytest.raises(AssertionError):
            paths.task_branch("run-1", "integration")

    def test_sanitizes_both_components(self) -> None:
        assert paths.task_branch("run 1", "task/a") == "ao/run-1/task-a"

    def test_ids_sharing_an_80_char_prefix_produce_distinct_branches_and_worktree_roots(
        self,
    ) -> None:
        """C-1's required test at the branch/path level, not just sanitize_ref_component's
        own output."""
        common_prefix = "a" * 80
        task_a = common_prefix + "-task-a-suffix"
        task_b = common_prefix + "-task-b-suffix"

        branch_a = paths.task_branch("run-1", task_a)
        branch_b = paths.task_branch("run-1", task_b)
        assert branch_a != branch_b

        root_a = paths.worktree_root("/ws", "run-1", task_a, "repo-key")
        root_b = paths.worktree_root("/ws", "run-1", task_b, "repo-key")
        assert root_a != root_b


class TestIntegrationBranch:
    def test_shape(self) -> None:
        assert paths.integration_branch("run-1") == "ao/run-1/integration"


class TestSquashRef:
    def test_shape(self) -> None:
        assert paths.squash_ref("run-1", "task-a", 3) == "refs/ao/runs/run-1/task-a/squash-3"


class TestNoDfRefConflict:
    def test_task_and_integration_branches_coexist_as_real_refs(self, tmp_path: Path) -> None:
        """AC-4: both `ao/<run>/<task>` and `ao/<run>/integration` are created in a real
        repo with no D/F ref conflict; `ao/<run>` alone is never created as a branch.
        """
        repo = make_repo(tmp_path)
        git = GitRepo(str(repo), hooks_dir=tmp_path / "hooks")
        head = git.rev_parse("HEAD")
        assert head is not None

        task_ref = paths.task_branch("run-1", "task-a")
        integration_ref = paths.integration_branch("run-1")
        assert task_ref != integration_ref
        assert task_ref != "ao/run-1"
        assert integration_ref != "ao/run-1"

        git.create_ref(f"refs/heads/{task_ref}", head)
        git.create_ref(f"refs/heads/{integration_ref}", head)

        assert git.branch_exists(task_ref)
        assert git.branch_exists(integration_ref)
        assert not git.branch_exists("ao/run-1")


# ---------------------------------------------------------------------------------------
# workspace_key (AC-6)
# ---------------------------------------------------------------------------------------


class TestWorkspaceKey:
    def test_stable_across_calls(self) -> None:
        assert paths.workspace_key("/a/proj") == paths.workspace_key("/a/proj")

    def test_distinct_for_colliding_basenames(self) -> None:
        assert paths.workspace_key("/a/proj") != paths.workspace_key("/b/proj")

    def test_filesystem_safe(self) -> None:
        key = paths.workspace_key("/a/proj with spaces/@weird!")
        result = subprocess.run(
            ["git", "check-ref-format", "--allow-onelevel", key], capture_output=True
        )
        assert result.returncode == 0


# ---------------------------------------------------------------------------------------
# state_dir / worktree_root_prefix_for / worktree_root (AC-5)
# ---------------------------------------------------------------------------------------


class TestStateDir:
    def test_override_env_wins(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        override = tmp_path / "custom-state"
        monkeypatch.setenv("AO_STATE_DIR", str(override))
        assert paths.state_dir() == override

    def test_xdg_state_home_fallback(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))
        assert paths.state_dir() == tmp_path / "xdg-state" / "ao"

    def test_home_fallback(self, tmp_path: Path) -> None:
        assert paths.state_dir() == tmp_path / "home" / ".local" / "state" / "ao"


class TestWorktreeRoot:
    def test_default_base_under_state_dir(self, tmp_path: Path) -> None:
        root = paths.worktree_root("/workspace", "run-1", "task-a", "repo-key")
        expected_prefix = tmp_path / "home" / ".local" / "state" / "ao" / "worktrees"
        assert str(root).startswith(str(expected_prefix))
        assert root.name == "repo-key"
        assert root.parent.name == "task-a"

    def test_ao_worktree_root_env_override(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        override = tmp_path / "custom-worktrees"
        monkeypatch.setenv("AO_WORKTREE_ROOT", str(override))
        root = paths.worktree_root("/workspace", "run-1", "task-a", "repo-key")
        assert str(root).startswith(str(override))

    def test_prefix_is_ancestor_of_worktree_root(self, tmp_path: Path) -> None:
        prefix = paths.worktree_root_prefix_for("/workspace", "run-1")
        root = paths.worktree_root("/workspace", "run-1", "task-a", "repo-key")
        assert str(root).startswith(str(prefix) + "/")

    def test_two_repos_two_tasks_produce_distinct_roots(self) -> None:
        r1 = paths.worktree_root("/ws", "run-1", "task-a", "repo-1")
        r2 = paths.worktree_root("/ws", "run-1", "task-b", "repo-1")
        r3 = paths.worktree_root("/ws", "run-1", "task-a", "repo-2")
        assert len({r1, r2, r3}) == 3


# ---------------------------------------------------------------------------------------
# effective_path (AC-7, HLD §7.2 exactly)
# ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _FakeRepo:
    toplevel: str
    worktree_root: str


@dataclass
class _FakeTaskIso:
    workspace_root: str
    repos: list[_FakeRepo] = field(default_factory=list)


class TestEffectivePath:
    def test_none_task_iso_is_identity(self) -> None:
        assert paths.effective_path("/ws/anything", None) == "/ws/anything"

    def test_path_inside_isolated_repo_is_remapped(self) -> None:
        iso = _FakeTaskIso(
            workspace_root="/ws", repos=[_FakeRepo(toplevel="/ws/repo", worktree_root="/wt/repo")]
        )
        assert paths.effective_path("/ws/repo/src/a.py", iso) == "/wt/repo/src/a.py"

    def test_path_outside_every_repo_is_unchanged(self) -> None:
        iso = _FakeTaskIso(
            workspace_root="/ws", repos=[_FakeRepo(toplevel="/ws/repo", worktree_root="/wt/repo")]
        )
        assert paths.effective_path("/ws/outputs/x.md", iso) == "/ws/outputs/x.md"

    def test_orchestrator_dir_unchanged_even_inside_isolated_repo(self) -> None:
        # Reserved prefixes win even when the repo's toplevel IS the workspace root.
        iso = _FakeTaskIso(
            workspace_root="/ws", repos=[_FakeRepo(toplevel="/ws", worktree_root="/wt/repo")]
        )
        assert paths.effective_path("/ws/.orchestrator/runs/x", iso) == "/ws/.orchestrator/runs/x"

    def test_ao_dir_unchanged_even_inside_isolated_repo(self) -> None:
        iso = _FakeTaskIso(
            workspace_root="/ws", repos=[_FakeRepo(toplevel="/ws", worktree_root="/wt/repo")]
        )
        assert paths.effective_path("/ws/.ao/config.yaml", iso) == "/ws/.ao/config.yaml"

    def test_nested_repos_innermost_wins(self) -> None:
        iso = _FakeTaskIso(
            workspace_root="/ws",
            repos=[
                _FakeRepo(toplevel="/ws/outer", worktree_root="/wt/outer"),
                _FakeRepo(toplevel="/ws/outer/inner", worktree_root="/wt/inner"),
            ],
        )
        assert paths.effective_path("/ws/outer/inner/f.py", iso) == "/wt/inner/f.py"
        assert paths.effective_path("/ws/outer/f.py", iso) == "/wt/outer/f.py"

    def test_repo_toplevel_itself_maps_to_worktree_root(self) -> None:
        iso = _FakeTaskIso(
            workspace_root="/ws", repos=[_FakeRepo(toplevel="/ws/repo", worktree_root="/wt/repo")]
        )
        assert paths.effective_path("/ws/repo", iso) == "/wt/repo"
