"""`Integrator` -- land one task's work on the run's shared integration ref, or report
precisely why it could not (E-Wk9Tz3 T-Ib5Qy9, HLD §8, §11 M4).

Squash (`GitRepo.commit_tree`, deterministic) -> rebase onto the current integration head
(`GitRepo.rebase_onto`) -> on a real conflict, the injected `resolver_hook` (T1, mechanical
-- `T-Rm2Lx7`'s `resolvers.py`) then the injected `escalation_hook` (T2/T3/T4 --
`T-Lr6Ka3`'s `escalation.py`) -> verify once on the post-rebase tree -> compare-and-swap
`update-ref` per repo, all under a per-repository `IntegrationLock` acquired in sorted
repo-key order (no deadlock possible across multi-repo tasks).

R-20 / NFR-3: `Integrator` holds no `RunState` and never will. Everything it needs from
run-wide integration bookkeeping arrives per call as a `RunIntegrationSnapshot` (an
immutable *value*, constructed fresh by the caller -- `T-En8Hd4`, main thread -- from the
live `RunIntegrationState`, never a reference to it); everything it needs from this task's
own bookkeeping arrives as a `TaskIntegrationState` value the caller likewise passes by
value. `integrate()`/`resume_integration()` run on a WORKER thread (ADR-0007), and neither
of those input types is `RunState` itself nor exposes a `.save()` -- there is nothing here
for a worker thread to illegally mutate.

Every git call is routed through `GitRepo` (`isolation/git.py`, `T-Gt4Pw8`) via its public
surface only -- the default structural verify check uses `GitRepo.grep_conflict_markers`/
`.diff_check` (review C-3: added to `git.py`, authorized as part of this ticket's fix pass,
replacing an earlier `_run`/`_raise` reach-across this module used before those wrappers
existed).
"""

from __future__ import annotations

import dataclasses
import fnmatch
import logging
import os
import subprocess
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal, Protocol

from ..errors import GitError
from ..models import (
    ON_DENYLISTED_FAIL,
    ON_DENYLISTED_WARN,
    TIER_AUTO,
    TIER_LLM,
    TIER_MECHANICAL,
    IntegrationSpec,
    ResolverConfig,
    ResolverTier,
    TaskIntegrationState,
)
from . import paths
from .git import GitRepo, Runner
from .locks import IntegrationLock
from .paths import effective_path
from .worktrees import RepoIsolation, TaskIsolation

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------------------
# Constants (no magic literals at call sites)
# ---------------------------------------------------------------------------------------

ClockFn = Callable[[], datetime]

STATUS_INTEGRATED: Literal["integrated"] = "integrated"
STATUS_CONFLICT_RESOLVER: Literal["conflict_resolver"] = "conflict_resolver"
STATUS_CONFLICT_RERUN: Literal["conflict_rerun"] = "conflict_rerun"
STATUS_FAILED: Literal["failed"] = "failed"
STATUS_EMPTY: Literal["empty"] = "empty"

VERIFY_NOT_RUN: Literal["not_run"] = "not_run"
VERIFY_PASSED: Literal["passed"] = "passed"
VERIFY_FAILED: Literal["failed"] = "failed"

CAUSE_CONFLICT: Literal["conflict"] = "conflict"
CAUSE_VERIFY: Literal["verify"] = "verify"

# `reason` values this module itself assigns (an `escalation_hook` may assign others, e.g.
# "conflict_unresolved"/"verify_unresolved" -- that vocabulary is T-Lr6Ka3's, not ours).
REASON_LOCK_TIMEOUT = "lock_timeout"
REASON_DENYLISTED_PATH = "denylisted_path"
REASON_REF_RACE = "ref_race"
REASON_VERIFY_COMMAND_ERROR = "verify_command_error"
REASON_VERIFY_TIMEOUT = "verify_timeout"
REASON_VERIFY_COMMAND_FAILED = "verify_command_failed"
REASON_GIT_ERROR = "git_error"

# HLD "Triggers / events" (TASK.md Schemas/Interface Notes) plus two natural extras this
# module also emits: `denylisted_path` (named explicitly by AC-18/S-3) and `resolved`
# (HLD §8.4's per-tier table) and `empty` (observability for the no-op landing path).
EVENT_STARTED = "integration.started"
EVENT_SQUASHED = "integration.squashed"
EVENT_REBASED = "integration.rebased"
EVENT_CONFLICT = "integration.conflict"
EVENT_RESOLVED = "integration.resolved"
EVENT_VERIFY_STARTED = "integration.verify_started"
EVENT_VERIFY_PASSED = "integration.verify_passed"
EVENT_VERIFY_FAILED = "integration.verify_failed"
EVENT_MERGED = "integration.merged"
EVENT_PARTIAL = "integration.partial"
EVENT_FAILED = "integration.failed"
EVENT_DENYLISTED_PATH = "integration.denylisted_path"
EVENT_EMPTY = "integration.empty"

# R-7: a lost CAS is retried exactly this many times, scoped to the losing repo only.
CAS_RETRY_BOUND: int = 1

# HLD §8.3: verify's captured stdout/stderr are size-capped (only the exit code enters
# engine memory as a decision input).
VERIFY_CAPTURE_CAP_BYTES: int = 1_048_576  # 1 MiB per stream

# Default commit message template (HLD §8.2), used for both the local auto-commit and the
# squash commit unless `spec.commit_message_template` overrides it. Only ids and shas are
# ever interpolated (AC-3: no file contents, no diffs).
DEFAULT_COMMIT_MESSAGE_TEMPLATE = (
    "ao({task_id}): {run_id}\n"
    "\n"
    "AO-Run-Id: {run_id}\n"
    "AO-Task-Id: {task_id}\n"
    "AO-Agent: {agent_id}\n"
    "AO-Base: {base_commit}\n"
    "AO-Attempt: {attempt}\n"
)

# `AO_*` env vars overlaid on `os.environ` for verify_command / regenerate-resolver
# subprocess env (HLD §8.3/§11 M6 "run env"; no `isolation.env`/agent-spec data reaches
# this module -- it never sees a TaskSpec/AgentSpec).
_ENV_RUN_ID = "AO_RUN_ID"
_ENV_TASK_ID = "AO_TASK_ID"
_ENV_ATTEMPT = "AO_ATTEMPT"

