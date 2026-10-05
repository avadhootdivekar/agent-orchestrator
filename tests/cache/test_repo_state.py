"""AC U-G1..U-G8: workspace-bounded repo detection, HEAD reader and worktree probe (HLD 8.2.4).

VCS tests use temporary repositories, an injected `hooks_dir` and an `AO_STATE_DIR` pointed at
`tmp_path`; most paths run against the scripted `FakeVcsRunner` so no real git is needed.
"""

from __future__ import annotations

import ast
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

import agent_orchestrator.cache.repo_state as repo_state
from agent_orchestrator.cache.constants import (
    CACHE_GIT_TIMEOUT_SECONDS,
    GIT_OPTIONAL_LOCKS_OFF,
    GIT_OPTIONAL_LOCKS_VAR,
    REASON_PATH_REJECTED,
    REASON_REPO_HEAD_UNAVAILABLE,
    REASON_REPO_WORKTREE_PROBE_FAILED,
    UNBORN_HEAD,
)
from agent_orchestrator.cache.repo_state import (
    RepoHeadReader,
    WorktreeProbe,
    find_git_toplevel,
    nested_repo_marker,
)
from agent_orchestrator.cache.types import UncacheableError
from tests.cache.fakes import FakeVcsRunner

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
needs_posix = pytest.mark.skipif(sys.platform == "win32", reason="POSIX path semantics")

SHA = "0123456789abcdef0123456789abcdef01234567"
SHA2 = "fedcba9876543210fedcba9876543210fedcba98"


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("AO_CACHE", raising=False)
    monkeypatch.setenv("AO_STATE_DIR", str(tmp_path / "ao-state"))


@pytest.fixture
def hooks_dir(tmp_path: Path) -> Path:
    return tmp_path / "empty-hooks"


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    path = tmp_path / "ws"
    path.mkdir()
    return path


def fake_repo(root: Path) -> Path:
    """A directory that merely carries a `.git` marker (for scripted-runner tests)."""
    root.mkdir(parents=True, exist_ok=True)
    (root / ".git").mkdir(exist_ok=True)
    return root


