"""Deterministic naming, XDG state resolution, and the `effective_path` remap rule
(E-Wk9Tz3 T-Wk3Nv6, HLD §7.2/§11 M3).

Pure functions only -- no subprocess, no git, no filesystem reads (directory *creation* is
`worktrees.py`'s job, not this module's). Import-safe even when `git` is not installed on
the host: nothing here shells out or requires the binary to exist (AC-1).

Ownership boundary: `isolation/git.py` (`T-Gt4Pw8`) is the single choke point for actual git
subprocess calls; this module never calls it. `isolation/worktrees.py` (also `T-Wk3Nv6`)
is the impure lifecycle layer built on top of both this module and `git.py`.
"""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from ..xdg import resolve_state_dir

# ---------------------------------------------------------------------------------------
# Constants (no magic literals at call sites)
# ---------------------------------------------------------------------------------------

# Every ao-created ref/branch lives under this namespace (HLD §14 S-8: documented reserved
# namespace; `ensure()`'s hard-error-on-collision is the enforcement, in `worktrees.py`).
AO_REF_NAMESPACE = "ao"

# Env var names (mirrors `service/paths.py`'s own convention: read fresh at call time,
# never cached, so tests can monkeypatch per-test without import-order fragility).
STATE_ENV = "AO_STATE_DIR"
WORKTREE_ENV = "AO_WORKTREE_ROOT"

# `xdg.resolve_state_dir`'s xdg_subdir/default_subdir for ao's OWN (non-service) state dir:
# `$AO_STATE_DIR` (exact dir) > `$XDG_STATE_HOME/ao` > `~/.local/state/ao`.
_STATE_XDG_SUBDIR = "ao"
_STATE_DEFAULT_SUBDIR = "ao"
_WORKTREES_SUBDIR = "worktrees"

# `sanitize_ref_component` bounds and rules (git ref-format rules + PATH_MAX defense --
# TASK.md Risks: a deep run_id/task_id could otherwise approach PATH_MAX).
_MAX_REF_COMPONENT_LEN = 80
_INVALID_REF_CHARS_RE = re.compile(r"[^A-Za-z0-9._-]")
_MULTI_DOT_RE = re.compile(r"\.\.+")
_LOCK_SUFFIX = ".lock"
_LOCK_REPLACEMENT = "-lock"
_FALLBACK_COMPONENT = "x"
_STRIP_CHARS = "-."
# C-1 (review, 2026-09-07): over the 80-char bound, HLD §11 M3's own Edge Cases prose says
# "hash-shorten", not plain-truncate -- a bare `out[:80]` lets two ids that agree on their
# first 80 sanitized characters (plausible for LLM-generated/agent-supplied ids, which have
# no schema `maxLength`) collide onto the identical branch name AND worktree path, silently
# merging two supposedly-isolated tasks. The suffix is a hash of the ORIGINAL (pre-
# sanitization) value, so it still diverges even when the sanitized prefix is identical.
_TRUNCATION_HASH_LEN = 8
_TRUNCATION_SEPARATOR = "-"
_TRUNCATED_PREFIX_LEN = _MAX_REF_COMPONENT_LEN - _TRUNCATION_HASH_LEN - len(_TRUNCATION_SEPARATOR)

# A task id that sanitizes to this component would collide with the integration branch
# `ao/<run_id>/integration` (V9 at validate time is the primary gate; `task_branch`'s assert
# is defence in depth against an injected id -- AC-3).
RESERVED_BRANCH_COMPONENTS: frozenset[str] = frozenset({"integration"})

# §7.2: paths under these workspace-root-relative directories are NEVER remapped into a
# worktree, even when they happen to lie inside an isolated repo's toplevel -- run state and
# orchestrator bookkeeping stay shared (R-15: this is `output_manifest_path` vs
# `task_manifest_path`'s containing rule).
RESERVED_SHARED_PREFIXES: frozenset[str] = frozenset({".orchestrator", ".ao"})

_WORKSPACE_KEY_HASH_LEN = 12


# ---------------------------------------------------------------------------------------
# sanitize_ref_component (AC-2, AC-3)
# ---------------------------------------------------------------------------------------