# review C-1 fix: `resume_integration`'s LOCKED signature carries no `agent_id` (unlike
# `integrate()`, which requires the caller to supply the actual dispatched agent). The one
# path that needs a commit message rendered from inside `resume_integration` is squashing a
# repo the ORIGINAL `integrate()` call never reached at all (a conflict earlier in
# repo-key order returned before this repo's turn) -- a rare, multi-repo-only edge case.
# This placeholder degrades the `AO-Agent` trailer's audit value for that one path only;
# it never affects the landed TREE content, which is identical either way.
_RESUME_FALLBACK_AGENT_ID = "unknown-resume-fallback"


# ---------------------------------------------------------------------------------------
# Run-scoped snapshot (R-20/NFR-3)
# ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class RunIntegrationSnapshot:
    """Immutable, worker-thread-safe view of the run-wide integration bookkeeping this
    module needs -- NEVER a live reference to `RunState` or its `RunIntegrationState`
    field. The caller (`T-En8Hd4`, main thread) constructs a fresh one of these per
    dispatch from that state's current values; nothing here is mutated by `Integrator`.

    Deliberately minimal: `heads` is NOT carried here. The current integration head for
    each repo is read LIVE, under that repo's lock, inside `integrate()` -- a value handed
    in from a snapshot taken before dispatch could be stale by the time the lock is
    acquired (a sibling task may have landed in the meantime), which is exactly the race
    the per-repo lock + CAS exist to close. Trusting a stale snapshot value here would
    reopen it.
    """

    run_id: str
    # e.g. "ao/<run_id>/integration" -- `RunIntegrationState.branch`, verbatim.
    branch: str
    # Absolute `.orchestrator/runs/<run_id>` directory (already resolved through the
    # ArtifactStore by the caller); verify capture lands under
    # `<run_dir>/<task_id>/integration/attempt-<n>/verify.*`.
    run_dir: str


# ---------------------------------------------------------------------------------------
# Result shape (locked, HLD §11 M4)
# ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class IntegrationResult:
    status: Literal["integrated", "conflict_resolver", "conflict_rerun", "failed", "empty"]
    tier_reached: ResolverTier | None = None
    heads: dict[str, str] = field(default_factory=dict)
    squash: dict[str, str] = field(default_factory=dict)
    conflicted_paths: list[str] = field(default_factory=list)  # paths only (NFR-1)
    verify_status: Literal["not_run", "passed", "failed"] = VERIFY_NOT_RUN
    reason: str | None = None
    untracked_outputs: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------------------
# Injected hooks (T1 mechanical resolution, T2/T3/T4 escalation)
# ---------------------------------------------------------------------------------------


class ResolverHook(Protocol):
    """T1 mechanical resolution -- `T-Rm2Lx7`'s `resolvers.py`, injected. This module never
    imports that ticket's code; tests here inject a stub.

    Attempts rerere/union/regenerate resolution for every path in *conflicted_paths*
    (already `git add`ing whatever it fixes in *worktree*) and returns the paths that
    remain genuinely unresolved. An empty return means everything was resolved -- the
    caller then runs `git rebase --continue`.
    """

    def __call__(
        self,
        conflicted_paths: list[str],
        *,
        worktree: str,
        git: GitRepo,
        config: ResolverConfig,
        env: dict[str, str],
    ) -> list[str]: ...


class EscalationHook(Protocol):
    """T2/T3/T4 decision -- `T-Lr6Ka3`'s `escalation.py`, injected. This module never
    imports that ticket's code; tests here inject a stub.

    Receives a snapshot of this task's integration bookkeeping (never a live `RunState`
    reference) and the ladder-entry *cause*, and returns the full `IntegrationResult` for
    that decision (which tier/status the ladder assigns next). `Integrator` merges in the
    `heads`/`squash`/`conflicted_paths`/`verify_status` entries it already computed before
    returning the merged result to its own caller (`_merge_escalation`).
    """

    def __call__(
        self,
        task_integration: TaskIntegrationState,
        spec: IntegrationSpec,
        cause: Literal["conflict", "verify"],
    ) -> IntegrationResult: ...


# ---------------------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------------------


def _render_commit_message(
    spec: IntegrationSpec,
    *,
    run_id: str,
    task_id: str,
    agent_id: str,
    base_commit: str,
    attempt: int,
) -> str:
    """AC-3: only ids and shas are ever interpolated -- never file contents or a diff."""
    template = spec.commit_message_template or DEFAULT_COMMIT_MESSAGE_TEMPLATE
    return template.format(
        run_id=run_id,
        task_id=task_id,
        agent_id=agent_id,
        base_commit=base_commit,
        attempt=attempt,
    )


def _matches_denylist(path: str, denylist: list[str]) -> bool:
    """S-3: match both the full (worktree-relative) path and its basename, so
    `commit_denylist: [".env"]` also catches `config/.env`, not only a root-level `.env`.
    This is a footgun backstop, not a secrets scanner -- documented at the call site."""
    basename = os.path.basename(path)
    return any(
        fnmatch.fnmatchcase(path, glob) or fnmatch.fnmatchcase(basename, glob) for glob in denylist
    )


def _is_untracked(entry_index: str, entry_worktree: str) -> bool:
    return entry_index == "?" and entry_worktree == "?"


def _default_exec_runner(
    argv: list[str], *, cwd: str, env: dict[str, str] | None, timeout: float
) -> subprocess.CompletedProcess[bytes]:
    """Default `Runner` for `verify_command` execution (NOT a git call -- an arbitrary
    user-supplied argv, so it never goes through `GitRepo`). Never a shell."""
    return subprocess.run(  # noqa: S603
        argv, cwd=cwd, env=env, timeout=timeout, capture_output=True, check=False
    )


def _cap(data: bytes) -> bytes:
    return data[:VERIFY_CAPTURE_CAP_BYTES]


@dataclass(frozen=True)
class _VerifyOutcome:
    passed: bool
    reason: str | None
    exit_code: int | None


@dataclass(frozen=True)
class _StageResult:
    """Result of squashing/rebasing (or, on resume, continuing) ONE repo."""

    squash_sha: str
    conflict: bool
    new_sha: str | None
    unresolved_paths: list[str]
    tier: ResolverTier