def git(repo: Path, *args: str) -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.invalid",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
    }
    cp = subprocess.run(
        ["git", "-c", "commit.gpgsign=false", *args],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return cp.stdout.strip()


def real_repo(root: Path, *, commit: bool = True) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    git(root, "init", "-q", "-b", "main")
    if commit:
        (root / "tracked.txt").write_text("aaaa")
        (root / "other.txt").write_text("keep")
        git(root, "add", "tracked.txt", "other.txt")
        git(root, "commit", "-q", "-m", "init")
    return root


def head_reader(ws: Path, hooks_dir: Path, runner: Any = None) -> RepoHeadReader:
    return RepoHeadReader(workspace_root=str(ws), runner=runner, hooks_dir=hooks_dir)


def probe(hooks_dir: Path, runner: Any = None) -> WorktreeProbe:
    return WorktreeProbe(runner=runner, hooks_dir=hooks_dir)


# ------------------------------------------------------------------------------------ U-G1
def test_toplevel_for_a_repo_a_subdirectory_and_a_git_file(ws: Path) -> None:
    repo = fake_repo(ws / "repo")
    (repo / "a" / "b").mkdir(parents=True)
    wt = ws / "wt"
    wt.mkdir()
    (wt / ".git").write_text("gitdir: /elsewhere/.git/worktrees/wt\n")  # worktree-style FILE
    (wt / "deep").mkdir()
    assert find_git_toplevel(str(repo), workspace_root=str(ws)) == str(repo)
    assert find_git_toplevel(str(repo / "a" / "b"), workspace_root=str(ws)) == str(repo)
    assert find_git_toplevel(str(wt), workspace_root=str(ws)) == str(wt)
    assert find_git_toplevel(str(wt / "deep"), workspace_root=str(ws)) == str(wt)


def test_toplevel_is_none_for_a_plain_directory(ws: Path) -> None:
    (ws / "plain" / "sub").mkdir(parents=True)
    assert find_git_toplevel(str(ws / "plain" / "sub"), workspace_root=str(ws)) is None
    assert find_git_toplevel(str(ws), workspace_root=str(ws)) is None


def test_toplevel_includes_the_workspace_root_itself(ws: Path) -> None:
    fake_repo(ws)
    (ws / "proj").mkdir()
    assert find_git_toplevel(str(ws / "proj"), workspace_root=str(ws)) == str(ws)
    assert find_git_toplevel(str(ws), workspace_root=str(ws)) == str(ws)


def test_toplevel_normalizes_dotdot_and_trailing_separators(ws: Path) -> None:
    repo = fake_repo(ws / "repo")
    (repo / "sub").mkdir()
    messy = f"{repo}{os.sep}sub{os.sep}..{os.sep}"
    assert find_git_toplevel(messy, workspace_root=str(ws) + os.sep) == str(repo)


def test_toplevel_prefers_the_nearest_marker(ws: Path) -> None:
    outer = fake_repo(ws / "outer")
    inner = fake_repo(outer / "inner")
    assert find_git_toplevel(str(inner), workspace_root=str(ws)) == str(inner)


def test_a_dangling_git_symlink_counts_as_a_marker(ws: Path) -> None:
    if sys.platform == "win32":
        pytest.skip("symlinks")
    d = ws / "d"
    d.mkdir()
    (d / ".git").symlink_to(ws / "nowhere")  # lstat only: exists as an entry
    assert find_git_toplevel(str(d), workspace_root=str(ws)) == str(d)


# ------------------------------------------------------------------------------------ U-G7
def test_a_git_dir_above_the_workspace_is_never_consulted(tmp_path: Path) -> None:
    home = fake_repo(tmp_path / "home")  # simulated $HOME holding a .git
    ws = home / "work" / "ws"
    proj = ws / "proj"
    proj.mkdir(parents=True)
    runner = FakeVcsRunner()
    assert find_git_toplevel(str(proj), workspace_root=str(ws)) is None
    assert head_reader(ws, tmp_path / "hooks", runner).read({"p": str(proj)}) == {}
    assert probe(tmp_path / "hooks", runner).snapshot({"p": str(proj)}, str(ws), frozenset()) == (
        frozenset()
    )
    assert runner.calls == []  # no VCS command ran against the outer repository
    assert nested_repo_marker(str(ws)) == str(home)


def test_nested_repo_marker_is_none_without_an_ancestor_repo(tmp_path: Path) -> None:
    ws = tmp_path / "a" / "b" / "ws"
    ws.mkdir(parents=True)
    fake_repo(ws / "inside")  # at/below the workspace root: not a nested-workspace case
    assert nested_repo_marker(str(ws)) is None


def test_nested_repo_marker_reports_the_nearest_ancestor(tmp_path: Path) -> None:
    fake_repo(tmp_path / "top")
    nearer = fake_repo(tmp_path / "top" / "mid")
    ws = nearer / "ws"
    ws.mkdir()
    assert nested_repo_marker(str(ws)) == str(nearer)


def test_nested_repo_marker_ignores_a_marker_at_the_workspace_root(ws: Path) -> None:
    fake_repo(ws)
    assert nested_repo_marker(str(ws)) is None


def test_a_repo_path_outside_the_workspace_is_rejected(tmp_path: Path, ws: Path) -> None:
    outside = fake_repo(tmp_path / "outside")
    sibling = tmp_path / "ws-evil"  # shares the textual prefix of the workspace
    sibling.mkdir()
    for bad in (outside, sibling, tmp_path):
        with pytest.raises(UncacheableError) as info:
            find_git_toplevel(str(bad), workspace_root=str(ws))
        assert info.value.reason == REASON_PATH_REJECTED


def test_head_reader_and_probe_reject_a_repo_path_outside_the_workspace(
    tmp_path: Path, ws: Path, hooks_dir: Path
) -> None:
    outside = fake_repo(tmp_path / "outside")
    with pytest.raises(UncacheableError) as info:
        head_reader(ws, hooks_dir, FakeVcsRunner()).read({"x": str(outside)})
    assert info.value.reason == REASON_PATH_REJECTED
    with pytest.raises(UncacheableError) as info2:
        probe(hooks_dir, FakeVcsRunner()).snapshot({"x": str(outside)}, str(ws), frozenset())
    assert info2.value.reason == REASON_REPO_WORKTREE_PROBE_FAILED


# --------------------------------------------------------------------------- U-G2 / U-G3 / U-G4
def test_head_reader_omits_non_git_repos(ws: Path, hooks_dir: Path) -> None:
    (ws / "plain").mkdir()
    runner = FakeVcsRunner()
    assert head_reader(ws, hooks_dir, runner).read({"p": str(ws / "plain")}) == {}
    assert runner.calls == []


def test_head_reader_returns_the_scripted_sha(ws: Path, hooks_dir: Path) -> None:
    repo = fake_repo(ws / "core")
    runner = FakeVcsRunner().on("rev-parse", "HEAD", stdout=SHA.encode() + b"\n")
    assert head_reader(ws, hooks_dir, runner).read({"core": str(repo)}) == {"core": SHA}


def test_head_reader_reports_unborn_when_a_branch_has_no_commit(ws: Path, hooks_dir: Path) -> None:
    repo = fake_repo(ws / "core")
    runner = (
        FakeVcsRunner().on("rev-parse", "HEAD", returncode=1).on("symbolic-ref", stdout=b"main\n")
    )
    assert head_reader(ws, hooks_dir, runner).read({"core": str(repo)}) == {"core": UNBORN_HEAD}


@needs_git
def test_head_reader_with_real_repositories(ws: Path, hooks_dir: Path) -> None:
    born = real_repo(ws / "born")
    unborn = real_repo(ws / "unborn", commit=False)
    (ws / "plain").mkdir()
    heads = head_reader(ws, hooks_dir).read(
        {"born": str(born), "unborn": str(unborn), "plain": str(ws / "plain")}
    )
    assert heads == {"born": git(born, "rev-parse", "HEAD"), "unborn": UNBORN_HEAD}
    assert len(heads["born"]) == 40


@needs_git
def test_head_reader_follows_a_new_commit_between_reads(ws: Path, hooks_dir: Path) -> None:
    repo = real_repo(ws / "r")
    reader = head_reader(ws, hooks_dir)
    first = reader.read({"r": str(repo)})
    (repo / "tracked.txt").write_text("bbbb")
    git(repo, "commit", "-q", "-am", "second")
    second = reader.read({"r": str(repo)})
    assert first != second  # only the GitRepo is memoized, never the HEAD value


@needs_git
def test_a_stray_git_directory_is_head_unavailable(ws: Path, hooks_dir: Path) -> None:
    stray = fake_repo(ws / "stray")  # `.git` is an empty directory: not a repository
    with pytest.raises(UncacheableError) as info:
        head_reader(ws, hooks_dir).read({"stray": str(stray)})
    assert info.value.reason == REASON_REPO_HEAD_UNAVAILABLE


def test_a_failing_rev_parse_is_head_unavailable(ws: Path, hooks_dir: Path) -> None:
    repo = fake_repo(ws / "core")
    runner = FakeVcsRunner().on("rev-parse", returncode=128).on("symbolic-ref", returncode=128)
    with pytest.raises(UncacheableError) as info:
        head_reader(ws, hooks_dir, runner).read({"core": str(repo)})
    assert info.value.reason == REASON_REPO_HEAD_UNAVAILABLE and info.value.detail == "core"


@pytest.mark.parametrize(
    ("exc", "type_name"),
    [
        (subprocess.TimeoutExpired(["git"], 10), "GitTimeoutError"),
        (OSError("no git"), "GitUnavailableError"),
    ],
)
def test_a_runner_raising_maps_to_head_unavailable_with_the_exception_type(
    ws: Path, hooks_dir: Path, exc: BaseException, type_name: str
) -> None:
    repo = fake_repo(ws / "core")
    runner = FakeVcsRunner().on("rev-parse", raises=exc)
    with pytest.raises(UncacheableError) as info:
        head_reader(ws, hooks_dir, runner).read({"core": str(repo)})
    assert info.value.reason == REASON_REPO_HEAD_UNAVAILABLE
    assert info.value.detail == f"core:{type_name}"


def test_a_non_empty_hooks_dir_is_head_unavailable(ws: Path, hooks_dir: Path) -> None:
    repo = fake_repo(ws / "core")
    hooks_dir.mkdir()
    (hooks_dir / "pre-commit").write_text("#!/bin/sh\n")  # GitRepo refuses: RuntimeError
    with pytest.raises(UncacheableError) as info:
        head_reader(ws, hooks_dir, FakeVcsRunner()).read({"core": str(repo)})
    assert info.value.reason == REASON_REPO_HEAD_UNAVAILABLE
    assert info.value.detail == "core:RuntimeError"


def test_a_repo_construction_oserror_is_head_unavailable(
    ws: Path, hooks_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = fake_repo(ws / "core")

    def boom(*a: Any, **k: Any) -> Any:
        raise OSError("read-only HOME")

    monkeypatch.setattr(repo_state, "GitRepo", boom)
    with pytest.raises(UncacheableError) as info:
        head_reader(ws, hooks_dir).read({"core": str(repo)})
    assert info.value.detail == "core:OSError"


def test_head_reader_is_memoized_per_toplevel(
    ws: Path, hooks_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = fake_repo(ws / "mono")
    (repo / "pkg-a").mkdir()
    (repo / "pkg-b").mkdir()
    other = fake_repo(ws / "other")
    built: list[str] = []
    real = repo_state.GitRepo

    def spy(path: str, **kw: Any) -> Any:
        built.append(path)
        return real(path, **kw)

    monkeypatch.setattr(repo_state, "GitRepo", spy)
    runner = FakeVcsRunner().on("rev-parse", "HEAD", stdout=SHA.encode())
    reader = head_reader(ws, hooks_dir, runner)
    paths = {"a": str(repo / "pkg-a"), "b": str(repo / "pkg-b"), "o": str(other)}
    assert reader.read(paths) == {"a": SHA, "b": SHA, "o": SHA}
    assert sorted(built) == sorted([str(repo), str(other)])  # one GitRepo per toplevel
    assert sum("rev-parse" in c["argv"] for c in runner.calls) == 2  # one read per toplevel
    reader.read(paths)
    assert len(built) == 2  # the GitRepo instances survive across reads


def test_head_reader_iterates_repo_ids_in_sorted_order(ws: Path, hooks_dir: Path) -> None:
    a, b = fake_repo(ws / "a"), fake_repo(ws / "b")
    runner = FakeVcsRunner().on("rev-parse", "HEAD", stdout=SHA.encode())
    reader = head_reader(ws, hooks_dir, runner)
    reader.read({"zz": str(b), "aa": str(a)})
    cwds = [c["cwd"] for c in runner.calls if "rev-parse" in c["argv"]]
    assert cwds == [str(a), str(b)]


# ------------------------------------------------------------------------------------ U-G5/6
@needs_git
def test_worktree_snapshot_is_clean_for_an_untouched_repo(ws: Path, hooks_dir: Path) -> None:
    repo = real_repo(ws / "r")
    assert probe(hooks_dir).snapshot({"r": str(repo)}, str(ws), frozenset()) == frozenset()


@needs_git
def test_worktree_snapshot_changes_on_an_undeclared_tracked_edit(ws: Path, hooks_dir: Path) -> None:
    repo = real_repo(ws / "r")
    p = probe(hooks_dir)
    before = p.snapshot({"r": str(repo)}, str(ws), frozenset())
    (repo / "tracked.txt").write_text("bbbb")
    after = p.snapshot({"r": str(repo)}, str(ws), frozenset())
    assert before != after
    ((top, path, index, worktree, mtime_ns, size),) = after
    assert (top, path, index, worktree, size) == (str(repo), "tracked.txt", " ", "M", 4)
    assert isinstance(mtime_ns, int)


@needs_git
def test_a_same_size_rewrite_is_caught_by_mtime(ws: Path, hooks_dir: Path) -> None:
    repo = real_repo(ws / "r")
    target = repo / "tracked.txt"
    p = probe(hooks_dir)
    target.write_text("bbbb")
    os.utime(target, ns=(1_000_000_000_000_000_000, 1_000_000_000_000_000_000))
    first = p.snapshot({"r": str(repo)}, str(ws), frozenset())
    target.write_text("cccc")  # same size, same status code (" M")
    os.utime(target, ns=(2_000_000_000_000_000_000, 2_000_000_000_000_000_000))
    second = p.snapshot({"r": str(repo)}, str(ws), frozenset())
    assert first != second
    assert {e[:4] + (e[5],) for e in first} == {e[:4] + (e[5],) for e in second}  # only mtime moved


@needs_git
def test_a_declared_output_is_excluded(ws: Path, hooks_dir: Path) -> None:
    repo = real_repo(ws / "r")
    p = probe(hooks_dir)
    clean = p.snapshot({"r": str(repo)}, str(ws), frozenset())
    (repo / "tracked.txt").write_text("bbbb")
    out = frozenset({str(repo / "tracked.txt")})
    assert p.snapshot({"r": str(repo)}, str(ws), out) == clean


@needs_git
def test_a_file_under_the_orchestrator_dir_is_excluded(ws: Path, hooks_dir: Path) -> None:
    repo = real_repo(ws, commit=False)  # the workspace root itself is the repo
    state = ws / ".orchestrator"
    state.mkdir()
    (state / "state.json").write_text("{}")
    git(repo, "add", "-f", ".orchestrator/state.json")
    git(repo, "commit", "-q", "-m", "oops tracked state")
    p = probe(hooks_dir)
    clean = p.snapshot({"ws": str(ws)}, str(ws), frozenset())
    (state / "state.json").write_text('{"changed": true}')
    assert p.snapshot({"ws": str(ws)}, str(ws), frozenset()) == clean == frozenset()


@needs_git
def test_a_new_untracked_file_does_not_change_the_snapshot(ws: Path, hooks_dir: Path) -> None:
    repo = real_repo(ws / "r")
    p = probe(hooks_dir)
    before = p.snapshot({"r": str(repo)}, str(ws), frozenset())
    (repo / "brand-new.txt").write_text("untracked")
    assert p.snapshot({"r": str(repo)}, str(ws), frozenset()) == before


@needs_git
def test_a_deleted_tracked_file_has_a_none_signature(ws: Path, hooks_dir: Path) -> None:
    repo = real_repo(ws / "r")
    (repo / "other.txt").unlink()
    ((_, path, _, worktree, mtime_ns, size),) = probe(hooks_dir).snapshot(
        {"r": str(repo)}, str(ws), frozenset()
    )
    assert (path, worktree, mtime_ns, size) == ("other.txt", "D", None, None)


def test_snapshot_skips_non_git_repo_paths(ws: Path, hooks_dir: Path) -> None:
    (ws / "plain").mkdir()
    runner = FakeVcsRunner()
    assert probe(hooks_dir, runner).snapshot({"p": str(ws / "plain")}, str(ws), frozenset()) == (
        frozenset()
    )
    assert runner.calls == []


def test_snapshot_dedupes_repos_that_share_a_toplevel(ws: Path, hooks_dir: Path) -> None:
    repo = fake_repo(ws / "mono")
    (repo / "a").mkdir()
    (repo / "b").mkdir()
    runner = FakeVcsRunner().on("status", stdout=b" M f.txt\0")
    (repo / "f.txt").write_text("x")
    snap = probe(hooks_dir, runner).snapshot(
        {"a": str(repo / "a"), "b": str(repo / "b")}, str(ws), frozenset()
    )
    assert len(snap) == 1 and sum("status" in c["argv"] for c in runner.calls) == 1


@pytest.mark.parametrize(
    "exc",
    [
        subprocess.TimeoutExpired(["git"], 10),
        OSError("no git"),
    ],
)
def test_a_probe_failure_maps_to_repo_worktree_probe_failed(
    ws: Path, hooks_dir: Path, exc: BaseException
) -> None:
    repo = fake_repo(ws / "r")
    runner = FakeVcsRunner().on("status", raises=exc)
    with pytest.raises(UncacheableError) as info:
        probe(hooks_dir, runner).snapshot({"r": str(repo)}, str(ws), frozenset())
    assert info.value.reason == REASON_REPO_WORKTREE_PROBE_FAILED


def test_a_nonzero_status_exit_is_a_probe_failure(ws: Path, hooks_dir: Path) -> None:
    repo = fake_repo(ws / "r")
    runner = FakeVcsRunner().on("status", returncode=128)
    with pytest.raises(UncacheableError) as info:
        probe(hooks_dir, runner).snapshot({"r": str(repo)}, str(ws), frozenset())
    assert info.value.reason == REASON_REPO_WORKTREE_PROBE_FAILED


def test_a_non_empty_hooks_dir_is_a_probe_failure(ws: Path, hooks_dir: Path) -> None:
    repo = fake_repo(ws / "r")
    hooks_dir.mkdir()
    (hooks_dir / "post-merge").write_text("x")
    with pytest.raises(UncacheableError) as info:
        probe(hooks_dir, FakeVcsRunner()).snapshot({"r": str(repo)}, str(ws), frozenset())
    assert info.value.reason == REASON_REPO_WORKTREE_PROBE_FAILED
    assert info.value.detail == "RuntimeError"


# ------------------------------------------------------------------------------------ U-G8
def test_every_gitrepo_gets_the_short_timeout_and_the_optional_locks_env(
    ws: Path, hooks_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = fake_repo(ws / "r")
    monkeypatch.setenv("PATH", os.environ.get("PATH", ""))  # make the env copy observable
    seen: list[dict[str, Any]] = []
    real = repo_state.GitRepo

    def spy(path: str, **kw: Any) -> Any:
        seen.append(kw)
        return real(path, **kw)

    monkeypatch.setattr(repo_state, "GitRepo", spy)
    runner = FakeVcsRunner().on("rev-parse", "HEAD", stdout=SHA.encode())
    head_reader(ws, hooks_dir, runner).read({"r": str(repo)})
    probe(hooks_dir, runner).snapshot({"r": str(repo)}, str(ws), frozenset())
    assert len(seen) == 2
    for kw in seen:
        assert kw["timeout"] == CACHE_GIT_TIMEOUT_SECONDS == 10
        assert kw["env"][GIT_OPTIONAL_LOCKS_VAR] == GIT_OPTIONAL_LOCKS_OFF == "0"
        assert kw["env"]["PATH"] == os.environ["PATH"]  # layered over the process env
        assert kw["hooks_dir"] == hooks_dir and kw["runner"] is runner


def test_the_runner_sees_optional_locks_off_and_the_short_timeout(
    ws: Path, hooks_dir: Path
) -> None:
    repo = fake_repo(ws / "r")
    runner = FakeVcsRunner().on("rev-parse", "HEAD", stdout=SHA.encode())
    head_reader(ws, hooks_dir, runner).read({"r": str(repo)})
    probe(hooks_dir, runner).snapshot({"r": str(repo)}, str(ws), frozenset())
    assert runner.calls
    for call in runner.calls:
        assert call["env"][GIT_OPTIONAL_LOCKS_VAR] == "0"
        assert call["timeout"] == CACHE_GIT_TIMEOUT_SECONDS


def test_a_custom_timeout_is_passed_through(ws: Path, hooks_dir: Path) -> None:
    repo = fake_repo(ws / "r")
    runner = FakeVcsRunner().on("rev-parse", "HEAD", stdout=SHA.encode())
    RepoHeadReader(workspace_root=str(ws), runner=runner, hooks_dir=hooks_dir, timeout=3).read(
        {"r": str(repo)}
    )
    assert runner.calls[0]["timeout"] == 3


def test_the_module_never_uses_the_private_run_or_probe() -> None:
    tree = ast.parse(Path(repo_state.__file__).read_text())
    banned = {"_run", "probe"}
    uses = [
        n.lineno
        for n in ast.walk(tree)
        if (isinstance(n, ast.Attribute) and n.attr in banned)
        or (isinstance(n, ast.Name) and n.id in banned)
    ]
    assert uses == []


@needs_posix
def test_real_git_runs_with_the_inherited_environment(ws: Path, hooks_dir: Path) -> None:
    """A real runner must still find `git` (PATH survives the env layering)."""
    if shutil.which("git") is None:
        pytest.skip("git not installed")
    repo = real_repo(ws / "r")
    assert head_reader(ws, hooks_dir).read({"r": str(repo)})["r"] == git(repo, "rev-parse", "HEAD")