def sanitize_ref_component(value: str) -> str:
    """Map an arbitrary string (a task/run id -- possibly agent-supplied via an injected
    `emit_tasks` manifest, HLD §14) onto a string `git check-ref-format --allow-onelevel`
    always accepts as a single ref component.

    The allowlist character class (`[A-Za-z0-9._-]`) alone already rules out `/`, `@{`,
    control characters, etc. -- everything else here handles the git ref rules that class
    can't: no leading/trailing `.`/`-`, no `..` run, no `.lock` suffix, never empty, and a
    length bound (PATH_MAX defense, TASK.md Risks). Over the bound, the tail is a hash of
    the ORIGINAL value (C-1), not a bare truncation, so two ids sharing a long common prefix
    still diverge. The strip is applied both before AND after this step, since either one
    can re-expose a trailing `.`/`-`.
    """
    out = _INVALID_REF_CHARS_RE.sub("-", value)
    out = out.strip(_STRIP_CHARS)
    out = _MULTI_DOT_RE.sub(".", out)
    if out.endswith(_LOCK_SUFFIX):
        out = out[: -len(_LOCK_SUFFIX)] + _LOCK_REPLACEMENT
    if len(out) > _MAX_REF_COMPONENT_LEN:
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:_TRUNCATION_HASH_LEN]
        prefix = out[:_TRUNCATED_PREFIX_LEN].strip(_STRIP_CHARS)
        out = f"{prefix}{_TRUNCATION_SEPARATOR}{digest}" if prefix else digest
    out = out.strip(_STRIP_CHARS)
    return out or _FALLBACK_COMPONENT


# ---------------------------------------------------------------------------------------
# Branch / ref naming (AC-3, AC-4)
# ---------------------------------------------------------------------------------------


def task_branch(run_id: str, task_id: str) -> str:
    """`ao/<san(run_id)>/<san(task_id)>` -- the branch a task's worktree is created on.

    `ao/<run_id>` is never itself created as a branch (only this leaf and
    `integration_branch`'s sibling leaf ever exist), so there is no git D/F ref conflict
    between the two (AC-4).
    """
    san_run = sanitize_ref_component(run_id)
    san_task = sanitize_ref_component(task_id)
    # AC-3: defence in depth -- the fatal case is caught earlier, at validate time, by
    # T-Sc7Rm2's V9 rule. This assert exists for an id that reaches here despite that (e.g.
    # a future caller that skips validation), not as the primary gate.
    assert san_task not in RESERVED_BRANCH_COMPONENTS, (
        f"task id {task_id!r} sanitizes to reserved branch component {san_task!r} "
        f"(collides with the integration branch ao/{san_run}/integration)"
    )
    return f"{AO_REF_NAMESPACE}/{san_run}/{san_task}"


def integration_branch(run_id: str) -> str:
    """`ao/<san(run_id)>/integration` -- the run's shared integration branch."""
    return f"{AO_REF_NAMESPACE}/{sanitize_ref_component(run_id)}/integration"


def squash_ref(run_id: str, task_id: str, n: int) -> str:
    """`refs/ao/runs/<san(run)>/<san(task)>/squash-<n>` -- kept alive for T3 rerun/audit."""
    san_run = sanitize_ref_component(run_id)
    san_task = sanitize_ref_component(task_id)
    return f"refs/{AO_REF_NAMESPACE}/runs/{san_run}/{san_task}/squash-{n}"


# ---------------------------------------------------------------------------------------
# Workspace / state-dir / worktree-root resolution (AC-5, AC-6)
# ---------------------------------------------------------------------------------------


def workspace_key(workspace_root: str) -> str:
    """A stable, filesystem-safe key for *workspace_root*, distinct for two roots whose
    basenames collide (`/a/proj` vs `/b/proj` -- AC-6).

    Lexical only (`os.path.abspath`/`normpath`, never `Path.resolve()`): this module does no
    filesystem I/O, so two different symlinks are NOT collapsed to one key here.
    """
    normalized = os.path.normpath(os.path.abspath(workspace_root))
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:_WORKSPACE_KEY_HASH_LEN]
    basename = sanitize_ref_component(os.path.basename(normalized) or "root")
    return f"{basename}-{digest}"


