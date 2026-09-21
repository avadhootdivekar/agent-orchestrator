"""T1 mechanical conflict resolution -- repair conflicts for free, no LLM, no file
contents held in orchestrator memory (E-Wk9Tz3 T-Rm2Lx7, HLD §11 M6).

`resolve_mechanically` is the `ResolverHook` (`isolation/integrator.py`, T-Ib5Qy9)
implementation this module ships: `Integrator` calls it on every real rebase conflict,
injected by the caller that wires `IntegrationSpec` into an `Integrator` instance. This
module never imports `integrator.py` -- it only conforms to the `ResolverHook` Protocol's
call shape (verified structurally by `tests/isolation/test_resolvers.py`).

Precedence, pure and git/filesystem-free (`plan_resolution`, HLD §11 M6, verbatim):
already-resolved (rerere replay) -> regenerate (first matching rule wins, declared
order) -> union (first matching glob wins, declared order) -> unresolved.

Three built-in techniques:
  * ``rerere``     -- recognition only. Every git call already carries
                       ``-c rerere.enabled=true -c rerere.autoupdate=true``
                       (`isolation/git.py` `RERERE_ARGS`), so by the time this module
                       runs a path may already be resolved+staged by git itself. This
                       module RE-QUERIES git truth to recognize that (never trusts a
                       stale list) and counts/events it as tier T1 -- S-5: never silently
                       "free"/untracked.
  * ``union``       -- registry-style files (`mod.rs`, `__init__.py`, barrel/changelog
                       globs configured via `ResolverConfig.union`; empty by default,
                       AC-8). Operates purely over INDEX STAGES (`GitRepo.show_stage`),
                       never a global git merge driver, and REFUSES (leaves the path
                       untouched) whenever a hunk is not purely additive, or the
                       conflict shape isn't add/add-with-base (binary, add/add-no-base,
                       add/delete, delete/modify all decline).
  * ``regenerate``  -- lockfile-style paths matched by `ResolverConfig.regenerate`
                       rules: pick a stage (`rule.take`), run the rule's own argv with
                       ITS OWN `rule.timeout_seconds` bound (S-6 -- never the run-wide
                       integration lock timeout, since this runs while that per-repo
                       lock is held), then `git add -A` (a regeneration may legitimately
                       touch sibling files, e.g. a lockfile regen also rewriting a
                       manifest checksum).

NFR-1: this module never calls Python's `open(`/`.read_text(` on a repository file --
every byte that leaves git is fetched via a `GitRepo` subprocess (`show_stage`) and
either handed straight to another `GitRepo` call (`merge_file_union`) or written out with
`Path.write_bytes` (write-only, never read back through Python I/O). `tests/isolation/
test_resolvers.py` enforces this with a source-grep, not a comment.

S-1 (review C-1, E-Wk9Tz3 T-Rm2Lx7): every git call in this module is routed through the
injected `GitRepo` (`show_stage`, `add_paths`, `add_all`, `conflicted_paths`,
`checkout_stage`, `checkout_merge`, `merge_file_union`) -- there is no local
`subprocess`-based git invocation anywhere here. `checkout_stage`/`checkout_merge`/
`merge_file_union` were added to `GitRepo` (`isolation/git.py`) by this review round
specifically so this module never needs to shell out to git itself. The ONE remaining
`subprocess.run` call in this file is `RegenerateResolver`'s execution of the spec-
supplied `rule.command` -- that is arbitrary user argv, not a git invocation, so it
deliberately stays a locally hardened (no `shell=True`, explicit argv/cwd/env/timeout)
subprocess call, matching every other user-command dispatch in this codebase
(`executors/claude_cli.py`, `integrator.py`'s `verify_command`).
"""

from __future__ import annotations

import fnmatch
import logging
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar, Literal

from ..errors import GitError
from ..models import TIER_MECHANICAL, RegenerateRule, ResolverConfig
from .git import GIT_STDERR_TAIL_BYTES, GitRepo, StatusEntry

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------------------
# Constants (no magic literals at call sites)
# ---------------------------------------------------------------------------------------

RESOLVER_RERERE: Literal["rerere"] = "rerere"
RESOLVER_UNION: Literal["union"] = "union"
RESOLVER_REGENERATE: Literal["regenerate"] = "regenerate"

# `git show :<stage>:<path>` stage numbers (git plumbing convention -- never renumbered).
STAGE_BASE = 1
STAGE_OURS = 2
STAGE_THEIRS = 3