# ---------------------------------------------------------------------------------------
# Integrator
# ---------------------------------------------------------------------------------------


class Integrator:
    """Land one task's work on the run's shared integration ref, or report exactly why it
    could not (HLD §11 M4). See the module docstring for the R-20/NFR-3 threading
    invariant this class exists to uphold.
    """

    def __init__(
        self,
        spec: IntegrationSpec,
        logger: logging.LoggerAdapter,  # noqa: A002 -- locked parameter name (TASK.md AC-2)
        clock: ClockFn,
        resolver_hook: ResolverHook,
        escalation_hook: EscalationHook,
        *,
        runner: Runner | None = None,
        hooks_dir: Path | None = None,
    ) -> None:
        self._spec = spec
        self._logger = logger
        self._clock = clock
        self._resolver_hook = resolver_hook
        self._escalation_hook = escalation_hook
        self._runner = runner
        self._hooks_dir = hooks_dir
        self._exec: Runner = runner if runner is not None else _default_exec_runner
        self._git_repos: dict[str, GitRepo] = {}

    # --- construction helpers --------------------------------------------------------

    def _git_for(self, repo: RepoIsolation) -> GitRepo:
        """Cached per repo-key. Constructed against `repo.toplevel` (the MAIN checkout,
        never the task's isolated worktree) -- an implicit invariant every call site must
        honor: any worktree-CONTENT-sensitive call (status, commit, rebase, reset, ...)
        must pass an explicit `cwd=repo.worktree_root` (or the local `wt` alias); only
        ref/branch-only calls (shared via the common dir, safe from any worktree) may omit
        it.
        """
        git = self._git_repos.get(repo.key)
        if git is None:
            # review C-2 (T-Rm2Lx7): `resolvers.rerere` is the documented escape hatch
            # for a bad cached rerere resolution (HLD OQ-4) -- it must reach the actual
            # `GitRepo` whose per-invocation `-c rerere.*` args drive replay, not just
            # `resolve_mechanically`'s own crediting/eventing of an already-resolved path.
            git = GitRepo(
                repo.toplevel,
                runner=self._runner,
                hooks_dir=self._hooks_dir,
                rerere=self._spec.resolvers.rerere,
            )
            self._git_repos[repo.key] = git
        return git

    def _base_env(self, run_id: str, task_id: str, attempt: int) -> dict[str, str]:
        return {
            **os.environ,
            _ENV_RUN_ID: run_id,
            _ENV_TASK_ID: task_id,
            _ENV_ATTEMPT: str(attempt),
        }

    def _log_event(
        self, task_id: str, event: str, *, level: int = logging.INFO, **fields: object
    ) -> None:
        self._logger.log(
            level,
            event,
            extra={"event": event, "task_id": task_id, "at": self._clock().isoformat(), **fields},
        )

    # --- public API --------------------------------------------------------------------

    def integrate(
        self,
        task_iso: TaskIsolation,
        run_integration: RunIntegrationSnapshot,
        task_integration: TaskIntegrationState,
        attempt: int,
        *,
        agent_id: str,
    ) -> IntegrationResult:
        """HLD §11 M4's step order, exactly: auto-commit (outside any lock) -> Empty check
        -> acquire every repo lock (sorted key order) -> squash + rebase per repo -> verify
        once -> CAS land per repo.

        *agent_id* is not carried on `TaskIsolation` (R-20/R-8 keep it free of `TaskSpec`)
        but IS required by the commit message template's `AO-Agent` trailer (AC-3), so it
        is threaded through as an explicit argument -- the caller already has it (it is
        dispatching this exact agent).
        """
        task_id = task_iso.task_id
        # C-4: defensive copy -- `TaskIntegrationState` is a plain mutable pydantic model,
        # not `frozen=True` (models.py). Every use site in this module reads it only via
        # `.model_copy(update=...)`, never assigns to it directly, but that discipline is
        # not enforced by the type system; copying here holds the R-20/NFR-3 guarantee
        # regardless of what the caller passes (e.g. a live `RunState.task_integration[tid]`
        # reference) or what a future edit to this file does.
        task_integration = task_integration.model_copy()
        self._log_event(task_id, EVENT_STARTED, attempt=attempt)
        try:
            tips, denylist_result = self._auto_commit_and_screen(
                task_iso, run_integration, attempt, agent_id
            )
            if denylist_result is not None:
                return denylist_result
            if self._is_empty(task_iso, tips):
                untracked = self._untracked_declared_outputs(task_iso)
                self._log_event(task_id, EVENT_EMPTY)
                return IntegrationResult(
                    status=STATUS_EMPTY, verify_status=VERIFY_NOT_RUN, untracked_outputs=untracked
                )
            return self._squash_rebase_verify_land(
                task_iso, run_integration, task_integration, attempt, tips, agent_id
            )
        except GitError as exc:
            self._log_event(
                task_id, EVENT_FAILED, level=logging.ERROR, reason=REASON_GIT_ERROR, error=str(exc)
            )
            return IntegrationResult(
                status=STATUS_FAILED, reason=REASON_GIT_ERROR, verify_status=VERIFY_NOT_RUN
            )

    def resume_integration(
        self,
        task_iso: TaskIsolation,
        run_integration: RunIntegrationSnapshot,
        task_integration: TaskIntegrationState,
        attempt: int,
    ) -> IntegrationResult:
        """Entered after a T2 resolver dispatch: the worktree is mid-rebase and the
        resolver agent has written resolutions. Re-acquires the lock(s), sweeps + continues
        the rebase, verifies, and lands -- never re-runs auto-commit or squash (AC-11).

        Scope note: unlike `integrate()`, a lost CAS here is NOT retried against a
        re-staged tree -- it is reported directly as `failed(reason="ref_race")` (with an
        `is_ancestor` short-circuit for the already-landed/crash-recovery case, same as
        `integrate()`). Retrying a resume's CAS loss would mean re-running the mechanical
        ladder from scratch mid-resume; a caller that hits `ref_race` here can simply
        dispatch a fresh `resume_integration` call, which is bounded, simpler, and does not
        duplicate `integrate()`'s restaging logic for a path with no dedicated AC in this
        ticket.
        """
        task_id = task_iso.task_id
        task_integration = task_integration.model_copy()  # C-4, see `integrate()`'s comment
        try:
            sorted_repos = sorted(task_iso.repos, key=lambda r: r.key)
            env = self._base_env(run_integration.run_id, task_id, attempt)
            with ExitStack() as stack:
                for repo in sorted_repos:
                    lock = IntegrationLock(repo.common_dir)
                    if not lock.acquire(self._spec.lock_timeout_seconds):
                        self._log_event(
                            task_id,
                            EVENT_FAILED,
                            level=logging.ERROR,
                            reason=REASON_LOCK_TIMEOUT,
                            repo=repo.key,
                        )
                        return IntegrationResult(
                            status=STATUS_FAILED,
                            reason=REASON_LOCK_TIMEOUT,
                            verify_status=VERIFY_NOT_RUN,
                        )
                    stack.callback(lock.release)

                squash: dict[str, str] = {}
                staged: dict[str, tuple[str, str]] = {}
                overall_tier: ResolverTier = TIER_LLM  # resume is only entered post-T2

                for repo in task_iso.repos:
                    git = self._git_for(repo)
                    wt = repo.worktree_root
                    branch_ref = f"refs/heads/{repo.branch}"
                    integration_ref = f"refs/heads/{run_integration.branch}"
                    head_sha = git.rev_parse(integration_ref)
                    expected_old = head_sha if head_sha is not None else ""
                    target_head = head_sha if head_sha is not None else repo.base

                    recorded_squash = git.rev_parse(
                        paths.squash_ref(run_integration.run_id, task_id, attempt)
                    )
                    squash[repo.key] = recorded_squash or ""

                    if git.rebase_in_progress(wt):
                        git.add_all(wt)
                        cont = git.rebase_continue(wt)
                        if not cont.clean:
                            ti_copy = task_integration.model_copy(
                                update={
                                    "conflicted_paths": list(cont.paths),
                                    "tier_reached": overall_tier,
                                }
                            )
                            escalated = self._escalation_hook(ti_copy, self._spec, CAUSE_CONFLICT)
                            return self._merge_escalation(
                                escalated, squash, overall_tier, list(cont.paths)
                            )
                        self._log_event(task_id, EVENT_RESOLVED, repo=repo.key, tier=TIER_LLM)
                        new_sha = git.rev_parse(branch_ref) or recorded_squash or ""

                        # C-1 (review, blocking): the rebase just completed was rebasing
                        # onto whatever target was fixed when it STARTED -- it cannot
                        # "follow" a ref move mid-rebase. If a sibling task landed into
                        # this same repo during the T2 dispatch window, `head_sha` (read
                        # fresh above) has moved since, and `new_sha`'s history does not
                        # contain it. `update-ref`'s CAS performs no ancestry check, so
                        # blindly landing `new_sha` against the fresh `expected_old` would
                        # silently make the sibling's commit unreachable -- a lost update,
                        # not a detected failure. Detect via ancestry (the same primitive
                        # the CAS already-landed short-circuit trusts) and, if stale,
                        # discard this now-outdated rebase result and restage fresh.
                        stale = (
                            head_sha is not None
                            and bool(new_sha)
                            and not git.is_ancestor(head_sha, new_sha)
                        )
                        if stale:
                            restage_squash = recorded_squash or new_sha or ""
                            result = self._restage_or_escalate(
                                task_id,
                                repo,
                                restage_squash,
                                target_head,
                                env,
                                task_integration,
                                overall_tier,
                                squash,
                            )
                            if isinstance(result, IntegrationResult):
                                return result
                            assert result.new_sha is not None
                            new_sha = result.new_sha
                    else:
                        # C-1: symmetrically, never trust a not-mid-rebase repo's current
                        # branch tip as the landing candidate either -- it may have been
                        # staged in the ORIGINAL `integrate()` call against a head that has
                        # since moved, or never staged at all (a conflict earlier in
                        # repo-key order returned before this repo's turn). Always restage
                        # fresh against the CURRENTLY-read `target_head`: idempotent
                        # (harmless, reproduces the same sha) when nothing has moved, since
                        # `_squash_repo`'s `commit_tree` is deterministic and
                        # `rebase_onto`'s fast path is a genuine no-op when
                        # `target_head == repo.base`.
                        if recorded_squash is not None:
                            restage_squash = recorded_squash
                        else:
                            # Never reached by the original `integrate()` call at all --
                            # squash it fresh from its own current (auto-committed) tip.
                            # `agent_id` is unavailable on this locked signature; the
                            # commit message's audit trail degrades to a documented
                            # placeholder for this one rare path only (the landed TREE
                            # content is identical either way).
                            fallback_tip = git.rev_parse(branch_ref) or repo.base
                            restage_squash = self._squash_repo(
                                task_id,
                                repo,
                                fallback_tip,
                                run_integration.run_id,
                                attempt,
                                _RESUME_FALLBACK_AGENT_ID,
                            )
                        result = self._restage_or_escalate(
                            task_id,
                            repo,
                            restage_squash,
                            target_head,
                            env,
                            task_integration,
                            overall_tier,
                            squash,
                        )
                        if isinstance(result, IntegrationResult):
                            return result
                        assert result.new_sha is not None
                        new_sha = result.new_sha
                    staged[repo.key] = (expected_old, new_sha)

                verify = self._run_verify(task_iso, run_integration, attempt)
                if not verify.passed:
                    self._log_event(
                        task_id, EVENT_VERIFY_FAILED, level=logging.WARNING, reason=verify.reason
                    )
                    ti_copy = task_integration.model_copy(update={"tier_reached": overall_tier})
                    escalated = self._escalation_hook(ti_copy, self._spec, CAUSE_VERIFY)
                    return self._merge_escalation(
                        escalated, squash, overall_tier, [], verify_status=VERIFY_FAILED
                    )
                self._log_event(task_id, EVENT_VERIFY_PASSED)

                landed: dict[str, str] = {}
                for repo in sorted_repos:
                    git = self._git_for(repo)
                    ref = f"refs/heads/{run_integration.branch}"
                    expected_old, candidate = staged[repo.key]
                    if git.update_ref_cas(ref, candidate, expected_old):
                        landed[repo.key] = candidate
                        self._log_event(
                            task_id,
                            EVENT_MERGED,
                            repo=repo.key,
                            head_from=expected_old,
                            head_to=candidate,
                        )
                        continue
                    head_now = git.rev_parse(ref)
                    if head_now is not None and git.is_ancestor(candidate, head_now):
                        landed[repo.key] = head_now
                        self._log_event(task_id, EVENT_MERGED, repo=repo.key, already_landed=True)
                        continue
                    if landed:
                        self._log_event(
                            task_id,
                            EVENT_PARTIAL,
                            level=logging.ERROR,
                            landed=list(landed),
                            failed=repo.key,
                        )
                    return IntegrationResult(
                        status=STATUS_FAILED,
                        reason=REASON_REF_RACE,
                        heads=dict(landed),
                        squash=squash,
                        verify_status=VERIFY_PASSED,
                    )

                untracked = self._untracked_declared_outputs(task_iso)
                return IntegrationResult(
                    status=STATUS_INTEGRATED,
                    tier_reached=overall_tier,
                    heads=landed,
                    squash=squash,
                    verify_status=VERIFY_PASSED,
                    untracked_outputs=untracked,
                )
        except GitError as exc:
            self._log_event(
                task_id, EVENT_FAILED, level=logging.ERROR, reason=REASON_GIT_ERROR, error=str(exc)
            )
            return IntegrationResult(
                status=STATUS_FAILED, reason=REASON_GIT_ERROR, verify_status=VERIFY_NOT_RUN
            )

    # --- step 1: auto-commit + S-3 denylist screen --------------------------------------

    def _auto_commit_and_screen(
        self,
        task_iso: TaskIsolation,
        run_integration: RunIntegrationSnapshot,
        attempt: int,
        agent_id: str,
    ) -> tuple[dict[str, str], IntegrationResult | None]:
        """Returns `(tips, None)` on success (*tips*: repo.key -> this repo's committed tip
        after auto-commit, or its unchanged branch tip when `auto_commit` is false), or
        `({}, IntegrationResult(status="failed", ...))` when the S-3 screen aborts.

        HLD §11 M4 (locked step order): the untracked-path screen runs UNCONDITIONALLY for
        every repo, even when `spec.auto_commit` is false -- only the `git add -A` +
        `commit` sweep itself is gated on `auto_commit`. An already-tracked path matching
        `commit_denylist` is never screened (AC-18b): that is the repo author's decision,
        not the engine's -- this is a footgun backstop, not a secrets scanner.
        """
        tips: dict[str, str] = {}
        for repo in task_iso.repos:
            git = self._git_for(repo)
            wt = repo.worktree_root
            branch_ref = f"refs/heads/{repo.branch}"

            entries = git.status_porcelain(wt, untracked=True)
            untracked = [e.path for e in entries if _is_untracked(e.index, e.worktree)]
            hits = [p for p in untracked if _matches_denylist(p, self._spec.commit_denylist)]
            if hits and self._spec.on_denylisted_path == ON_DENYLISTED_FAIL:
                self._log_event(
                    task_iso.task_id,
                    EVENT_DENYLISTED_PATH,
                    level=logging.ERROR,
                    repo=repo.key,
                    paths=hits,
                )
                return {}, IntegrationResult(
                    status=STATUS_FAILED,
                    reason=REASON_DENYLISTED_PATH,
                    conflicted_paths=list(hits),
                    verify_status=VERIFY_NOT_RUN,
                )
            if hits and self._spec.on_denylisted_path == ON_DENYLISTED_WARN:
                self._log_event(
                    task_iso.task_id,
                    EVENT_DENYLISTED_PATH,
                    level=logging.WARNING,
                    repo=repo.key,
                    paths=hits,
                )

            if self._spec.auto_commit:
                # HLD §11 M4 step 1, exactly: `add -A` THEN commit -- `GitRepo.commit`
                # itself never stages anything (it only checks the INDEX against HEAD), so
                # skipping `add_all` here would silently leave every untracked file (this
                # denylist screen's own subject!) uncommitted and this task permanently
                # "empty" no matter what it wrote.
                git.add_all(wt)
                message = _render_commit_message(
                    self._spec,
                    run_id=run_integration.run_id,
                    task_id=task_iso.task_id,
                    agent_id=agent_id,
                    base_commit=repo.base,
                    attempt=attempt,
                )
                new_sha = git.commit(wt, message, allow_empty=False)
                tips[repo.key] = new_sha or (git.rev_parse(branch_ref) or repo.base)
            else:
                tips[repo.key] = git.rev_parse(branch_ref) or repo.base
        return tips, None

    # --- Empty check (R-8) --------------------------------------------------------------

    def _is_empty(self, task_iso: TaskIsolation, tips: dict[str, str]) -> bool:
        """R-8, authoritative condition: `tip == base` AND the worktree is clean (no
        STAGED-equivalent tracked delta remains -- untracked files that are not declared
        outputs are not this module's concern) AND there is no untracked declared output.
        """
        for repo in task_iso.repos:
            if tips[repo.key] != repo.base:
                return False
            git = self._git_for(repo)
            entries = git.status_porcelain(repo.worktree_root, untracked=True)
            if any(not _is_untracked(e.index, e.worktree) for e in entries):
                return False
        return not self._untracked_declared_outputs(task_iso)

    def _untracked_declared_outputs(self, task_iso: TaskIsolation) -> list[str]:
        """R-8/R-16: declared outputs (`task_iso.declared_outputs`) that exist in this
        task's worktree but are untracked-and-gitignored there
        (`GitRepo.ls_files_untracked_ignored`, purpose-built for exactly this per HLD §11
        M1). A declared output that is simply MISSING is not this module's concern (R-2's
        pre-`integrate()` gate, owned by `T-En8Hd4`, is what catches that); an untracked
        path that is NOT gitignored would already have been swept into the auto-commit
        (or, with `auto_commit: false`, is the repo author's own choice to leave uncommitted
        -- outside this method's documented "gitignored" scope).
        """
        result: list[str] = []
        for repo in task_iso.repos:
            wt = os.path.normpath(repo.worktree_root)
            rel_to_output: dict[str, str] = {}
            for output in task_iso.declared_outputs:
                workspace_abs = (
                    output
                    if os.path.isabs(output)
                    else os.path.normpath(os.path.join(task_iso.workspace_root, output))
                )
                remapped = os.path.normpath(effective_path(workspace_abs, task_iso))
                if remapped == wt or remapped.startswith(wt + os.sep):
                    rel_to_output[os.path.relpath(remapped, wt)] = output
            if not rel_to_output:
                continue
            git = self._git_for(repo)
            ignored = git.ls_files_untracked_ignored(repo.worktree_root, list(rel_to_output))
            result.extend(rel_to_output[rel] for rel in sorted(rel_to_output) if rel in ignored)
        return result

    # --- steps 2-5: lock, squash+rebase, verify, land -----------------------------------

    def _squash_rebase_verify_land(
        self,
        task_iso: TaskIsolation,
        run_integration: RunIntegrationSnapshot,
        task_integration: TaskIntegrationState,
        attempt: int,
        tips: dict[str, str],
        agent_id: str,
    ) -> IntegrationResult:
        task_id = task_iso.task_id
        sorted_repos = sorted(task_iso.repos, key=lambda r: r.key)
        env = self._base_env(run_integration.run_id, task_id, attempt)

        with ExitStack() as stack:
            for repo in sorted_repos:
                lock = IntegrationLock(repo.common_dir)
                if not lock.acquire(self._spec.lock_timeout_seconds):
                    self._log_event(
                        task_id,
                        EVENT_FAILED,
                        level=logging.ERROR,
                        reason=REASON_LOCK_TIMEOUT,
                        repo=repo.key,
                    )
                    return IntegrationResult(
                        status=STATUS_FAILED,
                        reason=REASON_LOCK_TIMEOUT,
                        verify_status=VERIFY_NOT_RUN,
                    )
                stack.callback(lock.release)

            squash: dict[str, str] = {}
            staged: dict[str, tuple[str, str]] = {}
            overall_tier: ResolverTier = TIER_AUTO

            # Step 3: squash + rebase every repo (still nothing landed).
            for repo in task_iso.repos:
                git = self._git_for(repo)
                integration_ref = f"refs/heads/{run_integration.branch}"
                head_sha = git.rev_parse(integration_ref)
                expected_old = head_sha if head_sha is not None else ""
                target_head = head_sha if head_sha is not None else repo.base

                stage = self._stage_one_repo(
                    task_id,
                    repo,
                    target_head,
                    tips[repo.key],
                    run_integration.run_id,
                    attempt,
                    agent_id,
                    env,
                )
                squash[repo.key] = stage.squash_sha
                if stage.tier != TIER_AUTO and overall_tier == TIER_AUTO:
                    overall_tier = stage.tier
                if stage.conflict:
                    ti_copy = task_integration.model_copy(
                        update={
                            "conflicted_paths": stage.unresolved_paths,
                            "tier_reached": overall_tier,
                        }
                    )
                    escalated = self._escalation_hook(ti_copy, self._spec, CAUSE_CONFLICT)
                    return self._merge_escalation(
                        escalated, squash, overall_tier, stage.unresolved_paths
                    )
                assert stage.new_sha is not None
                staged[repo.key] = (expected_old, stage.new_sha)

            # Step 4: verify once, on the post-rebase tree.
            self._log_event(task_id, EVENT_VERIFY_STARTED)
            verify = self._run_verify(task_iso, run_integration, attempt)
            if not verify.passed:
                self._log_event(
                    task_id, EVENT_VERIFY_FAILED, level=logging.WARNING, reason=verify.reason
                )
                ti_copy = task_integration.model_copy(update={"tier_reached": overall_tier})
                escalated = self._escalation_hook(ti_copy, self._spec, CAUSE_VERIFY)
                return self._merge_escalation(
                    escalated, squash, overall_tier, [], verify_status=VERIFY_FAILED
                )
            self._log_event(task_id, EVENT_VERIFY_PASSED)

            # Step 5: land, CAS per repo, in key order. R-7: a lost CAS retries ONLY the
            # losing repo, bounded by CAS_RETRY_BOUND, never re-processing a repo already
            # in `landed`.
            landed: dict[str, str] = {}
            cas_attempts: dict[str, int] = {}
            for repo in sorted_repos:
                git = self._git_for(repo)
                ref = f"refs/heads/{run_integration.branch}"
                expected_old, candidate = staged[repo.key]
                while True:
                    if git.update_ref_cas(ref, candidate, expected_old):
                        landed[repo.key] = candidate
                        self._log_event(
                            task_id,
                            EVENT_MERGED,
                            repo=repo.key,
                            head_from=expected_old,
                            head_to=candidate,
                        )
                        break
                    head_now = git.rev_parse(ref)
                    if head_now is not None and git.is_ancestor(candidate, head_now):
                        landed[repo.key] = head_now
                        self._log_event(task_id, EVENT_MERGED, repo=repo.key, already_landed=True)
                        break
                    if cas_attempts.get(repo.key, 0) >= CAS_RETRY_BOUND:
                        if landed:
                            self._log_event(
                                task_id,
                                EVENT_PARTIAL,
                                level=logging.ERROR,
                                landed=list(landed),
                                failed=repo.key,
                            )
                        return IntegrationResult(
                            status=STATUS_FAILED,
                            reason=REASON_REF_RACE,
                            heads=dict(landed),
                            squash=squash,
                            verify_status=VERIFY_PASSED,
                        )
                    cas_attempts[repo.key] = cas_attempts.get(repo.key, 0) + 1
                    new_target = head_now if head_now is not None else repo.base
                    # R-7/C-1: never re-squash -- the squash sha already computed for this
                    # repo in the first pass (`squash[repo.key]`) is reused as-is; only the
                    # rebase target changed.
                    result = self._restage_or_escalate(
                        task_id,
                        repo,
                        squash[repo.key],
                        new_target,
                        env,
                        task_integration,
                        overall_tier,
                        squash,
                    )
                    if isinstance(result, IntegrationResult):
                        if landed:
                            self._log_event(
                                task_id,
                                EVENT_PARTIAL,
                                level=logging.ERROR,
                                landed=list(landed),
                                failed=repo.key,
                            )
                        return dataclasses.replace(result, heads={**landed, **result.heads})
                    assert result.new_sha is not None
                    expected_old = new_target
                    candidate = result.new_sha

            untracked = self._untracked_declared_outputs(task_iso)
            return IntegrationResult(
                status=STATUS_INTEGRATED,
                tier_reached=overall_tier,
                heads=landed,
                squash=squash,
                verify_status=VERIFY_PASSED,
                untracked_outputs=untracked,
            )
        raise AssertionError("unreachable: ExitStack body always returns")  # pragma: no cover

    def _squash_repo(
        self,
        task_id: str,
        repo: RepoIsolation,
        tip: str,
        run_id: str,
        attempt: int,
        agent_id: str,
    ) -> str:
        """Deterministic squash of *repo*'s worktree (from *tip*, a commit-ish whose TREE
        is captured) onto `repo.base`. Idempotent: the same (*tip*, *run_id*, *task_id*,
        *agent_id*, *attempt*) inputs always reproduce the identical sha (`commit_tree`),
        so calling this twice for the same repo/attempt is always safe.
        """
        git = self._git_for(repo)
        message = _render_commit_message(
            self._spec,
            run_id=run_id,
            task_id=task_id,
            agent_id=agent_id,
            base_commit=repo.base,
            attempt=attempt,
        )
        squash_sha = git.commit_tree(f"{tip}^{{tree}}", repo.base, message)
        git.create_ref(paths.squash_ref(run_id, task_id, attempt), squash_sha)
        git.reset_hard(repo.worktree_root, squash_sha)
        self._log_event(task_id, EVENT_SQUASHED, repo=repo.key, squash=squash_sha)
        return squash_sha

    def _rebase_onto_and_resolve(
        self,
        task_id: str,
        repo: RepoIsolation,
        squash_sha: str,
        target_head: str,
        env: dict[str, str],
    ) -> _StageResult:
        """Rebase *repo*'s already-squashed `squash_sha` onto *target_head* (fast path when
        `target_head == repo.base`), running the T1 `resolver_hook` on any conflict.
        Assumes *repo*'s worktree/branch already sits at `squash_sha` (via `_squash_repo`
        or an equivalent prior reset) -- never re-squashes.
        """
        git = self._git_for(repo)
        wt = repo.worktree_root
        branch_ref = f"refs/heads/{repo.branch}"

        if target_head == repo.base:
            self._log_event(task_id, EVENT_REBASED, repo=repo.key, tier=TIER_AUTO, fast_path=True)
            return _StageResult(squash_sha, False, squash_sha, [], TIER_AUTO)

        outcome = git.rebase_onto(wt, target_head, repo.base, repo.branch)
        if outcome.clean:
            new_sha = git.rev_parse(branch_ref) or squash_sha
            self._log_event(task_id, EVENT_REBASED, repo=repo.key, tier=TIER_AUTO)
            return _StageResult(squash_sha, False, new_sha, [], TIER_AUTO)

        self._log_event(
            task_id, EVENT_CONFLICT, level=logging.WARNING, repo=repo.key, paths=list(outcome.paths)
        )
        unresolved_tier: ResolverTier
        if outcome.paths and TIER_MECHANICAL in self._spec.ladder:
            unresolved = self._resolver_hook(
                list(outcome.paths), worktree=wt, git=git, config=self._spec.resolvers, env=env
            )
            unresolved_tier = TIER_MECHANICAL
        elif outcome.paths:
            # review C-3 (T-Rm2Lx7): `"mechanical"` removed from `integration.ladder`
            # disables the tier per HLD §8.4 ("removing an entry disables that tier") --
            # every path left conflicted by the rebase escalates directly, the hook is
            # never invoked, and the tier this stage reached stays `auto` (mechanical was
            # never attempted, so it must not be credited). Git's own rerere replay, if
            # any, already happened INSIDE `rebase_onto` above and is governed separately
            # by `resolvers.rerere`/C-2 -- this gate only controls whether NEW mechanical
            # work is attempted here.
            unresolved = list(outcome.paths)
            unresolved_tier = TIER_AUTO
        else:
            # A conflict occurred (non-clean rebase) but rerere already replayed and staged
            # every hunk (S-5): still tier T1 mechanical, never silently "auto".
            unresolved = []
        if unresolved:
            return _StageResult(squash_sha, True, None, unresolved, unresolved_tier)

        self._log_event(
            task_id, EVENT_RESOLVED, repo=repo.key, tier=TIER_MECHANICAL, resolver="mechanical"
        )
        cont = git.rebase_continue(wt)
        if not cont.clean:
            # Defensive: the branch being rebased holds exactly ONE commit (the squash),
            # so a single `rebase --continue` after full resolution should always finish.
            return _StageResult(squash_sha, True, None, list(cont.paths), TIER_MECHANICAL)
        new_sha = git.rev_parse(branch_ref) or squash_sha
        self._log_event(task_id, EVENT_REBASED, repo=repo.key, tier=TIER_MECHANICAL)
        return _StageResult(squash_sha, False, new_sha, [], TIER_MECHANICAL)

    def _stage_one_repo(
        self,
        task_id: str,
        repo: RepoIsolation,
        target_head: str,
        tip: str,
        run_id: str,
        attempt: int,
        agent_id: str,
        env: dict[str, str],
    ) -> _StageResult:
        """Squash *repo*'s worktree (from *tip*) onto *target_head*, from scratch. Used
        for the FIRST staging pass only (`_squash_rebase_verify_land`'s step 3) -- both the
        CAS-retry loop and `resume_integration`'s restage path reuse an already-known
        squash sha via `_restage_or_escalate` instead, since re-deriving it from *tip*
        would need `agent_id`, which `resume_integration`'s locked signature doesn't carry.
        """
        squash_sha = self._squash_repo(task_id, repo, tip, run_id, attempt, agent_id)
        return self._rebase_onto_and_resolve(task_id, repo, squash_sha, target_head, env)

    def _restage_or_escalate(
        self,
        task_id: str,
        repo: RepoIsolation,
        squash_sha: str,
        target_head: str,
        env: dict[str, str],
        task_integration: TaskIntegrationState,
        overall_tier: ResolverTier,
        squash: dict[str, str],
    ) -> _StageResult | IntegrationResult:
        """Re-rebase repo's ALREADY-COMPUTED *squash_sha* onto *target_head* -- never
        re-squashes (the squash tree never depends on `target_head`, only the rebase
        does). Shared by two call sites needing exactly this operation, avoiding the
        duplicate logic a reviewer flagged (E-Wk9Tz3 T-Ib5Qy9 review C-1):
        - the CAS-retry loop in `_squash_rebase_verify_land` (R-7: the losing repo only,
          `squash_sha` = its own first-pass result already in `squash`);
        - `resume_integration` (C-1: any repo whose landing candidate is discovered stale
          against a freshly-read integration head, `squash_sha` = the durable
          `recorded_squash` read from `paths.squash_ref(...)`, or a freshly-squashed one
          for a repo the original `integrate()` call never reached at all).

        Updates *squash* in place. Returns the successful `_StageResult`, or an already
        fully-formed `IntegrationResult` if the restage itself conflicts -- callers return
        that immediately (merging in any already-landed heads first, for call sites that
        track any).
        """
        squash[repo.key] = squash_sha
        stage = self._rebase_onto_and_resolve(task_id, repo, squash_sha, target_head, env)
        if stage.conflict:
            ti_copy = task_integration.model_copy(
                update={"conflicted_paths": stage.unresolved_paths, "tier_reached": overall_tier}
            )
            escalated = self._escalation_hook(ti_copy, self._spec, CAUSE_CONFLICT)
            return self._merge_escalation(escalated, squash, overall_tier, stage.unresolved_paths)
        return stage

    def _merge_escalation(
        self,
        escalated: IntegrationResult,
        squash: dict[str, str],
        tier: ResolverTier,
        conflicted_paths: list[str],
        *,
        verify_status: Literal["not_run", "passed", "failed"] = VERIFY_NOT_RUN,
    ) -> IntegrationResult:
        """Merge in the squash shas / fallback tier / fallback conflicted-paths /
        fallback verify_status `Integrator` already knows, without overriding whatever the
        hook itself explicitly set."""
        return dataclasses.replace(
            escalated,
            squash={**squash, **escalated.squash},
            conflicted_paths=escalated.conflicted_paths or conflicted_paths,
            tier_reached=escalated.tier_reached or tier,
            verify_status=escalated.verify_status
            if escalated.verify_status != VERIFY_NOT_RUN
            else verify_status,
        )

    # --- verify --------------------------------------------------------------------------

    def _run_verify(
        self, task_iso: TaskIsolation, run_integration: RunIntegrationSnapshot, attempt: int
    ) -> _VerifyOutcome:
        if self._spec.verify_command:
            return self._run_verify_command(task_iso, run_integration, attempt)
        return self._run_verify_structural(task_iso)

    def _run_verify_structural(self, task_iso: TaskIsolation) -> _VerifyOutcome:
        """HLD §8.3 default: `git grep -l` (conflict markers) + `git diff --check`, per
        repo, on the post-rebase tree, via `GitRepo.grep_conflict_markers`/`.diff_check`
        (public wrappers -- review C-3). Paths only ever enter Python (NFR-1; a dedicated
        test asserts this module never calls `open()`/`read_text()` on a repo file)."""
        for repo in task_iso.repos:
            git = self._git_for(repo)
            wt = repo.worktree_root
            branch_ref = f"refs/heads/{repo.branch}"
            head = git.rev_parse(branch_ref)
            if head is None:  # pragma: no cover -- defensive; a just-rebased branch exists
                continue
            changed = git.diff_names(wt, repo.base, head)
            if not changed:
                continue
            if git.grep_conflict_markers(wt, head, paths=changed):
                return _VerifyOutcome(passed=False, reason="conflict_markers", exit_code=None)
            if git.diff_check(wt, repo.base, head):
                return _VerifyOutcome(passed=False, reason="diff_check", exit_code=None)
        return _VerifyOutcome(passed=True, reason=None, exit_code=0)

    def _run_verify_command(
        self, task_iso: TaskIsolation, run_integration: RunIntegrationSnapshot, attempt: int
    ) -> _VerifyOutcome:
        """*cwd* = the "primary" repo -- deterministically `task_iso.repos[0]`, itself
        deterministic (`group_repos` sorts by repo key). *env* = process env +
        `AO_RUN_ID`/`AO_TASK_ID`/`AO_ATTEMPT` (this module never sees `isolation.env` --
        R-20 keeps it free of `TaskSpec`/`AgentSpec`)."""
        primary = task_iso.repos[0]
        cwd = primary.worktree_root
        env = self._base_env(run_integration.run_id, task_iso.task_id, attempt)
        argv = list(self._spec.verify_command)
        timeout = float(self._spec.verify_timeout_seconds)

        capture_dir = (
            Path(run_integration.run_dir) / task_iso.task_id / "integration" / f"attempt-{attempt}"
        )
        capture_dir.mkdir(parents=True, exist_ok=True)

        try:
            cp = self._exec(argv, cwd=cwd, env=env, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            (capture_dir / "verify.stdout").write_bytes(_cap(exc.stdout or b""))
            (capture_dir / "verify.stderr").write_bytes(_cap(exc.stderr or b""))
            (capture_dir / "verify.exit").write_text("")
            return _VerifyOutcome(passed=False, reason=REASON_VERIFY_TIMEOUT, exit_code=None)
        except OSError:
            # Missing/non-executable command -- AC-8(c): never a silent pass.
            return _VerifyOutcome(passed=False, reason=REASON_VERIFY_COMMAND_ERROR, exit_code=None)

        (capture_dir / "verify.stdout").write_bytes(_cap(cp.stdout))
        (capture_dir / "verify.stderr").write_bytes(_cap(cp.stderr))
        (capture_dir / "verify.exit").write_text(str(cp.returncode))
        if cp.returncode == 0:
            return _VerifyOutcome(passed=True, reason=None, exit_code=0)
        return _VerifyOutcome(
            passed=False, reason=REASON_VERIFY_COMMAND_FAILED, exit_code=cp.returncode
        )