def state_dir() -> Path:
    """`$AO_STATE_DIR` (exact dir) > `$XDG_STATE_HOME/ao` > `~/.local/state/ao` (AC-5).

    R-11 (DRY): this would otherwise be a THIRD hand-copy of the same env->XDG->default
    shape (`service/paths.py::default_state_dir`, `isolation/git.py`'s `EMPTY_HOOKS_DIR`
    resolution are the other two) -- factored once as `xdg.resolve_state_dir`.
    """
    return resolve_state_dir(STATE_ENV, _STATE_XDG_SUBDIR, _STATE_DEFAULT_SUBDIR)


def worktree_root_prefix_for(workspace_root: str, run_id: str) -> Path:
    """The directory prefix common to every worktree a given run creates -- one level above
    `worktree_root`'s task_id/repo_key components.

    R-6 (AC-16): the sole input to every `GitRepo.prune_worktrees_scoped` call site in
    `worktrees.py`, so pruning is always scoped to this run, never global. Also honours
    `AO_WORKTREE_ROOT` (AC-5), read fresh at call time like every other env lookup here.
    """
    override = os.environ.get(WORKTREE_ENV)
    base = Path(override) if override else state_dir() / _WORKTREES_SUBDIR
    return base / workspace_key(workspace_root) / sanitize_ref_component(run_id)


def worktree_root(workspace_root: str, run_id: str, task_id: str, repo_key: str) -> Path:
    """Per-repo worktree directory: `<worktree_root_prefix_for(ws, run)>/<task_id>/<repo_key>`."""
    return (
        worktree_root_prefix_for(workspace_root, run_id)
        / sanitize_ref_component(task_id)
        / sanitize_ref_component(repo_key)
    )


# ---------------------------------------------------------------------------------------
# effective_path (AC-7, HLD §7.2 exactly)
# ---------------------------------------------------------------------------------------


class _RepoIsolationLike(Protocol):
    """Structural shape `effective_path` needs from one repo's isolation record.

    A `Protocol`, not an import of `worktrees.RepoIsolation`, so this module stays
    dependency-free of the impure lifecycle module (no circular import, and paths.py stays
    import-safe without git -- AC-1). Read-only (`@property`) members: a plain dataclass
    field satisfies a read-only protocol property structurally, which is what lets
    `Sequence[_RepoIsolationLike]` below accept a concrete `list[RepoIsolation]` covariantly
    -- a *mutable*-attribute Protocol member would require exact (invariant) type matches.
    """

    @property
    def toplevel(self) -> str: ...
    @property
    def worktree_root(self) -> str: ...


class _TaskIsolationLike(Protocol):
    @property
    def workspace_root(self) -> str: ...
    @property
    def repos(self) -> Sequence[_RepoIsolationLike]: ...


def effective_path(resolved_abs: str, task_iso: _TaskIsolationLike | None) -> str:
    """HLD §7.2's one remapping rule, exactly:

    - `task_iso is None` -> unchanged (not an isolated task).
    - under `<workspace_root>/.orchestrator` or `<workspace_root>/.ao` -> unchanged, even
      when that happens to also be inside an isolated repo's toplevel (run state stays
      shared -- R-15).
    - inside an isolated repo (innermost/longest toplevel wins for nested repos) -> remapped
      into that repo's worktree; the repo's own toplevel remaps to the worktree root itself.
    - outside every isolated repo -> unchanged.
    """
    if task_iso is None:
        return resolved_abs

    normalized = os.path.normpath(resolved_abs)
    workspace_root = os.path.normpath(task_iso.workspace_root)
    for reserved in RESERVED_SHARED_PREFIXES:
        reserved_abs = os.path.normpath(os.path.join(workspace_root, reserved))
        if normalized == reserved_abs or normalized.startswith(reserved_abs + os.sep):
            return resolved_abs

    # Longest (innermost) toplevel first, so a repo nested inside another isolated repo's
    # toplevel wins over its outer container.
    repos = sorted(task_iso.repos, key=lambda r: len(os.path.normpath(r.toplevel)), reverse=True)
    for repo in repos:
        toplevel = os.path.normpath(repo.toplevel)
        if normalized == toplevel:
            return repo.worktree_root
        if normalized.startswith(toplevel + os.sep):
            rel = os.path.relpath(normalized, toplevel)
            return os.path.join(repo.worktree_root, rel)

    return resolved_abs