TAKE_OURS: Literal["ours"] = "ours"
TAKE_THEIRS: Literal["theirs"] = "theirs"

# HLD §8.4's per-tier event, emitted here at PATH granularity (`resolver=<rerere|union|
# regenerate>`) -- `Integrator` separately emits its own coarser per-REPO `EVENT_RESOLVED`
# (`isolation/integrator.py`); the two are complementary, not duplicates.
EVENT_RESOLVED = "integration.resolved"
EVENT_REGENERATE_TIMEOUT = "integration.resolver_regenerate_timeout"
EVENT_REGENERATE_ERROR = "integration.resolver_regenerate_error"
EVENT_MERGE_FILE_ERROR = "integration.resolver_merge_file_error"


# ---------------------------------------------------------------------------------------
# Locked result shapes (TASK.md "Schemas / Interface Notes")
# ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ResolutionStep:
    path: str
    resolver: Literal["rerere", "union", "regenerate"]
    rule: str | None = None  # matched glob (union) / matched rule glob (regenerate)


@dataclass(frozen=True)
class ResolutionPlan:
    steps: list[ResolutionStep] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------------------
# plan_resolution -- PURE (no git, no filesystem)
# ---------------------------------------------------------------------------------------


def _first_regenerate_rule(rules: list[RegenerateRule], path: str) -> RegenerateRule | None:
    """First matching rule in DECLARED order (HLD §11 M6: "first match wins")."""
    for rule in rules:
        if fnmatch.fnmatch(path, rule.glob):
            return rule
    return None


def _first_union_glob(globs: list[str], path: str) -> str | None:
    for glob in globs:
        if fnmatch.fnmatch(path, glob):
            return glob
    return None


def plan_resolution(
    conflicted: list[str], cfg: ResolverConfig, already_resolved: set[str]
) -> ResolutionPlan:
    """Precedence ladder (HLD §11 M6, verbatim): already-resolved -> regenerate (first
    matching rule, declared order) -> union (first matching glob, declared order) ->
    unresolved. Pure: never touches git or the filesystem, so it is unit-testable without
    either. Deterministic regardless of *conflicted*'s input order: always iterates
    ``sorted(conflicted)``.
    """
    steps: list[ResolutionStep] = []
    unresolved: list[str] = []
    for path in sorted(conflicted):
        if path in already_resolved:
            steps.append(ResolutionStep(path, RESOLVER_RERERE, None))
            continue
        rule = _first_regenerate_rule(cfg.regenerate, path)
        if rule is not None:
            steps.append(ResolutionStep(path, RESOLVER_REGENERATE, rule.glob))
            continue
        glob = _first_union_glob(cfg.union, path)
        if glob is not None:
            steps.append(ResolutionStep(path, RESOLVER_UNION, glob))
            continue
        unresolved.append(path)
    return ResolutionPlan(steps=steps, unresolved=unresolved)


# ---------------------------------------------------------------------------------------
# Pluggable resolver registry (name -> class). `apply_plan`'s dispatch loop never needs
# editing to add a technique (review C-6: `plan_resolution`'s own precedence branches are
# NOT covered by that claim -- a genuinely new, independently-triggered T1 technique still
# needs its own `plan_resolution` branch and a widened `ResolutionStep.resolver` Literal;
# this registry only makes `apply_plan`'s *dispatch* zero-core-change).
# ---------------------------------------------------------------------------------------


class MechanicalResolver(ABC):
    """One pluggable T1 technique, dispatched by `apply_plan` via `RESOLVER_REGISTRY`.

    Must touch only *step.path* in the worktree (the one documented exception: a
    `regenerate` command may legitimately rewrite sibling files, e.g. a lockfile
    regeneration also touching a manifest checksum -- HLD §11 M6). Returns ``True`` when
    it resolved AND staged (`git.add_paths`/`git.add_all`) the path; ``False`` to leave
    conflict markers intact and the path unstaged, for T2/T3 escalation.
    """

    name: ClassVar[str]

    @abstractmethod
    def apply(
        self,
        step: ResolutionStep,
        *,
        worktree: str,
        git: GitRepo,
        env: dict[str, str],
        cfg: ResolverConfig,
    ) -> bool: ...


RESOLVER_REGISTRY: dict[str, type[MechanicalResolver]] = {}


