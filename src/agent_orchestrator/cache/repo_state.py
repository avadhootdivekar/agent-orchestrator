"""Repository state for the result cache: HEAD per repo and a tracked-worktree snapshot (HLD 8.2.4).

Repository detection is bounded by the WORKSPACE ROOT (ADR-0019 D5, early-gate A2): the nearest
`.git` entry is searched from a repo path up to the workspace root inclusive and never above it,
so a `.git` in `$HOME` or any other ancestor is never consulted and no VCS command ever runs
against an outer repository. A workspace nested inside a parent repository is therefore treated
as non-git; `nested_repo_marker` exists only so the banner can warn about it (A-12).

Only PUBLIC `isolation.git.GitRepo` methods are used (`rev_parse`, `current_branch`,
`status_porcelain`): never the private `_run` and never `GitRepo.probe()`, which collapses "not a
repo" and "git failed" into None and uses the 300 s default timeout. Every `GitRepo` built here
gets a 10 s timeout and `GIT_OPTIONAL_LOCKS=0`, so a read can never race a parallel agent's
`index.lock`.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping
from pathlib import Path

from agent_orchestrator.cache.constants import (
    CACHE_DIR_PARTS,
    CACHE_GIT_TIMEOUT_SECONDS,
    GIT_NEUTRALISED_CONFIG,
    GIT_OPTIONAL_LOCKS_OFF,
    GIT_OPTIONAL_LOCKS_VAR,
    GIT_SCRUBBED_ENV_PREFIXES,
    GIT_SCRUBBED_ENV_VARS,
    REASON_PATH_REJECTED,
    REASON_REPO_HEAD_UNAVAILABLE,
    REASON_REPO_WORKTREE_PROBE_FAILED,
    UNBORN_HEAD,
)
from agent_orchestrator.cache.types import UncacheableError
from agent_orchestrator.errors import GitError
from agent_orchestrator.isolation.git import GitRepo, Runner

_GIT_MARKER = ".git"
# The exception family a git read can raise. `GitRepo` wraps a runner's TimeoutExpired / OSError
# into GitError subclasses, but its constructor can raise OSError / RuntimeError directly, and a
# custom runner may raise anything the injected `subprocess` layer does.
_GIT_FAILURES = (GitError, OSError, RuntimeError, subprocess.TimeoutExpired)


def _within(path: str, root: str) -> bool:
    return path == root or path.startswith(root + os.sep)


def find_git_toplevel(path: str, *, workspace_root: str) -> str | None:
    """The directory holding the nearest `.git` entry (directory or worktree-style file; lstat
    only), walking up from *path* to *workspace_root* INCLUSIVE and never above it. None means
    "not a git repository". A path outside the workspace raises `path_rejected`.
    """
    current = os.path.normpath(os.path.abspath(path))
    ws = os.path.normpath(os.path.abspath(workspace_root))
    if not _within(current, ws):
        raise UncacheableError(REASON_PATH_REJECTED, "repo path outside the workspace")
    while True:
        if os.path.lexists(os.path.join(current, _GIT_MARKER)):
            return current
        if current == ws:
            return None
        current = os.path.dirname(current)


def nested_repo_marker(workspace_root: str) -> str | None:
    """The first ancestor ABOVE the workspace root holding a `.git` entry (lstat only), else
    None. Used only for the banner warning: a nested workspace is treated as non-git.
    """
    current = os.path.normpath(os.path.abspath(workspace_root))
    while True:
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent
        if os.path.lexists(os.path.join(current, _GIT_MARKER)):
            return current


def git_read_env(base: Mapping[str, str] | None = None) -> dict[str, str]:
    """The child environment of every repository read (SEC-03, SEC-04).

    `GitRepo.env` REPLACES the child environment, so this is a copy of the process environment
    (PATH, HOME, ... must survive) with (1) every repository-selecting / config-injecting `GIT_*`
    variable removed, so an inherited `GIT_DIR` can never redirect the workspace-bounded read to
    another repository, (2) `GIT_OPTIONAL_LOCKS=0`, and (3) the config that makes `git status`
    run a program named by the agent-writable repo config (`core.fsmonitor`) forced off through
    `GIT_CONFIG_COUNT`, which has command-line precedence over repository config.
    """
    env = {
        k: v
        for k, v in (os.environ if base is None else base).items()
        if k not in GIT_SCRUBBED_ENV_VARS and not k.startswith(GIT_SCRUBBED_ENV_PREFIXES)
    }
    env[GIT_OPTIONAL_LOCKS_VAR] = GIT_OPTIONAL_LOCKS_OFF
    env["GIT_CONFIG_COUNT"] = str(len(GIT_NEUTRALISED_CONFIG))
    for i, (key, value) in enumerate(GIT_NEUTRALISED_CONFIG):
        env[f"GIT_CONFIG_KEY_{i}"] = key
        env[f"GIT_CONFIG_VALUE_{i}"] = value
    return env


class _GitReaders:
    """Shared `GitRepo` construction (one memoized instance per toplevel) for the two readers."""

    def __init__(
        self,
        *,
        runner: Runner | None = None,
        hooks_dir: Path | None = None,
        timeout: int = CACHE_GIT_TIMEOUT_SECONDS,
    ) -> None:
        self._runner = runner
        self._hooks_dir = hooks_dir
        self._timeout = timeout
        self._repos: dict[str, GitRepo] = {}

    def _repo(self, top: str) -> GitRepo:
        repo = self._repos.get(top)
        if repo is None:
            env = git_read_env()
            repo = GitRepo(
                top,
                timeout=self._timeout,
                runner=self._runner,
                hooks_dir=self._hooks_dir,
                env=env,
            )
            self._repos[top] = repo
        return repo


class RepoHeadReader(_GitReaders):
    """`read(repo_paths) -> {repo id: HEAD sha | "unborn"}`; non-git repos are omitted."""

    def __init__(
        self,
        *,
        workspace_root: str,
        runner: Runner | None = None,
        hooks_dir: Path | None = None,
        timeout: int = CACHE_GIT_TIMEOUT_SECONDS,
    ) -> None:
        super().__init__(runner=runner, hooks_dir=hooks_dir, timeout=timeout)
        self._ws = workspace_root

    def read(self, repo_paths: Mapping[str, str]) -> dict[str, str]:
        out: dict[str, str] = {}
        by_top: dict[str, str] = {}  # one HEAD read per toplevel within a call
        for rid in sorted(repo_paths):
            top = find_git_toplevel(repo_paths[rid], workspace_root=self._ws)
            if top is None:
                continue  # non-git: omitted
            if top not in by_top:
                by_top[top] = self._head(rid, top)
            out[rid] = by_top[top]
        return out

    def _head(self, rid: str, top: str) -> str:
        try:
            git = self._repo(top)
            sha = git.rev_parse("HEAD")
            if sha is not None:
                return sha
            if git.current_branch() is not None:
                return UNBORN_HEAD  # a branch with no commit yet
        except _GIT_FAILURES as e:
            raise UncacheableError(REASON_REPO_HEAD_UNAVAILABLE, f"{rid}:{type(e).__name__}") from e
        raise UncacheableError(REASON_REPO_HEAD_UNAVAILABLE, rid)


class WorktreeProbe(_GitReaders):
    """Store guard (3): a snapshot of TRACKED worktree changes (ADR-0019 D13).

    The coordinator calls it lazily (only when a store is about to happen) and maps a failure to
    "not storable", never to "ineligible".
    """

    def snapshot(
        self,
        repo_paths: Mapping[str, str],
        workspace_root: str,
        exclude_abs: frozenset[str] | set[str],
    ) -> frozenset[tuple[object, ...]]:
        """`{(toplevel, path, index_code, worktree_code, mtime_ns | None, size | None)}` for every
        tracked change in every git repo of the set, EXCLUDING paths under `<ws>/.orchestrator`
        and the declared outputs in *exclude_abs*. Untracked files are never listed.
        """
        ws = os.path.normpath(os.path.abspath(workspace_root))
        state_dir = os.path.join(ws, CACHE_DIR_PARTS[0])
        entries: set[tuple[object, ...]] = set()
        try:
            tops = {find_git_toplevel(p, workspace_root=ws) for p in repo_paths.values()}
            for top in sorted(t for t in tops if t is not None):
                for se in self._repo(top).status_porcelain(top, untracked=False):
                    path = os.path.normpath(os.path.join(top, se.path))
                    if path in exclude_abs or _within(path, state_dir):
                        continue
                    try:
                        st = os.lstat(path)
                        sig: tuple[int | None, int | None] = (st.st_mtime_ns, st.st_size)
                    except FileNotFoundError:
                        sig = (None, None)
                    entries.add((top, se.path, se.index, se.worktree, *sig))
        except (*_GIT_FAILURES, UncacheableError) as e:
            raise UncacheableError(REASON_REPO_WORKTREE_PROBE_FAILED, type(e).__name__) from e
        return frozenset(entries)