def register_resolver(cls: type[MechanicalResolver]) -> type[MechanicalResolver]:
    """Class decorator: registers *cls* under its own `.name` in `RESOLVER_REGISTRY`.
    Extending the mechanical ladder with a new technique never requires editing
    `apply_plan`'s dispatch loop -- only a new `MechanicalResolver` subclass plus
    (for a config-driven one) a `plan_resolution` precedence branch.
    """
    if not getattr(cls, "name", None):
        raise ValueError(f"{cls.__name__}.name must be a non-empty resolver name")
    RESOLVER_REGISTRY[cls.name] = cls
    return cls


def _looks_binary(data: bytes) -> bool:
    """Standard NUL-byte heuristic (matches git's own binary-file detection) -- operates
    on bytes already fetched via a `GitRepo` subprocess, never a fresh file read."""
    return b"\x00" in data


def _is_subsequence(needle: list[bytes], haystack: list[bytes]) -> bool:
    """True iff every line of *needle* appears in *haystack*, in order (not necessarily
    contiguous). Used to prove a hunk is PURELY additive: every one of base's lines must
    still be present, in order, on both sides -- a side that modified or deleted an
    existing line fails this check.
    """
    it = iter(haystack)
    return all(line in it for line in needle)


def _purely_additive(base: bytes, ours: bytes, theirs: bytes) -> bool:
    base_lines = base.splitlines()
    return _is_subsequence(base_lines, ours.splitlines()) and _is_subsequence(
        base_lines, theirs.splitlines()
    )


@register_resolver
class UnionResolver(MechanicalResolver):
    """Union merge over index stages (HLD §11 M6). Declines (returns `False`) whenever
    the merge cannot be MEANINGFUL: missing base (add/add), a missing side (add/delete,
    delete/modify), a binary blob on any side, or a hunk that is not purely additive on
    both sides (AC-3).
    """

    name: ClassVar[str] = RESOLVER_UNION

    def apply(
        self,
        step: ResolutionStep,
        *,
        worktree: str,
        git: GitRepo,
        env: dict[str, str],
        cfg: ResolverConfig,
    ) -> bool:
        base = git.show_stage(worktree, STAGE_BASE, step.path)
        ours = git.show_stage(worktree, STAGE_OURS, step.path)
        theirs = git.show_stage(worktree, STAGE_THEIRS, step.path)
        if base is None or ours is None or theirs is None:
            return False  # add/add-no-base, add/delete, or delete/modify
        if _looks_binary(base) or _looks_binary(ours) or _looks_binary(theirs):
            return False
        if not _purely_additive(base, ours, theirs):
            return False
        # review C-1: routed through `GitRepo.merge_file_union` (S-1 choke point), not a
        # local subprocess call. A genuine infrastructure failure (timeout, git
        # unavailable) raises `GitError` -- caught here so ONE path declining never
        # crashes the whole resolution batch (review C-4: logged, not silently dropped).
        try:
            merged = git.merge_file_union(ours, base, theirs)
        except GitError as exc:
            logger.warning(
                EVENT_MERGE_FILE_ERROR,
                extra={"event": EVENT_MERGE_FILE_ERROR, "path": step.path, "error": str(exc)},
            )
            return False
        if merged is None:
            logger.warning(
                EVENT_MERGE_FILE_ERROR,
                extra={"event": EVENT_MERGE_FILE_ERROR, "path": step.path, "returncode": "nonzero"},
            )
            return False
        (Path(worktree) / step.path).write_bytes(merged)
        git.add_paths(worktree, [step.path])
        return True


def _rule_for_step(rules: list[RegenerateRule], step: ResolutionStep) -> RegenerateRule | None:
    for rule in rules:
        if rule.glob == step.rule:
            return rule
    return None


def _touched_paths(before: list[StatusEntry], after: list[StatusEntry]) -> set[str]:
    """Paths whose `git status --porcelain` entry differs between *before* and *after*
    (new, removed, or a changed index/worktree code) -- used to scope
    `RegenerateResolver`'s post-success `git add` to files the command actually touched,
    instead of a blind `git add -A` that would also silently stage (and thereby
    "resolve") any OTHER, unrelated path this same rebase left genuinely conflicted -- a
    regenerate command has no business resolving a sibling conflict it never looked at.
    Sourced from git's own status (review C-5), not a filesystem mtime walk: unaffected
    by timestamp-granularity races, and git already computes exactly this diff for free.
    """
    before_map = {e.path: (e.index, e.worktree) for e in before}
    after_map = {e.path: (e.index, e.worktree) for e in after}
    changed = {p for p, status in after_map.items() if before_map.get(p) != status}
    removed = set(before_map) - set(after_map)
    return changed | removed


@register_resolver
class RegenerateResolver(MechanicalResolver):
    """Regenerate a lockfile-style path via its matching `RegenerateRule`'s command (HLD
    §11 M6). S-6: bounded by `rule.timeout_seconds` -- never `cfg.lock_timeout_seconds` --
    because this runs while the per-repo integration lock is held; a hung command here
    would otherwise stall every other task touching the repo for up to 1800s.

    Git rebase gotcha (documented here because it is genuinely confusing and load-bearing
    for `rule.take`): during a REBASE -- unlike a merge -- "ours"/"theirs" are swapped.
    "ours" (`git checkout --ours`, index stage 2) is the branch being rebased ONTO (the
    integration target, i.e. other tasks' already-landed work); "theirs" (stage 3) is the
    branch whose commit is being replayed (THIS task's own squash commit). `rule.take`
    passes straight through to `git checkout --ours/--theirs`, matching that git-native
    meaning exactly -- the default `"theirs"` therefore selects the TASK's own new content
    by default, which is normally what you want to regenerate a lockfile from.

    A regeneration may legitimately touch sibling files (e.g. rewriting a manifest
    checksum alongside the lockfile itself) -- on success, every path `git status`
    reports as changed during the command's run is staged, not a blind `git add -A`: the
    worktree can contain OTHER, unrelated paths this same rebase left genuinely
    conflicted, and `add -A` would silently "resolve" (stage, markers and all) any of
    those it never touched.
    """

    name: ClassVar[str] = RESOLVER_REGENERATE

    def apply(
        self,
        step: ResolutionStep,
        *,
        worktree: str,
        git: GitRepo,
        env: dict[str, str],
        cfg: ResolverConfig,
    ) -> bool:
        rule = _rule_for_step(cfg.regenerate, step)
        if rule is None:
            return False  # step.rule no longer matches any configured rule
        stage = STAGE_OURS if rule.take == TAKE_OURS else STAGE_THEIRS
        if git.show_stage(worktree, stage, step.path) is None:
            return False  # e.g. an add/delete conflict has no content on this side
        # `checkout_stage` writes ONLY the worktree file -- the index keeps its three
        # unmerged stages untouched, so a failure below can cleanly restore the original
        # conflict markers via `checkout_merge` rather than needing to have cached bytes
        # ourselves (NFR-1: no repository file content read into Python). review C-1:
        # routed through `GitRepo`, not a local subprocess call -- the ONE remaining
        # `subprocess.run` in this module is the spec-supplied `rule.command` itself,
        # which is not a git invocation.
        git.checkout_stage(worktree, rule.take, step.path)
        before_status = git.status_porcelain(worktree, untracked=True)
        try:
            cp = subprocess.run(  # noqa: S603 -- fixed argv from spec, no shell
                list(rule.command),
                cwd=worktree,
                env=env,
                timeout=rule.timeout_seconds,
                capture_output=True,
                check=False,
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired:
            logger.warning(
                EVENT_REGENERATE_TIMEOUT,
                extra={
                    "event": EVENT_REGENERATE_TIMEOUT,
                    "path": step.path,
                    "resolver": RESOLVER_REGENERATE,
                    "timeout_seconds": rule.timeout_seconds,
                },
            )
            git.checkout_merge(worktree, step.path)
            return False
        except OSError as exc:
            logger.warning(
                EVENT_REGENERATE_ERROR,
                extra={
                    "event": EVENT_REGENERATE_ERROR,
                    "path": step.path,
                    "resolver": RESOLVER_REGENERATE,
                    "error": str(exc),
                },
            )
            git.checkout_merge(worktree, step.path)
            return False
        if cp.returncode != 0:
            # review C-4: this is, functionally, the primary error path (the regenerate
            # command RAN and rejected the input) -- log it like its Timeout/OSError
            # siblings, with the exit code and a bounded stderr tail to actually debug.
            logger.warning(
                EVENT_REGENERATE_ERROR,
                extra={
                    "event": EVENT_REGENERATE_ERROR,
                    "path": step.path,
                    "resolver": RESOLVER_REGENERATE,
                    "returncode": cp.returncode,
                    "stderr_tail": cp.stderr[-GIT_STDERR_TAIL_BYTES:].decode(
                        "utf-8", errors="replace"
                    ),
                },
            )
            git.checkout_merge(worktree, step.path)
            return False
        # Stage step.path plus whatever else the command actually touched (sibling
        # manifest files, e.g.) -- never an unscoped `add -A` (class docstring).
        after_status = git.status_porcelain(worktree, untracked=True)
        touched = _touched_paths(before_status, after_status)
        touched.add(step.path)
        git.add_paths(worktree, sorted(touched))
        return True


def _log_resolved(step: ResolutionStep) -> None:
    logger.info(
        EVENT_RESOLVED,
        extra={
            "event": EVENT_RESOLVED,
            "tier": TIER_MECHANICAL,
            "resolver": step.resolver,
            "path": step.path,
        },
    )


# ---------------------------------------------------------------------------------------
# apply_plan
# ---------------------------------------------------------------------------------------


def apply_plan(
    plan: ResolutionPlan,
    worktree: str,
    git: GitRepo,
    env: dict[str, str],
    cfg: ResolverConfig,
) -> list[str]:
    """Apply *plan*'s steps in *worktree* (HLD §11 M6). Returns the paths STILL
    unresolved -- by RE-QUERYING `git.conflicted_paths` after applying (AC-6), never by
    trusting this function's own bookkeeping (a regenerate command can exit 0 and still
    leave its target genuinely conflicted).

    *cfg* is required here (beyond the four-argument shape named in TASK.md's interface
    note) so `RegenerateResolver` can re-look-up the full `RegenerateRule` (command/
    timeout/take) a `ResolutionStep` only carries by its matched glob string -- see the
    module docstring's "Interface change request".
    """
    for step in plan.steps:
        if step.resolver == RESOLVER_RERERE:
            # git already wrote + staged this path (rerere autoupdate) -- nothing to
            # apply, but S-5 requires it counted/evented as tier T1, never silently free.
            _log_resolved(step)
            continue
        resolver_cls = RESOLVER_REGISTRY.get(step.resolver)
        if resolver_cls is None:  # pragma: no cover -- defensive; plan_resolution only
            continue  # ever emits registered names
        if resolver_cls().apply(step, worktree=worktree, git=git, env=env, cfg=cfg):
            _log_resolved(step)
    return git.conflicted_paths(worktree)


# ---------------------------------------------------------------------------------------
# resolve_mechanically -- the ResolverHook entry point (isolation/integrator.py)
# ---------------------------------------------------------------------------------------


def resolve_mechanically(
    conflicted_paths: list[str],
    *,
    worktree: str,
    git: GitRepo,
    config: ResolverConfig,
    env: dict[str, str],
) -> list[str]:
    """`ResolverHook` implementation (`isolation/integrator.py`'s `ResolverHook`
    Protocol, T-Ib5Qy9) -- T1 mechanical resolution, HLD §11 M6.

    Recognizes any path already resolved by git's own rerere replay (re-queried from
    live git state, never assumed), then attempts `config`-driven regenerate/union
    resolution for the rest, staging whatever it fixes via `git.add_paths`/`git.add_all`.
    Leaves every path it cannot resolve completely untouched -- markers intact, unstaged
    -- for the caller's T2/T3 escalation. Never rewrites a path outside *conflicted_paths*
    (the one documented exception is `RegenerateResolver`'s `git add -A` sibling-file
    sweep after a SUCCESSFUL regenerate, matching HLD §11 M6's own carve-out).
    """
    if not conflicted_paths:
        return []
    already_resolved: set[str] = set()
    if config.rerere:
        # S-5: recognize (never assume) a path git's OWN rerere already resolved+staged
        # since the caller captured *conflicted_paths* -- re-queried from git truth, the
        # same "never trust bookkeeping" principle `apply_plan`'s return uses (AC-6).
        still_conflicted = set(git.conflicted_paths(worktree))
        already_resolved = {p for p in conflicted_paths if p not in still_conflicted}
    # `resolvers.rerere: false` (AC-5's second test): no credit/eventing as rerere even if
    # a path happens to already be resolved -- and this branch never writes any git config
    # of its own either way (rerere is driven entirely by the *git* argument's own
    # per-invocation `-c rerere.*` flags, set once at its construction by the caller).
    plan = plan_resolution(conflicted_paths, config, already_resolved)
    return apply_plan(plan, worktree, git, env, config)
