"""Orchestration engine — drives a workflow DAG to completion."""

from __future__ import annotations

import logging
import os
import re
import shutil
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from .artifacts import (
    ArtifactStore,
    LocalFsArtifactStore,
    read_gate,
    read_manifest,
    read_routes,
    read_task_manifest,
)
from .breakers import apply_breaker_extension, evaluate_breakers, record_trip
from .budget import BudgetDecision, BudgetManager, cycle_key
from .dag import Graph, build_dag, compute_cones
from .errors import (
    ArtifactPathError,
    ControlFileError,
    GateError,
    InjectionError,
    SpecValidationError,
    WorktreeCollisionError,
)
from .estimator import TokenEstimator
from .executors.base import Executor
from .isolation import escalation as resolver_escalation
from .isolation import paths as isolation_paths
from .isolation.git import GIT_MIN_VERSION, GitRepo
from .isolation.hotspots import load_hotspots
from .isolation.integrator import (
    EscalationHook,
    IntegrationResult,
    Integrator,
    ResolverHook,
    RunIntegrationSnapshot,
)
from .isolation.resolvers import resolve_mechanically
from .isolation.runlock import WorkspaceRunLock
from .isolation.view import IsolatedArtifactView
from .isolation.worktrees import TaskIsolation, WorktreeManager, group_repos
from .logging_setup import attach_run_handler, detach_run_handler, get_run_logger
from .models import (
    BUILTIN_BUDGET_EXHAUSTED,
    BUILTIN_BUDGET_UNSATISFIABLE,
    BUILTIN_QUOTA_MAX_WAIT,
    DEFAULT_MAX_EXTENSIONS_PER_BREAKER,
    DEFAULT_MAX_HEAL_RETRIES_PER_TASK,
    DEFAULT_MAX_MONITOR_CALLS_PER_RUN,
    DEFAULT_MAX_PARALLEL,
    DEFAULT_QUOTA_MAX_WAIT_SECONDS,
    DEFAULT_QUOTA_POLL_SECONDS,
    ISOLATION_NONE,
    ISOLATION_WORKTREE,
    OVERLAP_SOFT,
    CircuitBreakerSpec,
    EstimatorConfig,
    IntegrationSpec,
    LoopSpec,
    MonitorDecisionRecord,
    RouterSpec,
    RunState,
    TaskContext,
    TaskIntegrationState,
    TaskResult,
    TaskRunState,
    TaskSpec,
    WorkflowSpec,
    count_monitor_breaker_extensions,
    count_monitor_calls_made,
    count_monitor_heal_retries,
    resolve_effective_agent,
    resolve_overlap_preference,
    resolve_task_isolation,
    strip_iter_suffix,
)
from .monitoring import (
    SAFE_DEFAULT_BREAKER_VERDICT,
    SAFE_DEFAULT_HEAL_VERDICT,
    BreakerTripSummary,
    BreakerVerdict,
    HealVerdict,
    Monitor,
    RuleBasedMonitor,
    build_task_failure_summary,
)
from .runstate import RunStateStore
from .scheduling.overlap import rank_wave
from .spec import validate_isolation

# Valid origin values for injected/loop tasks
_TaskOrigin = Literal["static", "injected", "loop"]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Per-task git isolation & rebase-based integration (E-Wk9Tz3 T-En8Hd4, HLD §11 M5) —
# named constants, no magic literals at call sites.
# ---------------------------------------------------------------------------

# S-7: warn (never a hard cap -- deleting failure evidence to save disk is explicitly
# rejected, HLD §24 "Deferred") once this many non-integrated task worktrees are retained
# in one run. Points the operator at the remedy; existing breakers are what actually stop
# a runaway verify-failure storm.
_WORKTREE_RETENTION_WARN_THRESHOLD = 5

# HLD §7.2 RESERVED_SHARED_PREFIXES: appended to <common_dir>/info/exclude at activation
# (AC-4) -- never .gitignore, so a repo's own tracked ignore rules are never touched.
_INFO_EXCLUDE_ENTRY = ".orchestrator/"

# R-21 (E-Wk9Tz3 T-Ac6Vd9): capture-directory segment for a dispatch cycle >= 2 (cycle 1
# keeps the legacy flat layout, no prefix at all -- see `_run_with_retries`'s own comment
# block for the full "state in one place" rule). Named to match `budget.py`'s own
# `_CYCLE_KEY_SEP` precedent (no magic literals at the call site).
_CYCLE_DIR_PREFIX = "cycle-"

# E-Wk9Tz3 T-Wl2Bq7 (HLD §12.3, ADR-0013 D8): `IntegrationSpec.workspace_lock` policy
# values -- named here (not in models.py, which is frozen for this ticket) so every
# comparison at a call site below reads as a policy name, never a bare string literal.
_WORKSPACE_LOCK_REQUIRE = "require"
_WORKSPACE_LOCK_SKIP_SYNC = "skip_sync"
_WORKSPACE_LOCK_OFF = "off"

# E-Wk9Tz3 T-Lr6Ka3 (HLD §8.4-8.6, §11 M7): `TaskIntegrationState.mode` values -- named
# here (not in models.py, frozen for this ticket) so every comparison at a call site below
# reads as a mode name, never a bare string literal.
_INTEGRATION_MODE_NORMAL = "normal"
_INTEGRATION_MODE_RESOLVE = "resolve"
_INTEGRATION_MODE_RERUN = "rerun"

# The packaged builtin T2 resolver instruction (AC-3), copied into the run dir at requeue
# time. Resolved against engine.py's own `__file__` (not an `importlib.resources` lookup,
# and not an import of the `templates` package) so wiring this ticket's one asset costs no
# new module dependency -- the same "packaged path anchored on `__file__`" technique
# `templates/__init__.py` documents for its own asset lookups, applied without actually
# importing that (heavier, yaml/pydantic-parsing) package into the engine.
_RESOLVER_INSTRUCTION_ASSET = str(
    Path(__file__).resolve().parent / "templates" / "builtin" / "instructions" / "merge-resolve.md"
)

_ENV_UNSAFE_RE = re.compile(r"[^A-Za-z0-9_]")


def _env_safe(value: str) -> str:
    """Uppercase, env-var-safe suffix for AO_WORKTREE_ROOT_<REPO_ID> (AC-8)."""
    return _ENV_UNSAFE_RE.sub("_", value).upper()


def _iso_repo_paths(task_iso: TaskIsolation) -> dict[str, str]:
    """HLD §7.1: 'repo_paths handed to an isolated task become
    ``{member.repo_id: join(worktree_root, member.rel)}``' -- the SEVENTH remappable path
    category (R-19). Unlike the six inside `_run_with_retries`, `repo_paths` is already a
    parameter to that function; the fix is simply handing it a DIFFERENT dict per isolated
    task instead of the run-level shared one.
    """
    result: dict[str, str] = {}
    for repo in task_iso.repos:
        for member in repo.members:
            result[member.repo_id] = (
                os.path.normpath(os.path.join(repo.worktree_root, member.rel))
                if member.rel
                else repo.worktree_root
            )
    return result


def _workspace_root_from_run_dir(run_dir: str) -> str:
    """``run_dir`` = ``<workspace_root>/.orchestrator/runs/<run_id>``
    (`RunStateStore._path`'s layout) -- three levels up recovers ``workspace_root`` without
    a second private-attribute reach into `RunStateStore` (one already exists, at `run()`'s
    own `run_dir` derivation).
    """
    return os.path.dirname(os.path.dirname(os.path.dirname(run_dir)))


def _append_info_exclude(common_dir: str) -> None:
    """AC-4: append '.orchestrator/' to ``<common_dir>/info/exclude`` -- never
    ``.gitignore``. Idempotent. Plain filesystem I/O, not a git subprocess call, so this
    does not go through `GitRepo` (which owns no such method and is not this ticket's file
    to extend). Uses `pathlib.Path`'s own read_text/write_text methods rather than the
    builtin file-open function -- engine.py's own NFR-1 static check
    (`test_dynamic_injection.py`) forbids that spelling anywhere in this file, on the
    theory that every read/write in the engine should be traceable to a narrow, named
    helper rather than an ad hoc file handle; this is git bookkeeping, not artifact/
    instruction payload content, but the check draws no such distinction, so it is
    honoured the same way regardless.
    """
    info_dir = Path(common_dir) / "info"
    info_dir.mkdir(parents=True, exist_ok=True)
    exclude_path = info_dir / "exclude"
    existing_lines = (
        exclude_path.read_text(encoding="utf-8").splitlines() if exclude_path.exists() else []
    )
    if _INFO_EXCLUDE_ENTRY in existing_lines:
        return
    exclude_path.write_text(
        "\n".join([*existing_lines, _INFO_EXCLUDE_ENTRY]) + "\n", encoding="utf-8"
    )


def _default_resolver_hook(
    conflicted_paths: list[str],
    *,
    worktree: str,  # noqa: ARG001 -- Protocol-shaped, unused by the no-op stub
    git: object,  # noqa: ARG001
    config: object,  # noqa: ARG001
    env: dict[str, str],  # noqa: ARG001
) -> list[str]:
    """No-op T1 mechanical resolver (`isolation.integrator.ResolverHook`) -- every
    conflicted path comes back unresolved, exactly the "stub resolves nothing" case
    `T-Ib5Qy9`'s own `Integrator` tests already exercise. No longer `Orchestrator`'s
    default (that is now `isolation.resolvers.resolve_mechanically`, T-Rm2Lx7's real T1
    implementation) -- kept as an explicit, deliberate opt-out a caller can still pass via
    ``Orchestrator(resolver_hook=_default_resolver_hook)`` to disable T1 entirely.
    """
    return list(conflicted_paths)


def _default_escalation_hook(
    task_integration: TaskIntegrationState,
    spec: IntegrationSpec,  # noqa: ARG001 -- Protocol-shaped, unused by the fail-safe stub
    cause: Literal["conflict", "verify"],
) -> IntegrationResult:
    """Fail-safe T2/T3 ladder stand-in (`isolation.integrator.EscalationHook`): a
    conflict/verify-failure this pass could not resolve mechanically fails straight to the
    operator (T4) instead of dispatching a resolver/rerun agent at all. No longer
    `Orchestrator`'s default (that is now `isolation.escalation.escalate`, this ticket's
    real T2/T3/T4 decision table) -- kept as an explicit, deliberate opt-out a caller can
    still pass via ``Orchestrator(escalation_hook=_default_escalation_hook)`` to force
    every conflict straight to the operator with zero LLM/rerun spend.
    """
    return IntegrationResult(
        status="failed",
        tier_reached=task_integration.tier_reached,
        conflicted_paths=list(task_integration.conflicted_paths),
        reason=f"no_escalation_hook_configured:{cause}",
    )


def _no_cancel() -> bool:
    return False


_NO_CANCEL: Callable[[], bool] = _no_cancel


# ---------------------------------------------------------------------------
# Wave/barrier scheduler control-flow types (T-j8YLGd, ADR-0007)
# ---------------------------------------------------------------------------

# Outcome of `Orchestrator._prepare_and_maybe_dispatch`.
DispatchSignal = Literal["skipped", "dispatch", "halt", "blocked"]

# Outcome of `Orchestrator._settle_completed_task`.
SettleSignal = Literal["settled", "requeue", "halt", "reshaped"]


@dataclass
class DispatchPrep:
    """Result of `_prepare_and_maybe_dispatch` (T-j8YLGd).

    Only ``signal == "dispatch"`` populates the payload fields -- exactly what
    `run()` needs to submit `self._run_and_integrate(...)` for *task*, mirroring
    today's inline call one-for-one (task/dynamic_input_paths/resolved manifest
    + gate paths).

    E-Wk9Tz3 (T-En8Hd4) additions -- populated only for an isolated dispatch;
    ``None``/empty otherwise, which is what keeps the non-isolated path byte-identical
    (NFR-2):
    ``store``: the task's `IsolatedArtifactView`, or ``None`` meaning "use `self._store`"
      (R-19).
    ``task_iso``: this dispatch's `TaskIsolation`, or ``None`` when not isolated.
    ``repo_paths``: the per-task, worktree-remapped repo_paths dict (HLD §7.1's SEVENTH
      remappable category), or ``None`` meaning "use the run-level shared dict".
    ``run_integration``: an immutable snapshot for `Integrator.integrate()`, built once per
      dispatch from the live `RunIntegrationState` (never a reference to it -- R-20/NFR-3).
    ``env_overlay``: ``TaskContext.env`` (AC-8) -- AO_ISOLATION/AO_TASK_BRANCH/
      AO_INTEGRATION_BRANCH/AO_WORKTREE_ROOT_<repo> plus any `isolation.env` overlay.

    E-Wk9Tz3 T-Ac6Vd9 addition -- ``cycle``: this dispatch's ``TaskRunState.dispatch_cycle``
    (R-21), always populated (every dispatch, isolated or not) since `dispatch_cycle` is
    incremented unconditionally in `_prepare_and_maybe_dispatch`. Threaded through to
    `_run_and_integrate`/`_run_with_retries` to key the capture directory, and used by
    the caller's budget charge/reconcile/reverse calls (R-1b) to key the ledger.
    """

    signal: DispatchSignal
    task: TaskSpec | None = None
    dynamic_input_paths: list[str] | None = None
    task_manifest_path: str | None = None
    gate_output_path: str | None = None
    store: ArtifactStore | None = None
    task_iso: TaskIsolation | None = None
    repo_paths: dict[str, str] | None = None
    run_integration: RunIntegrationSnapshot | None = None
    env_overlay: dict[str, str] | None = None
    cycle: int = 1


@dataclass
class SettleResult:
    """Result of `_settle_completed_task` (T-j8YLGd).

    ``signal == "reshaped"`` carries the already-rebuilt `graph`/`order` back to
    `run()` -- computed once, inside settle, at the same point (and for the same
    logging purpose) the original inline code computed them, so handing them
    back avoids a second, duplicate `build_dag` call in `run()` that would
    double-emit `dag.py`'s inferred-edge warning into `run.log`.
    """

    signal: SettleSignal
    graph: Graph | None = None
    order: list[str] | None = None


@dataclass
class WorkerOutcome:
    """Result of `_run_and_integrate` (E-Wk9Tz3 T-En8Hd4, HLD §11 M5), returned from the
    worker thread to the main thread's `_settle_completed_task`. Carries NO `RunState`
    mutation of its own (ADR-0007 D3 / NFR-3) -- it is a plain, immutable-by-convention
    value the worker builds and the main thread reads.

    ``integration`` is ``None`` for a non-isolated task, OR for an isolated task whose
    execution failed / whose declared outputs were missing (R-2's worker-side gate) -- in
    both of those cases `integrate()` was never called. ``missing_outputs`` is populated
    only in the second of those (R-2). ``task_iso`` is echoed back unchanged so
    `_settle_completed_task` can drive the untracked-outputs copy-back (§7.4) without the
    main thread needing to re-derive it.

    E-Wk9Tz3 T-Lr6Ka3 addition -- ``rerun_base``: repo_key -> the fresh integration head the
    WORKER reset a T3 (mode == "rerun") dispatch's worktree to, BEFORE redispatching the
    original agent. ``None`` for every other dispatch (NFR-2/byte-identical). The worker
    performs the reset itself (an operation scoped to this task's own exclusively-owned
    worktree, safe off the main thread) but cannot write `RunState` itself (NFR-3), so it
    reports the new base back here for `_settle_completed_task` to persist onto
    ``TaskIntegrationState.base_commits``.
    """

    result: TaskResult
    integration: IntegrationResult | None = None
    missing_outputs: list[str] | None = None
    task_iso: TaskIsolation | None = None
    rerun_base: dict[str, str] | None = None


@dataclass
class _RunContext:
    """Per-`run()` state shared across `_prepare_and_maybe_dispatch` and
    `_settle_completed_task` (T-j8YLGd).

    Bundles the values the old single-cursor loop computed once in setup and
    closed over for the rest of the method (`repo_paths`, `agents`, `cones`,
    `membership`, `run_log`) alongside the two genuinely mutable pieces of
    loop-local state the extracted bodies read AND write (`done`,
    `quota_exhausted_since`) -- held here BY REFERENCE (not copied), so a
    mutation made inside one helper call (e.g. ``done.add(tid)``) is visible on
    the very next wave, exactly as the old inline closure was.
    """

    repo_paths: dict[str, str]
    agents: dict
    cones: dict[str, dict[str, set[str]]]
    membership: dict[str, set[tuple[str, str]]]
    run_log: logging.LoggerAdapter
    done: set[str]
    quota_exhausted_since: float | None = None
    # --- E-Wk9Tz3 task isolation (T-En8Hd4) ---
    # Absolute run directory (<workspace_root>/.orchestrator/runs/<run_id>); set once at
    # run() setup, read by _activate_integration/_sync_checkout/_reconcile_*.
    run_dir: str = ""
    # Constructed once, lazily, at the first isolated dispatch (or at resume, if the run
    # already had integration active) -- never per-task, never per-wave.
    worktree_manager: WorktreeManager | None = None
    integrator: Integrator | None = None
    # HLD §9.2: loaded ONCE at run start (empty-on-any-error fallback), never per wave.
    hotspots: dict[str, float] = field(default_factory=dict)
    # AC-4: latches True on the first degrade so a later isolated dispatch never re-probes
    # or re-warns; also True once integration is genuinely active (no more probing needed).
    integration_degraded: bool = False
    # S-7: emit worktree.retention_high at most once per run.
    retention_warned: bool = False
    # E-Wk9Tz3 T-Wl2Bq7: set (only) when this run's `_activate_integration` /
    # `_reconcile_integration_on_resume` successfully claims the workspace lock -- held for
    # the run's entire lifetime, released ONLY in `run()`'s `finally`. `None` for the
    # byte-identical non-isolated path (NFR-2) and for `workspace_lock: "off"`/a denied
    # claim, both of which never touch this field.
    workspace_lock: WorkspaceRunLock | None = None


class Orchestrator:
    """Drives a WorkflowSpec DAG to completion.

    Design invariants:
    - Engine never reads artifact or instruction file contents (NFR-1).
    - All artifact access goes through ArtifactStore.resolve()/exists() only.
    - Injectable sleeper and cancel_fn for deterministic testing.

    Parameters
    ----------
    executor:
        Executor implementation (ClaudeCliExecutor in production, FakeExecutor in tests).
    artifact_store:
        Path resolution + existence checks.
    runstate_store:
        Persist and load RunState.
    sleeper:
        Injectable sleep function (default: time.sleep).
    cancel_fn:
        Returns True when the run should be cancelled (default: never).
    budget_manager:
        Optional token budget manager; when None, budget enforcement is skipped.
    estimator:
        Optional token estimator paired with budget_manager; when None, budget gate is skipped.
    clock:
        Injectable clock returning the current UTC datetime (default: datetime.now(UTC)).
    quota_max_wait_seconds:
        Maximum total seconds ao will wait across consecutive quota-exhaustion events before
        giving up (default: DEFAULT_QUOTA_MAX_WAIT_SECONDS).  The timer resets after each
        successful task — it tracks only the current exhaustion episode.
    quota_poll_seconds:
        How long to sleep between quota-exhaustion re-run attempts
        (default: DEFAULT_QUOTA_POLL_SECONDS).
    monitor:
        Agent-based monitoring/self-healing implementation (E-XyfjuZ). Defaults to a fresh
        `RuleBasedMonitor()` (deterministic, zero-cost) when None — recommend-mode breakers
        and self-heal both need SOME monitor available so a workflow that declares
        `mode: "recommend"` gets consistent behavior regardless of whether the caller wired
        one up explicitly. Only ever consulted for `mode: "recommend"` breaker trips
        (Consult Point A) and, when `self_heal_enabled`, task failures (Consult Point B) --
        hard-mode breakers and disabled self-heal are completely unaffected (byte-identical).
    max_extensions_per_breaker:
        Cap on monitor-driven extensions per breaker id this run (default
        DEFAULT_MAX_EXTENSIONS_PER_BREAKER). Enforced centrally here, never trusted to the
        Monitor implementation — bound exhausted halts regardless of the monitor's answer.
        Tracked via `count_monitor_breaker_extensions(state, breaker_id)` (derived from
        `RunState.monitor_decisions`, D9) — independent of the unbounded, operator-driven
        `ao resume --extend-breaker` mechanism (E-3JTmVu), which this never touches.
    max_monitor_calls_per_run:
        Cap on total ACTUAL monitor consults this run, shared across both consult points
        (default DEFAULT_MAX_MONITOR_CALLS_PER_RUN). Exhausting it falls back to the safe
        default (halt / accept_failure) without calling the monitor again.
    self_heal_enabled:
        Opt-in switch for Consult Point B (task-failure self-healing, default False —
        byte-identical to today when unset). When True, a task that settles `"failed"`
        after exhausting its `RetryPolicy` is offered ONE bounded extra retry via
        `self._monitor.decide_task_failure(...)` before the run gives up on it. Scoped
        strictly to `result.status == "failed"` — `timed_out`/`cancelled` are never healed
        (deliberate MVP boundary).
    max_heal_retries_per_task:
        Cap on heal retries per task id this run (default
        DEFAULT_MAX_HEAL_RETRIES_PER_TASK). Enforced centrally here via
        `count_monitor_heal_retries(state, task_id)` (derived from
        `RunState.monitor_decisions`, D9) — heal retries never consume
        `RetryPolicy.max_attempts` accounting (a completely separate counter).
    max_parallel:
        Max independent ready tasks the engine may dispatch concurrently (default
        DEFAULT_MAX_PARALLEL = 1). Invocation-scoped (ADR-0003 §3 / ADR-0007 D5) — plumbed
        identically to quota_max_wait_seconds, never a workflow-spec field. Stored as
        `self._max_parallel`, defensively clamped to >= 1. `run()`'s wave/barrier scheduler
        (ADR-0007) dispatches up to N ready non-barrier tasks at once on a thread pool while
        the engine core stays serialized on the main thread; `emit_tasks`/loop-gate/router
        tasks run solo as barriers. The default of 1 is byte-identical to the pre-ADR-0007
        serial engine.
    """

    def __init__(
        self,
        executor: Executor,
        artifact_store: ArtifactStore,
        runstate_store: RunStateStore,
        sleeper: Callable[[float], None] = time.sleep,
        cancel_fn: Callable[[], bool] = _NO_CANCEL,
        budget_manager: BudgetManager | None = None,
        estimator: TokenEstimator | None = None,
        clock: Callable[[], datetime] | None = None,
        quota_max_wait_seconds: float = DEFAULT_QUOTA_MAX_WAIT_SECONDS,
        quota_poll_seconds: float = DEFAULT_QUOTA_POLL_SECONDS,
        monitor: Monitor | None = None,
        max_extensions_per_breaker: int = DEFAULT_MAX_EXTENSIONS_PER_BREAKER,
        max_monitor_calls_per_run: int = DEFAULT_MAX_MONITOR_CALLS_PER_RUN,
        self_heal_enabled: bool = False,
        max_heal_retries_per_task: int = DEFAULT_MAX_HEAL_RETRIES_PER_TASK,
        max_parallel: int = DEFAULT_MAX_PARALLEL,
        general_instructions: list[str] | None = None,
        worktree_manager: WorktreeManager | None = None,
        integrator: Integrator | None = None,
        resolver_hook: ResolverHook | None = None,
        escalation_hook: EscalationHook | None = None,
        isolation_strict: bool = False,
        isolation_env: dict[str, dict[str, str]] | None = None,
    ) -> None:
        self._executor = executor
        self._store = artifact_store
        self._runstate = runstate_store
        self._sleeper = sleeper
        self._cancel_fn = cancel_fn
        self._budget_manager = budget_manager
        self._estimator = estimator
        self._clock: Callable[[], datetime] = clock or (lambda: datetime.now(UTC))
        self._quota_max_wait_seconds = quota_max_wait_seconds
        self._quota_poll_seconds = quota_poll_seconds
        # Agent-based monitoring & self-healing (E-XyfjuZ). Defaults to a fresh
        # RuleBasedMonitor() (never a shared module-level instance) so each Orchestrator
        # owns an independent, stateless-but-distinct monitor.
        self._monitor: Monitor = monitor if monitor is not None else RuleBasedMonitor()
        self._max_extensions_per_breaker = max_extensions_per_breaker
        self._max_monitor_calls_per_run = max_monitor_calls_per_run
        self._self_heal_enabled = self_heal_enabled
        self._max_heal_retries_per_task = max_heal_retries_per_task
        # Concurrency bound (ADR-0007). Defensive clamp: never below 1 regardless of what a
        # caller passes. NOT consumed by run() yet -- T-j8YLGd wires the wave scheduler that
        # reads this; until then the engine is unconditionally serial (T-JXiI9j plumbing-only).
        self._max_parallel = max(1, int(max_parallel))
        # Workspace/workflow general instructions applied to EVERY task (E-Ui7Kq2 FR-GI1).
        # Stored UNRESOLVED here; resolved through the artifact store per task in
        # _execute_with_retry (same treatment as task.instruction) so the path guard runs
        # on the same code path and no unguarded absolute path can leak into a TaskContext.
        self._general_instructions = list(general_instructions or [])

        # --- E-Wk9Tz3 task isolation (T-En8Hd4). Byte-identical default (NFR-2): every
        # field below is inert unless a workflow actually resolves a task to
        # isolation="worktree" (models.resolve_task_isolation), which requires
        # `defaults.isolation`/`task.isolation` to be set -- absent from every pre-epic
        # spec. ---
        # `worktree_manager`/`integrator`, when given, are used VERBATIM for every run
        # (test substitution -- "same pattern as budget_manager/monitor"); when None (the
        # production default), the real ones are constructed lazily per run, once, at
        # _activate_integration / resume-time reconcile, since they need the run's own
        # run_id/repos/integration heads, none of which exist yet at __init__ time.
        self._injected_worktree_manager = worktree_manager
        self._injected_integrator = integrator
        # HOOK POINTS for T-Rm2Lx7 (resolver_hook)/T-Lr6Ka3 (escalation_hook). Production
        # default is now the REAL implementation of each (T-Rm2Lx7's `resolve_mechanically`
        # / this ticket's `escalation.escalate`) -- neither `T-Rm2Lx7` nor this ticket
        # touches `cli.py` (off-limits to both), so wiring the real ladder into every
        # `Orchestrator(...)` construction that does NOT explicitly override these two
        # params (which is every production caller today) has to happen here, at the
        # constructor's own default-binding point. `_default_resolver_hook`/
        # `_default_escalation_hook` (module-level, above) stay available as an explicit,
        # deliberate "disable the ladder" stand-in (e.g. a caller that wants every conflict
        # to fail straight to the operator with no LLM/rerun spend at all) -- pass them
        # verbatim via `Orchestrator(resolver_hook=_default_resolver_hook, ...)`.
        self._resolver_hook: ResolverHook = (
            resolver_hook if resolver_hook is not None else resolve_mechanically
        )
        self._escalation_hook: EscalationHook = (
            escalation_hook if escalation_hook is not None else resolver_escalation.escalate
        )
        # isolation.strict (HLD §11 M9's `.ao/config.yaml: isolation.strict` / future
        # `--isolation`/`AO_ISOLATION` precedence chain is CLI-owned, M9, not yet built and
        # not this ticket's file (cli.py) -- this constructor parameter is the engine-side
        # injection point that CLI wiring will plug into, mirroring max_parallel's own
        # "invocation-scoped setting" pattern documented on this class).
        self._isolation_strict = isolation_strict
        # isolation.env (HLD §11 M9's `.ao/config.yaml: isolation.env` per-repo overlay,
        # keyed by repo_id) -- same CLI-plumbing gap as isolation_strict above; this is the
        # seam a future M9 ticket wires real config into.
        self._isolation_env: dict[str, dict[str, str]] = {
            k: dict(v) for k, v in (isolation_env or {}).items()
        }

    def run(
        self,
        workflow: WorkflowSpec,
        reposets: dict,
        agents: dict,
        run_state: RunState | None = None,
    ) -> RunState:
        """Execute *workflow* to completion and return the final RunState.

        Parameters
        ----------
        workflow:
            Parsed and cross-validated WorkflowSpec.
        reposets:
            Dict of repo_set_id -> RepoSet loaded from config.
        agents:
            Dict of agent_id -> AgentSpec loaded from config.
        run_state:
            Existing RunState to resume from; if None, a new run is created.
        """
        # Validate DAG structure (raises CycleError on cycle)
        graph = build_dag(workflow)
        order = graph.topological_order()

        state = run_state or self._runstate.new_run(workflow)
        self._runstate.save(state)

        # Derive run directory: <workspace>/.orchestrator/runs/<run_id>
        # The runstate store root is <workspace>/.orchestrator/runs — go up two
        # levels from the state.json path to get the workspace.
        run_dir = str(self._runstate._path(state.run_id).parent)
        log_path = os.path.join(run_dir, "run.log")

        # Attach per-run structured log handler (FR-2, FR-3).
        # Detached in finally so it is removed even if run() raises.
        attach_run_handler(state.run_id, log_path)
        run_log = get_run_logger(state.run_id)
        run_log.info("run.start", extra={"event": "run.start", "workflow_id": workflow.id})

        # E-Wk9Tz3 T-Wl2Bq7: declared here (not inside `try`) so `finally` can always
        # reference it, even for an exception raised before `ctx` itself is constructed --
        # at which point no lock could have been claimed yet either, so `None` there is
        # always the correct "nothing to release" answer.
        ctx: _RunContext | None = None
        try:
            repo_set = reposets[workflow.repo_set]
            repo_paths = {r.id: self._store.resolve(r.path) for r in repo_set.repos}

            # Merge any previously injected tasks back into the workflow before building
            # the DAG (handles the case where run_state comes from prepare_resume and
            # injected tasks were already merged there, but also guards a direct re-use
            # of a RunState that skipped prepare_resume).
            # Note: prepare_resume handles the canonical resume path; this guard ensures
            # the engine is always safe even if called with a raw loaded state.
            existing_ids = {t.id for t in workflow.tasks}
            if run_state and run_state.injected_tasks:
                for inj in run_state.injected_tasks:
                    if inj.id not in existing_ids:
                        workflow.tasks.append(inj)
                        existing_ids.add(inj.id)

            graph = build_dag(workflow)
            order = graph.topological_order()

            # Route cones/membership (LLD §4.3, T-m2h5t7): computed ONCE here and
            # cached for the whole run() call — never recomputed on injection.
            # Injected task ids are new and never appear in a pre-computed cone;
            # they inherit their emitter's route instead (§5.5, R4). Empty when
            # workflow.branches is empty (the common/pre-routing case).
            cones, membership = compute_cones(workflow, graph)

            # Terminal-success set: skip re-running tasks that already succeeded
            done: set[str] = {
                tid for tid, ts in state.tasks.items() if ts.status in ("succeeded", "skipped")
            }

            # Predecessor map for the ready-set (ADR-0007 D6); rebuilt alongside
            # `graph`/`order` on every RESHAPED signal.
            preds = self._predecessors(graph)
            failed = False

            # Per-run context (T-j8YLGd): bundles the values the old inline loop
            # closed over (repo_paths/agents/cones/membership/run_log) plus the two
            # pieces of genuinely mutable loop-local state the extracted prepare/
            # settle bodies read AND write (`done`, `quota_exhausted_since`) into one
            # shared object so mutations made inside a helper call are visible on the
            # very next wave, exactly as the old inline closure was. Tracks the epoch
            # when the current quota-exhaustion episode began; resets to None after
            # any task succeeds (new exhaustion → fresh max_wait window) — unchanged
            # semantics from the old `_quota_exhausted_since` local.
            ctx = _RunContext(
                repo_paths=repo_paths,
                agents=agents,
                cones=cones,
                membership=membership,
                run_log=run_log,
                done=done,
                run_dir=run_dir,
            )

            # E-Wk9Tz3 (HLD §9.2, R-5): hotspots load ONCE at run start, never per wave, with
            # an empty-on-any-error fallback (an unresolvable/absent path is not fatal --
            # hotspots are advisory scheduling data, never a gate). Skipped entirely when
            # `resolve_overlap_preference` is "off" (every pre-epic workflow, NFR-2): the
            # ONLY consumer is `rank_wave`, which never runs at "off", so loading (and
            # `load_hotspots`'s own missing-file WARNING, which -- being a CHILD of the
            # "agent_orchestrator" logger -- would otherwise land in every run's run.log
            # regardless of isolation) would be pure, observable overhead for a workflow
            # that will never touch this feature.
            if resolve_overlap_preference(workflow) == OVERLAP_SOFT:
                try:
                    ctx.hotspots = load_hotspots(
                        self._store.resolve(workflow.scheduling.hotspots_path)
                    ).weights()
                except ArtifactPathError:
                    ctx.hotspots = {}

            # E-Wk9Tz3 resume: `state.integration.active` can only be True here when
            # *run_state* was loaded from a previous (crashed/interrupted) run -- reconcile
            # orphaned worktrees/refs before dispatching anything (HLD §12.1).
            if state.integration.active:
                self._reconcile_integration_on_resume(state, workflow, ctx)

            # Wave/barrier scheduler (ADR-0007 D3): the entire engine core stays
            # serialized on the main thread (RunState mutation, save, budget gate/
            # charge/reconcile, breaker eval, router hook, injection, loop clone,
            # quota/429/self-heal decisions) -- only `_run_with_retries` (a pure
            # reader) runs on a worker. `pool.shutdown(wait=True)` is guaranteed by
            # the `with` block on every exit path, including an exception raised by
            # a worker (NFR-4 -- no thread leak).
            with ThreadPoolExecutor(max_workers=self._max_parallel) as pool:
                in_flight: dict[Future[WorkerOutcome], str] = {}
                while True:
                    if self._cancel_fn():
                        run_log.info(
                            "Cancellation requested; halting",
                            extra={"event": "run.cancelled"},
                        )
                        state.status = "cancelled"
                        failed = True
                        self._drain_remaining(in_flight, workflow, state, ctx)
                        break

                    # ---- FILL: launch ready non-barrier tasks up to N ----
                    ready = self._ready_ids(order, preds, state, done, set(in_flight.values()))
                    if not ready and not in_flight:
                        break  # nothing ready, nothing running -> run complete

                    # E-Wk9Tz3 R-5: soft overlap preference reorders (never filters) the
                    # candidate list. `resolve_overlap_preference` derives "off" unless the
                    # workflow isolates a task, so a workflow with no isolation anywhere
                    # keeps today's exact order (NFR-2) -- and `rank_wave` itself is a
                    # provable no-op whenever the caller has <= 1 open slot (max_parallel=1
                    # always does), so `pref == "soft"` alone is not enough to move dispatch
                    # order at N=1; both guards hold independently.
                    pref = resolve_overlap_preference(workflow)
                    candidates = (
                        rank_wave(
                            ready,
                            {t.id: t.touches for t in workflow.tasks},
                            ctx.hotspots,
                            self._max_parallel - len(in_flight),
                        )
                        if pref == OVERLAP_SOFT
                        else ready
                    )

                    for tid in candidates:
                        if len(in_flight) >= self._max_parallel:
                            break
                        barrier = self._is_barrier(workflow.task(tid), workflow, state)
                        if barrier and in_flight:
                            break  # barrier must run solo -- drain the wave first
                        prep = self._prepare_and_maybe_dispatch(
                            tid, workflow, graph, state, ctx, in_flight_nonempty=bool(in_flight)
                        )
                        if prep.signal == "skipped":
                            continue
                        if prep.signal == "halt":
                            failed = True
                            break
                        if prep.signal == "blocked":
                            # Budget gate said wait (T-VSfAUN, FR-6): stop filling
                            # this wave. If siblings are in flight, `_prepare_and_
                            # maybe_dispatch` already skipped sleeping (R3 -- would
                            # deadlock on capacity they hold) so we fall straight
                            # through to drain one of them below, which reconciles
                            # actuals and may free the window before the next
                            # wave re-gates. If nothing is in flight, it already
                            # slept once inline before returning here, and the
                            # `in_flight == {}` branch just below re-enters the
                            # fill loop immediately to re-gate (byte-identical to
                            # the old internal retry loop at N=1, where in_flight
                            # is always empty at gate time).
                            break
                        assert prep.task is not None  # DISPATCH always carries its payload
                        fut = pool.submit(
                            self._run_and_integrate,
                            task=prep.task,
                            workflow=workflow,
                            agents=agents,
                            repo_paths=(
                                prep.repo_paths if prep.repo_paths is not None else repo_paths
                            ),
                            state=state,
                            dynamic_input_paths=prep.dynamic_input_paths,
                            task_manifest_path=prep.task_manifest_path,
                            gate_output_path=prep.gate_output_path,
                            store=prep.store,
                            task_iso=prep.task_iso,
                            integrator=ctx.integrator,
                            run_integration=prep.run_integration,
                            env_overlay=prep.env_overlay,
                            agent_id=prep.task.agent,
                            cycle=prep.cycle,
                        )
                        in_flight[fut] = tid
                        if barrier:
                            break  # launched solo -- go drain it before filling further

                    if failed:
                        break
                    if not in_flight:
                        continue  # everything blocked this pass -> re-evaluate

                    # ---- DRAIN: one completion at a time, settle on main thread ----
                    done_fut = next(as_completed(list(in_flight)))
                    settled_tid = in_flight.pop(done_fut)
                    outcome = done_fut.result()
                    settle = self._settle_completed_task(settled_tid, outcome, workflow, state, ctx)
                    if settle.signal == "halt":
                        failed = True
                        self._drain_remaining(in_flight, workflow, state, ctx)
                        break
                    if settle.signal == "requeue":
                        # Normalizes every requeue path uniformly to "pending":
                        # the quota/self-heal paths already set this status inside
                        # _settle_completed_task (verbatim), but the provider-429-wait
                        # path never did (today's code re-visits the same cursor
                        # position regardless of ts.status, so it never needed to) --
                        # the ready-set model needs an explicit non-running status to
                        # re-admit the task on a later wave, so this is applied
                        # unconditionally regardless of which site requeued.
                        state.tasks[settled_tid].status = "pending"
                    elif settle.signal == "reshaped":
                        assert settle.graph is not None and settle.order is not None
                        graph = settle.graph
                        order = settle.order
                        preds = self._predecessors(graph)
                    # settled: nothing extra
                # pool.shutdown(wait=True) via `with`, on every exit path

            # E-Wk9Tz3: run-end finalize (AC-12) -- best-effort final checkout sync +
            # worktree reconcile + a summary log line. Never changes the run's own verdict
            # (state.status / failed are untouched here regardless of sync outcome).
            if state.integration.active:
                self._sync_checkout(state, workflow, ctx)
                if ctx.worktree_manager is not None:
                    ctx.worktree_manager.reconcile({t.id for t in workflow.tasks})
                self._log_integration_summary(state, run_log)

            if not failed and state.status == "running":
                state.status = "succeeded"

            run_log.info(
                "run.end",
                extra={"event": "run.end", "status": state.status},
            )
            self._runstate.save(state)
            return state

        finally:
            # E-Wk9Tz3 T-Wl2Bq7 AC-4: released here (every run() exit path -- success,
            # halt, cancel, or an exception raised anywhere above) alongside
            # detach_run_handler, so an exception or a cancel can never leak the
            # workspace lock. `release()` never raises; `ctx`/`ctx.workspace_lock` are
            # both `None` on the byte-identical non-isolated path (NFR-2).
            if ctx is not None and ctx.workspace_lock is not None:
                ctx.workspace_lock.release()
            detach_run_handler(state.run_id)

    def _prepare_and_maybe_dispatch(
        self,
        tid: str,
        workflow: WorkflowSpec,
        graph: Graph,
        state: RunState,
        ctx: _RunContext,
        in_flight_nonempty: bool = False,
    ) -> DispatchPrep:
        """Pre-dispatch checks + budget gate/charge for task *tid*.

        Extracted VERBATIM from the old per-task cursor-loop body (pre-T-j8YLGd
        ``engine.py`` lines ~279-577) -- same statements, same order, same
        ``self._runstate.save(state)`` sites. The only changes are mechanical:
        the loop-local ``done``/``membership``/``agents``/``repo_paths`` closures
        become ``ctx.*`` lookups, each bare ``continue`` (skip) becomes
        ``return DispatchPrep(signal="skipped")``, and each terminal
        ``failed = True; break`` becomes ``return DispatchPrep(signal="halt")``
        (immediately after the identical status-mutation + save statements the
        original had before its break). The now-unreachable
        ``if failed: save(); break`` that used to sit after the budget gate's
        inner retry loop is dropped -- every failure path inside that loop now
        returns directly, so nothing falls through to it any more.

        Returns ``DispatchPrep(signal="dispatch", ...)`` with the exact payload
        (task, dynamic_input_paths, resolved manifest/gate paths) the caller
        needs to ``pool.submit(self._run_and_integrate, ...)`` -- mirroring
        today's direct inline call one-for-one, just moved to the call site.
        E-Wk9Tz3 (T-En8Hd4) additionally populates the isolation fields
        (``store``/``task_iso``/``repo_paths``/``run_integration``/``env_overlay``) --
        see `DispatchPrep`'s own docstring.

        ``in_flight_nonempty`` (T-VSfAUN, FR-6/ADR-0007 §7.1): True when the
        caller already has at least one sibling task dispatched THIS wave (or
        still draining from a prior one). The budget-wait branch below uses it
        to choose between the two documented, non-deadlocking strategies:
        return ``BLOCKED`` immediately (never sleep while capacity is held by
        an in-flight sibling -- draining a completion may free the window; R3)
        when True, or sleep inline exactly as the pre-epic serial engine did
        when False (always the case at ``N=1``, since nothing can ever be in
        flight at gate time then -- byte-identical by construction).
        """
        run_log = ctx.run_log

        # Not-taken skip (LLD §5.3): a task on an unselected route never
        # dispatches, never bills. Checked ahead of `done` (which stays
        # succeeded/skipped-only, §5.6) so the two sets never conflate.
        ts0 = state.tasks.get(tid)
        if ts0 is not None and ts0.status == "not_taken":
            return DispatchPrep(signal="skipped")

        if tid in ctx.done:
            return DispatchPrep(signal="skipped")

        task = workflow.task(tid)
        task_log = get_run_logger(state.run_id, tid)

        # Resume / idempotency: skip completed tasks. E-Wk9Tz3 R-3/D5: the integration gate
        # is applied to BOTH of `should_skip`'s branches -- `runstate.py` is off-limits to
        # this ticket (the base store/should_skip stay byte-identical, NFR-2), so the extra
        # condition is layered at the call site instead, via `_integration_allows_skip`.
        if self._runstate.should_skip(task, state) and self._integration_allows_skip(tid, state):
            task_log.info(
                "Skipping task (already succeeded with outputs present)",
                extra={"event": "task.skip"},
            )
            # Mutate in-place to preserve persisted fields (e.g. dynamic_outputs)
            ts = state.tasks.setdefault(tid, TaskRunState())
            ts.status = "skipped"
            ctx.done.add(tid)
            self._runstate.save(state)
            return DispatchPrep(signal="skipped")

        # ---- E-Wk9Tz3 task isolation (T-En8Hd4): resolve mode, lazily activate, ensure
        # the worktree. Placed AFTER should_skip (a task about to be skipped must never pay
        # for a worktree it will never use) and BEFORE the missing-inputs / budget-estimate
        # checks below, both of which need `ctx_store` to see a predecessor's INTEGRATED
        # work inside THIS task's freshly-created worktree (§7.4). ----
        ts_pre = state.tasks.setdefault(tid, TaskRunState())
        ts_pre.dispatch_cycle += 1  # R-21: monotonic across requeues/resumes
        iso_mode = resolve_task_isolation(task, workflow)
        task_iso: TaskIsolation | None = None
        ctx_store: ArtifactStore = self._store
        env_overlay: dict[str, str] = {}
        run_integration: RunIntegrationSnapshot | None = None

        if iso_mode == ISOLATION_WORKTREE:
            if not state.integration.active and not ctx.integration_degraded:
                self._activate_integration(state, workflow, ctx)
            if not state.integration.active:
                if state.status == "failed":  # isolation_strict turned the degrade fatal
                    self._runstate.save(state)
                    return DispatchPrep(signal="halt")
                iso_mode = ISOLATION_NONE  # FR-12: degrade to unisolated for this task

        if iso_mode == ISOLATION_WORKTREE:
            assert ctx.worktree_manager is not None
            try:
                task_iso = ctx.worktree_manager.ensure(tid, ts_pre.dispatch_cycle, task.outputs)
            except WorktreeCollisionError as exc:
                task_log.error(
                    "Worktree collision: %s",
                    exc,
                    extra={"event": "task.fail", "reason": "worktree_collision"},
                )
                state.tasks[tid] = TaskRunState(
                    status="failed", dispatch_cycle=ts_pre.dispatch_cycle
                )
                self._runstate.save(state)
                state.status = "failed"
                return DispatchPrep(signal="halt")
            ctx_store = IsolatedArtifactView(
                base=self._require_local_fs_store(), task_isolation=task_iso
            )
            self._record_task_integration_pending(state, tid, task_iso)
            assert state.integration.branch is not None  # set together with active=True
            env_overlay = self._build_task_env(task_iso, state.integration.branch)
            run_integration = RunIntegrationSnapshot(
                run_id=state.run_id, branch=state.integration.branch, run_dir=ctx.run_dir
            )
        else:
            if state.integration.active:
                ok = self._sync_checkout(state, workflow, ctx)
                if not ok and workflow.integration.sync_checkout != "never":
                    state.status = "failed"
                    self._runstate.save(state)
                    return DispatchPrep(signal="halt")

        iso_repo_paths = _iso_repo_paths(task_iso) if task_iso is not None else None

        # Join handling (LLD §5.4): a convergence task with a not_taken
        # dependency propagates not_taken (join="all") or is skipped only
        # when EVERY effective dependency is not_taken (join="any").
        # Evaluated before dispatch so a not_taken task never reaches the
        # budget gate or the executor.
        if self._apply_join(task, state, ctx.membership, graph.producer_of) == "not_taken":
            self._runstate.save(state)
            return DispatchPrep(signal="skipped")

        # Check required inputs exist. join="any" tasks relax this check
        # (§5.4a): an input whose SOLE producer is not_taken never gets
        # written and must not fail the task — apply_join already proved
        # at least one other effective dependency is live.
        if task.join == "any":
            optional_inputs: set[str] = set()
            for inp in task.inputs:
                producer = graph.producer_of(inp)
                if (
                    producer is not None
                    and state.tasks.get(producer) is not None
                    and state.tasks[producer].status == "not_taken"
                ):
                    optional_inputs.add(inp)
            missing = [
                inp
                for inp in task.inputs
                if inp not in optional_inputs and not ctx_store.exists(inp)
            ]
        else:
            missing = [inp for inp in task.inputs if not ctx_store.exists(inp)]
        if missing:
            task_log.error(
                "Missing required inputs: %s",
                missing,
                extra={"event": "task.fail", "reason": "missing_inputs"},
            )
            state.tasks[tid] = TaskRunState(status="failed")
            self._runstate.save(state)
            state.status = "failed"
            return DispatchPrep(signal="halt")

        # Collect dynamic outputs from all upstream tasks declared in depends_on
        dynamic_input_paths: list[str] = []
        for dep_id in task.depends_on:
            dep_ts = state.tasks.get(dep_id)
            if dep_ts and dep_ts.dynamic_outputs:
                dynamic_input_paths.extend(dep_ts.dynamic_outputs)

        # _estimate is declared at task-block scope so the reconcile block can
        # reference it regardless of whether the budget gate ran (T-algywf, NFR-3).
        _estimate = 0

        # ---- Budget gate (T-algywf, FR-1..FR-3, FR-4) ----
        if self._budget_manager is not None and self._estimator is not None:
            _est_agent = ctx.agents[task.agent]
            _est_instr = ctx_store.resolve(task.instruction)
            _est_inputs = [ctx_store.resolve(p) for p in task.inputs]
            _est_cfg = (workflow.budget.estimator if workflow.budget else None) or EstimatorConfig()
            _est_ctx = TaskContext(
                run_id=state.run_id,
                task_id=tid,
                agent=_est_agent,
                instruction_path=_est_instr,
                general_instruction_paths=self._resolve_general_instructions(
                    workflow, store=ctx_store
                ),
                input_paths=_est_inputs,
                output_paths=[ctx_store.resolve(p) for p in task.outputs],
                dynamic_input_paths=dynamic_input_paths,
                repo_paths=iso_repo_paths if iso_repo_paths is not None else ctx.repo_paths,
                timeout_seconds=task.timeout_seconds or workflow.defaults.timeout_seconds,
            )
            _estimate = self._estimator.estimate(_est_ctx, _est_cfg)

            # Resume double-charge guard (R2, NFR-3, R-1b): if the PREVIOUS dispatch
            # cycle of this task was charged but never reconciled (the process crashed
            # between charge_estimate() and settle), reverse that stale estimate before
            # re-gating. `ts_pre.dispatch_cycle` was already incremented for THIS
            # dispatch above, so the stale cycle -- if any -- is exactly one behind it:
            # `prepare_resume` carries `dispatch_cycle` forward verbatim rather than
            # resetting it (runstate.py), and nothing can charge cycle N+1 before cycle
            # N's own settle has run (charge only ever happens here, once per prepare
            # call). Cycle-keyed (R-1b) rather than task-id-keyed: `charged_estimate`
            # no longer collapses every cycle of a task onto one dict entry, so the
            # stale entry must be looked up by ITS OWN cycle, not the current one --
            # membership alone (no separate `reconciled_tasks` check) is sufficient,
            # since reconcile()/reverse_estimate() always pop a cycle's key once it is
            # settled.
            _stale_cycle = ts_pre.dispatch_cycle - 1
            _stale_key = cycle_key(tid, _stale_cycle)
            if _stale_key in state.budget_counters.charged_estimate:
                self._budget_manager.reverse_estimate(
                    tid, state.budget_counters, cycle=_stale_cycle
                )
                run_log.info(
                    "Reversed stale estimate for task %s on resume",
                    tid,
                    extra={
                        "event": "budget.resume_reverse",
                        "task_id": tid,
                        "cycle": _stale_cycle,
                    },
                )

            # Single gate check per call (T-VSfAUN, FR-6/ADR-0007 §7.1). Re-gating
            # after a window-roll wait now happens by the CALLER re-invoking this
            # method on a later wave (the `blocked` returns below) instead of an
            # internal retry loop -- this is what lets in-flight siblings drain
            # instead of the engine sleeping while THEY hold the capacity that
            # might free the window (R3: never sleep on a rolled window while
            # capacity is held by in-flight siblings -- that is the deadlock).
            _decision = self._budget_manager.gate(tid, _estimate, state.budget_counters)
            if not _decision.admit:
                task_log.warning(
                    "Budget gate blocked task %s: blocked_by=%s next_available=%s",
                    tid,
                    _decision.blocked_by,
                    _decision.next_available_epoch,
                    extra={
                        "event": "budget.gate_block",
                        "task_id": tid,
                        "blocked_by": _decision.blocked_by,
                        "next_available_epoch": _decision.next_available_epoch,
                        "estimate": _estimate,
                    },
                )

                # Unsatisfiable: estimate exceeds the entire budget/window — would wait
                # forever; stop immediately.
                if self._is_unsatisfiable(_estimate, _decision):
                    run_log.error(
                        "Task %s estimate %d exceeds %s limit — unsatisfiable; stopping",
                        tid,
                        _estimate,
                        _decision.blocked_by,
                        extra={
                            "event": "budget.exhausted",
                            "task_id": tid,
                            "blocked_by": _decision.blocked_by,
                        },
                    )
                    # Re-frame onto trip->record->act (T-r3j9b6, LLD §8.2): additive
                    # only -- the event/status/break above are unchanged (ADR-RC-003).
                    record_trip(
                        state=state,
                        breaker_id=BUILTIN_BUDGET_UNSATISFIABLE,
                        condition="projected_cost_exceeds",
                        action="fail",
                        detail={
                            "task_id": tid,
                            "estimate": _estimate,
                            "blocked_by": _decision.blocked_by,
                        },
                        clock=self._clock,
                        run_log=run_log,
                    )
                    state.status = "failed"
                    self._runstate.save(state)
                    return DispatchPrep(signal="halt")

                _on_exhaustion = workflow.budget.on_exhaustion if workflow.budget else "stop"

                if _on_exhaustion == "stop":
                    run_log.warning(
                        "Budget exhausted; stopping run",
                        extra={
                            "event": "budget.exhausted",
                            "task_id": tid,
                            "blocked_by": _decision.blocked_by,
                            "next_available_epoch": _decision.next_available_epoch,
                        },
                    )
                    # Re-frame onto trip->record->act (T-r3j9b6, LLD §8.2): additive
                    # only -- the event/status/break above are unchanged (ADR-RC-003).
                    # Condition name reflects which limit blocked admission
                    # (_decision.blocked_by), not a single hardcoded string.
                    _budget_condition = (
                        "total_tokens" if _decision.blocked_by == "total" else "rate_window"
                    )
                    record_trip(
                        state=state,
                        breaker_id=BUILTIN_BUDGET_EXHAUSTED,
                        condition=_budget_condition,
                        action="fail",
                        detail={
                            "task_id": tid,
                            "blocked_by": _decision.blocked_by,
                            "next_available_epoch": _decision.next_available_epoch,
                        },
                        clock=self._clock,
                        run_log=run_log,
                    )
                    state.status = "failed"
                    self._runstate.save(state)
                    return DispatchPrep(signal="halt")
                else:  # wait
                    # A sibling holds capacity this wave: do NOT sleep here (that
                    # would block the whole engine while the sibling could finish
                    # and free the window itself). Stop filling; the run loop
                    # drains a completion (reconciling actuals, possibly rolling
                    # the window) and re-gates this task on a later wave.
                    if in_flight_nonempty:
                        return DispatchPrep(signal="blocked")

                    # Nothing in flight (always true at N=1, since nothing can be
                    # dispatched-but-undrained at gate time then): sleep inline
                    # exactly as the pre-epic serial engine did, then return
                    # BLOCKED so the caller re-gates via a fresh call. Same event
                    # sequence (gate_block/wait/resume, repeated) as the old
                    # internal retry loop -- nothing else can progress meanwhile.
                    _next = _decision.next_available_epoch or (self._clock().timestamp() + 1)
                    _sleep_secs = max(0.0, _next - self._clock().timestamp())
                    run_log.info(
                        "Budget wait: sleeping %.1f seconds until %.3f",
                        _sleep_secs,
                        _next,
                        extra={
                            "event": "budget.wait",
                            "task_id": tid,
                            "sleep_seconds": _sleep_secs,
                            "next_available_epoch": _next,
                        },
                    )
                    self._sleeper(_sleep_secs)
                    if self._cancel_fn():
                        run_log.info(
                            "Cancelled during budget wait",
                            extra={"event": "run.cancelled"},
                        )
                        state.status = "cancelled"
                        self._runstate.save(state)
                        return DispatchPrep(signal="halt")
                    run_log.info(
                        "Budget wait ended; re-gating task %s",
                        tid,
                        extra={"event": "budget.resume", "task_id": tid},
                    )
                    return DispatchPrep(signal="blocked")

            # Charge the estimate (admitted); keyed by THIS dispatch cycle (R-1b) so a
            # redispatch of this task charges its own ledger entry rather than
            # overwriting/racing an earlier, already-settled cycle's.
            self._budget_manager.charge_estimate(
                tid, _estimate, state.budget_counters, cycle=ts_pre.dispatch_cycle
            )
            self._runstate.save(state)
            task_log.info(
                "Budget charged estimate %d for task %s",
                _estimate,
                tid,
                extra={
                    "event": "budget.charge",
                    "task_id": tid,
                    "cycle": ts_pre.dispatch_cycle,
                    "estimate": _estimate,
                    "consumed_tokens": state.budget_counters.consumed_tokens,
                },
            )

        # Mark as running
        ts = state.tasks.setdefault(tid, TaskRunState())
        ts.status = "running"
        # Only set started_at on the task's FIRST dispatch (E-3JTmVu FR-1 fix): the
        # quota-exhaustion/429/budget-wait paths (now in _settle_completed_task) return
        # REQUEUE, which sends this task back through THIS method on a later wave to
        # redispatch the SAME task after a real sleep -- without this guard, that
        # redispatch used to overwrite started_at, silently excluding the wait from
        # `run_active_seconds`'s (ended_at - started_at) sum even though those waits are
        # genuine engine-busy/blocked time on this task, not an operator-initiated stop.
        # Safe: started_at has exactly one writer (here) and prepare_resume already
        # hands a fresh TaskRunState() (started_at=None) to any task reset for `ao
        # resume`, so a resumed dispatch still gets its own fresh started_at.
        if ts.started_at is None:
            ts.started_at = datetime.now(UTC).isoformat()
        self._runstate.save(state)
        task_log.info("Task started", extra={"event": "task.start"})

        # Resolve emit_tasks task manifest path (Area 2)
        resolved_task_manifest_path: str | None = None
        if task.emit_tasks and task.task_manifest_path:
            resolved_task_manifest_path = self._store.resolve(task.task_manifest_path)

        # Resolve gate output path for loop gate tasks (Area 2)
        gate_loop = self._loop_for_gate(workflow, tid)
        resolved_gate_output_path: str | None = None
        if gate_loop is not None:
            cur_iter = state.loop_iterations.get(gate_loop.id, 1)
            raw_gate_path = self._gate_path_for_iter(gate_loop, cur_iter)
            resolved_gate_output_path = self._store.resolve(raw_gate_path)

        return DispatchPrep(
            signal="dispatch",
            task=task,
            dynamic_input_paths=dynamic_input_paths,
            task_manifest_path=resolved_task_manifest_path,
            gate_output_path=resolved_gate_output_path,
            store=(ctx_store if task_iso is not None else None),
            task_iso=task_iso,
            repo_paths=iso_repo_paths,
            run_integration=run_integration,
            env_overlay=(env_overlay or None),
            cycle=ts_pre.dispatch_cycle,
        )

    def _settle_completed_task(
        self,
        tid: str,
        outcome: WorkerOutcome,
        workflow: WorkflowSpec,
        state: RunState,
        ctx: _RunContext,
    ) -> SettleResult:
        """Post-dispatch settlement for task *tid*'s completed *outcome*.

        E-Wk9Tz3 (T-En8Hd4): *outcome* replaces the old bare ``TaskResult`` parameter;
        ``result = outcome.result`` below is the ONLY change to this docstring's own
        "extracted verbatim" body -- every ``result.*`` reference in the (unedited) sections
        that follow keeps working unchanged. The NEW integration-settle block sits right
        after the existing (unedited) missing-outputs check and before the existing
        (unedited) ``ctx.done.add(tid)``/router-hook section -- see that block's own
        docstring for the full state-machine.

        Extracted VERBATIM from the old per-task cursor-loop body (pre-T-j8YLGd
        ``engine.py`` lines ~579-1077) -- same statements, same order, same
        ``self._runstate.save(state)`` sites; nothing inside was reordered or
        "cleaned up" while moving it (T-j8YLGd's N=1 byte-identical gate depends
        on this). Only the control-flow shell changed:
        - each ``cursor -= 1; continue`` (quota / provider-429-wait / self-heal
          requeue) -> ``return SettleResult(signal="requeue")``, keeping
          whatever reverse-estimate / ``self._sleeper`` / ``ts.status =
          "pending"`` / save statements that site already had (note: the
          provider-429-wait site never set ``ts.status = "pending"`` today --
          that asymmetry is preserved; ``run()`` normalizes status to
          "pending" uniformly for every REQUEUE so the ready-set model can
          re-admit the task on a later wave regardless of which site fired).
        - each terminal ``failed = True; break`` -> ``return
          SettleResult(signal="halt")``, keeping whatever ``state.status = ...``
          / save statements that site already had before its break (some sites
          saved before the status write, some after -- both orders preserved
          exactly per site).
        - the two DAG-rebuild sites (emit_tasks manifest injection, loop-gate
          clone injection) -> the inject + ``build_dag`` still happen HERE
          (so the log lines that read ``order``/``cursor`` keep their exact
          values), then ``return SettleResult(signal="reshaped", graph=graph,
          order=order)`` hands the already-computed graph/order back so
          ``run()`` never has to rebuild the DAG a second time (which would
          double-emit dag.py's inferred-edge warning into run.log).
        - the loop-gate site drops the now-unused ``cursor`` half of
          ``self._recompute_order(...)`` (that log line never reads it) and
          calls ``graph.topological_order()`` directly for the identical
          ``order`` value -- cursor is not part of the new interface anywhere.
        - the normal fall-through end of the body -> ``return
          SettleResult(signal="settled")``.

        ``ctx.done``/``ctx.quota_exhausted_since``/``ctx.cones`` replace the old
        inline loop's closed-over locals of the same name (mutations are
        visible to ``run()``'s next wave because ``ctx`` is a single shared,
        mutable object, not a copy).
        """
        task = workflow.task(tid)
        task_log = get_run_logger(state.run_id, tid)
        run_log = ctx.run_log
        ts = state.tasks[tid]
        result = outcome.result  # E-Wk9Tz3: the only line the "extracted verbatim" body needs

        # ---- Claude quota exhaustion route (distinct from provider 429) ----
        if result.claude_quota_exhausted:
            # Reverse any estimate charged for this task's CURRENT cycle before
            # re-queuing (R-1b: cycle-keyed, not task-id-keyed -- ts.dispatch_cycle is
            # still the cycle this very dispatch was charged under; nothing mutates it
            # between prepare and settle).
            if self._budget_manager is not None:
                self._budget_manager.reverse_estimate(
                    tid, state.budget_counters, cycle=ts.dispatch_cycle
                )

            now = self._clock().timestamp()
            if ctx.quota_exhausted_since is None:
                ctx.quota_exhausted_since = now

            elapsed = now - ctx.quota_exhausted_since
            remaining = self._quota_max_wait_seconds - elapsed

            if remaining <= 0:
                run_log.error(
                    "Claude quota exhaustion max_wait exceeded "
                    "(%.0f s elapsed, limit=%.0f s); stopping run",
                    elapsed,
                    self._quota_max_wait_seconds,
                    extra={
                        "event": "quota.max_wait_exceeded",
                        "task_id": tid,
                        "elapsed_seconds": elapsed,
                        "max_wait_seconds": self._quota_max_wait_seconds,
                    },
                )
                # Re-frame onto trip->record->act (T-r3j9b6, LLD §8.2): additive
                # only -- the event/status/break above are unchanged (ADR-RC-003).
                record_trip(
                    state=state,
                    breaker_id=BUILTIN_QUOTA_MAX_WAIT,
                    condition="quota_exhaustion_wait_exceeded",
                    action="fail",
                    detail={
                        "task_id": tid,
                        "elapsed_seconds": elapsed,
                        "max_wait_seconds": self._quota_max_wait_seconds,
                    },
                    clock=self._clock,
                    run_log=run_log,
                )
                state.status = "failed"
                self._runstate.save(state)
                return SettleResult(signal="halt")

            sleep_secs = min(self._quota_poll_seconds, remaining)
            run_log.info(
                "Claude quota exhausted on task %s; waiting %.0f s (elapsed=%.0f/max=%.0f)",
                tid,
                sleep_secs,
                elapsed,
                self._quota_max_wait_seconds,
                extra={
                    "event": "quota.wait",
                    "task_id": tid,
                    "sleep_seconds": sleep_secs,
                    "elapsed_seconds": elapsed,
                    "max_wait_seconds": self._quota_max_wait_seconds,
                },
            )
            self._sleeper(sleep_secs)

            if self._cancel_fn():
                run_log.info(
                    "Cancelled during quota wait",
                    extra={"event": "run.cancelled"},
                )
                state.status = "cancelled"
                self._runstate.save(state)
                return SettleResult(signal="halt")

            run_log.info(
                "Quota wait ended; re-running task %s",
                tid,
                extra={"event": "quota.resume", "task_id": tid},
            )
            # Reset task to pending so it re-executes cleanly.
            ts.status = "pending"
            self._runstate.save(state)
            return SettleResult(signal="requeue")  # skip budget reconcile + normal outcome handling

        # ---- Budget reconcile / 429 route (T-algywf, FR-4, FR-5, FR-8) ----
        if self._budget_manager is not None and self._estimator is not None:
            if result.provider_rate_limited:
                # Provider 429: reverse the estimate (task will re-run) then stop or
                # wait. Cycle-keyed (R-1b), same reasoning as the quota route above.
                self._budget_manager.reverse_estimate(
                    tid, state.budget_counters, cycle=ts.dispatch_cycle
                )
                _429_decision = self._budget_manager.on_provider_429(
                    result.provider_retry_after_epoch, state.budget_counters
                )
                run_log.warning(
                    "Provider 429 on task %s; retry_after=%.3f",
                    tid,
                    _429_decision.next_available_epoch or 0,
                    extra={
                        "event": "budget.provider_429",
                        "task_id": tid,
                        "next_available_epoch": _429_decision.next_available_epoch,
                    },
                )
                _on_exhaustion_429 = workflow.budget.on_exhaustion if workflow.budget else "stop"
                if _on_exhaustion_429 == "wait" and _429_decision.next_available_epoch is not None:
                    _sleep_secs_429 = max(
                        0.0,
                        _429_decision.next_available_epoch - self._clock().timestamp(),
                    )
                    run_log.info(
                        "Waiting %.1f seconds for provider rate limit to reset",
                        _sleep_secs_429,
                        extra={
                            "event": "budget.wait",
                            "task_id": tid,
                            "sleep_seconds": _sleep_secs_429,
                        },
                    )
                    self._sleeper(_sleep_secs_429)
                    if self._cancel_fn():
                        state.status = "cancelled"
                        self._runstate.save(state)
                        return SettleResult(signal="halt")
                    run_log.info(
                        "Provider rate limit wait ended; re-running task %s",
                        tid,
                        extra={"event": "budget.resume", "task_id": tid},
                    )
                    # Re-run the task
                    self._runstate.save(state)
                    return SettleResult(signal="requeue")
                else:
                    # stop (default) or no next_available — end the run
                    run_log.warning(
                        "Stopping run due to provider rate limit",
                        extra={"event": "budget.exhausted", "task_id": tid},
                    )
                    # Re-frame onto trip->record->act (T-r3j9b6, LLD §8.2): additive
                    # only -- the event/status/break above are unchanged (ADR-RC-003).
                    # Shares BUILTIN_BUDGET_EXHAUSTED with the gate-path stop above
                    # (LLD §8.1 groups both under one builtin id); the condition name
                    # keeps the two sites distinguishable in tripped_breakers.
                    record_trip(
                        state=state,
                        breaker_id=BUILTIN_BUDGET_EXHAUSTED,
                        condition="provider_429",
                        action="fail",
                        detail={
                            "task_id": tid,
                            "next_available_epoch": _429_decision.next_available_epoch,
                        },
                        clock=self._clock,
                        run_log=run_log,
                    )
                    state.status = "failed"
                    self._runstate.save(state)
                    return SettleResult(signal="halt")
            else:
                # Normal reconcile: replace estimate with actuals or keep estimate
                # (T-j8YLGd: the task-block-scoped `_estimate` local from the old
                # inline loop no longer exists after the prepare/settle split --
                # charged_estimate[cycle_key(tid, ts.dispatch_cycle)] holds the
                # identical value charge_estimate() stored pre-dispatch; read it here
                # BEFORE reconcile() pops it so the log line below is unchanged.
                # R-1b: keyed by THIS cycle, not the bare task_id -- a T2/T3/self-heal/
                # quota redispatch's own charge lives under its OWN cycle's key.)
                _cycle_key = cycle_key(tid, ts.dispatch_cycle)
                _estimate = state.budget_counters.charged_estimate.get(_cycle_key, 0)
                if result.actuals_available:
                    _actual = self._sum_actuals(result)
                else:
                    # Fallback (FR-5): keep the estimate already stored in
                    # charged_estimate (charge_estimate stored it; reconcile will pop it)
                    _actual = state.budget_counters.charged_estimate.get(_cycle_key, 0)
                self._budget_manager.reconcile(
                    tid, _actual, state.budget_counters, cycle=ts.dispatch_cycle
                )
                task_log.info(
                    "Budget reconciled task %s: actual=%d estimate_delta=%d",
                    tid,
                    _actual,
                    _actual - _estimate,
                    extra={
                        "event": "budget.reconcile",
                        "task_id": tid,
                        "cycle": ts.dispatch_cycle,
                        "actual": _actual,
                        "consumed_tokens": state.budget_counters.consumed_tokens,
                    },
                )
                self._runstate.save(state)

        # ---- Consult Point B: task-failure self-healing (E-XyfjuZ, opt-in) ----
        # Design Decision D4: placed BEFORE ts.attempts/ts.ended_at are ever
        # touched for this attempt -- mirrors exactly where the quota-exhaustion
        # and provider-429 routes above already short-circuit on result.* fields,
        # before any TaskRunState settle-time mutation. A successfully healed
        # failure therefore never sets ts.status="failed", never reaches
        # evaluate_breakers, and never touches ts.started_at (the E-3JTmVu
        # single-writer guard is untouched -- this block only ever resets
        # ts.status, exactly like the quota-exhaustion requeue above). Scoped
        # strictly to result.status == "failed": timed_out/cancelled are a
        # deliberate MVP boundary, never healed.
        if self._self_heal_enabled and result.status == "failed":
            _heal_verdict = self._consult_task_failure_heal(tid, result, state, run_log)
            if _heal_verdict is not None and _heal_verdict.decision == "retry":
                # Reviewer-flagged Critical fix: this failed cycle's REAL actuals
                # (a failed attempt can still report actuals_available -- tokens/
                # cost were genuinely spent before the error) would otherwise be
                # silently discarded by the `continue` below, since this cycle
                # never reaches the settle-time cumulative block further down.
                # Accumulate now (mirrors _run_with_retries' own cum_* pattern one
                # level up, across heal cycles instead of within one call) --
                # `+=` (inside `_accumulate_actuals`) because a later heal cycle (if
                # max_heal_retries_per_task > 1) must not clobber an earlier one's
                # already-accumulated actuals. This is the same bug class E-9h3m7k
                # fixed for retries WITHIN one _run_with_retries call; self-heal's
                # cross-call redispatch needed the identical treatment, caught by a
                # late-gate reviewer pass -- and the T2/T3 conflict ladder needed it
                # again (R-1a, E-Wk9Tz3 T-Ac6Vd9), which is why this is now the one
                # shared `_accumulate_actuals` helper instead of a third near-copy.
                self._accumulate_actuals(ts, result)
                self._sleeper(_heal_verdict.wait_seconds)
                if self._cancel_fn():
                    run_log.info(
                        "Cancelled during self-heal wait",
                        extra={"event": "run.cancelled"},
                    )
                    state.status = "cancelled"
                    self._runstate.save(state)
                    return SettleResult(signal="halt")
                ts.status = "pending"
                run_log.info(
                    "Self-heal retry: re-running task %s after %.1f s",
                    tid,
                    _heal_verdict.wait_seconds,
                    extra={
                        "event": "monitor.heal_retry",
                        "task_id": tid,
                        "wait_seconds": _heal_verdict.wait_seconds,
                    },
                )
                self._runstate.save(state)
                return SettleResult(signal="requeue")
            # else: bound/cap exhausted (_heal_verdict is None) or an explicit
            # accept_failure verdict -- fall through to the existing, unmodified
            # settle/outcome handling below exactly as if self-heal were disabled.

        ts.attempts = result.attempts
        ts.ended_at = datetime.now(UTC).isoformat()
        # Record captured output path (FR-5); engine never reads the files.
        if result.output_artifact_path:
            ts.output_artifact_path = result.output_artifact_path
        # Cumulative actual usage across every attempt (E-9h3m7k FR-2) — result's
        # token/cost fields already sum all attempts (_run_with_retries). `_accumulate_
        # actuals` uses `+=` (not `=`) so a prior, healed-and-discarded cycle's
        # already-accumulated actuals (added above, in the Consult Point B retry
        # branch) are preserved rather than clobbered -- safe for every other caller
        # too: this line runs at most once per dispatch outside of self-heal, and
        # `prepare_resume` hands any re-dispatched task a fresh `TaskRunState()`
        # (cumulative_* defaulted to 0), so `+=` is byte-identical to `=` whenever
        # nothing was accumulated first. This SAME call is also what makes R-1a hold
        # for the T2/T3 conflict-ladder requeue below (`conflict_resolver`/
        # `conflict_rerun`): it runs unconditionally, before that switch, so no
        # second accumulation call is needed there (E-Wk9Tz3 T-Ac6Vd9).
        self._accumulate_actuals(ts, result)

        if result.status == "succeeded":
            # Verify declared outputs were actually produced. E-Wk9Tz3 C-1 (review fix):
            # for an ISOLATED task, the authoritative outputs verdict is the WORKER's own
            # R-2 gate (`_run_and_integrate`, evaluated through the task's
            # IsolatedArtifactView, IN THE WORKTREE, before landing) -- not this
            # self._store check, which resolves against the shared, un-synced checkout.
            # A declared output living inside the isolated repo is genuinely on disk (and,
            # once landed, on the integration ref) but is NEVER visible to self._store
            # until a barrier/run-end sync happens -- which is always AFTER this point, so
            # the un-redirected check would wrongly report "missing" for exactly the
            # workflow shape (an isolated task whose own source-file edits ARE its
            # declared outputs) the epic exists to support. `outcome.missing_outputs` is
            # the worker's verdict when its own gate failed (handled below, in the
            # integration-settle block's R-23 branch, with the precise per-path reason);
            # here, for an isolated task, `missing_outputs` is unconditionally treated as
            # empty so control falls into the "present" branch below -- the integration-
            # settle block is what makes the FINAL, authoritative call.
            if outcome.task_iso is not None:
                missing_outputs: list[str] = []
            else:
                missing_outputs = [o for o in task.outputs if not self._store.exists(o)]
            if missing_outputs:
                task_log.error(
                    "Task succeeded but declared outputs missing: %s",
                    missing_outputs,
                    extra={"event": "task.fail", "reason": "missing_outputs"},
                )
                ts.status = "failed"
                ts.outputs_present = False
            else:
                ts.status = "succeeded"
                ts.outputs_present = True
                # Read output manifest if declared; failure fails the task
                if task.output_manifest:
                    try:
                        ts.dynamic_outputs = read_manifest(self._store, task.output_manifest)
                        task_log.info(
                            "Loaded %d dynamic outputs from manifest",
                            len(ts.dynamic_outputs),
                            extra={"event": "task.manifest_loaded"},
                        )
                    except ValueError as exc:
                        task_log.error(
                            "output_manifest read failed: %s",
                            exc,
                            extra={"event": "task.fail", "reason": "manifest_error"},
                        )
                        ts.status = "failed"
                        ts.outputs_present = False
        else:
            ts.status = result.status

        # ---- E-Wk9Tz3 task isolation: integration settle (main thread, sole writer,
        # ADR-0007 D3) ----
        # `ti` exists only for a task this run actually dispatched into a worktree
        # (`_record_task_integration_pending`, dispatch prep) -- a non-isolated task's
        # `state.task_integration` has no entry at all, so this whole block is a no-op for
        # every pre-epic workflow (NFR-2). No accumulation duplication needed here (unlike
        # Consult Point B above): the unconditional `ts.cumulative_*` accumulation a few
        # lines up ALREADY ran for this cycle before this block, so R-1a's "accumulate
        # before requeuing" requirement already holds by construction of this block's
        # placement, not by a second copy of that logic.
        ti = state.task_integration.get(tid)
        if ti is not None and ti.isolation != ISOLATION_NONE:
            if outcome.rerun_base:
                # E-Wk9Tz3 T-Lr6Ka3 (AC-8): the worker reset this task's worktree(s) to a
                # fresh integration head before redispatching (mode == "rerun") -- applied
                # regardless of this cycle's eventual outcome (integrated/conflict_*/
                # failed), since the reset itself already happened either way.
                ti.base_commits.update(outcome.rerun_base)
            if outcome.integration is None:
                # R-23: covers BOTH a plain execution failure (retries exhausted -- never
                # reached the worker's integration switch at all) AND R-2's worker-side
                # missing-outputs gate short-circuit (`outcome.missing_outputs`) -- neither
                # ever calls `integrate()`, so `release()` is otherwise unreachable here and
                # even `keep_worktrees: "never"` would leak this task's worktree/branch
                # (arguably the most common failure path). `TaskIntegrationState.status`
                # has no "cancelled"/"timed_out" value, so `ti.status` is unconditionally
                # "failed" here -- but `ts.status` (TaskRunState.status, which DOES support
                # them) must preserve `result.status` verbatim for a genuine
                # cancel/timeout, mirroring the pre-existing non-isolated `else: ts.status
                # = result.status` branch above; only the missing-outputs case (execution
                # itself reported "succeeded") is a genuine NEW failure this ticket
                # introduces.
                ti.status = "failed"
                ti.last_error = (
                    "missing_outputs:" + ",".join(outcome.missing_outputs)
                    if outcome.missing_outputs
                    else (result.error or "execution_failed")
                )
                ts.status = "failed" if result.status == "succeeded" else result.status
                ts.outputs_present = False
                if ctx.worktree_manager is not None:
                    ctx.worktree_manager.release(tid, "failed", workflow.integration.keep_worktrees)
                self._warn_if_retention_high(state, workflow, ctx)
            else:
                integ = outcome.integration
                ti.attempts += 1
                ti.tier_reached = integ.tier_reached
                ti.conflicted_paths = list(integ.conflicted_paths)
                ti.verify_status = integ.verify_status
                # E-Wk9Tz3 T-Lr6Ka3 (AC-8): durable per-repo squash shas, needed by a T3
                # rerun's `export_previous_patch` (the superseded squash is the diff's
                # `head`). Harmless no-op for a non-conflict settle (`integ.squash` is
                # `{}` for a clean "integrated"/"empty" landing).
                ti.squash_commits.update(integ.squash)
                if integ.status in ("integrated", "empty"):
                    state.integration.heads.update(integ.heads)
                    ti.status = "integrated"
                    ti.mode = "normal"
                    copy_ok = True
                    if integ.untracked_outputs and outcome.task_iso is not None:
                        copy_ok = self._copy_untracked_outputs(
                            integ.untracked_outputs,
                            outcome.task_iso,
                            workflow.integration.untracked_outputs,
                            run_log,
                        )
                    if ctx.worktree_manager is not None:
                        ctx.worktree_manager.release(
                            tid, "integrated", workflow.integration.keep_worktrees
                        )
                    if not copy_ok:
                        ts.status = "failed"
                    else:
                        run_log.info(
                            "integration.merged",
                            extra={
                                "event": "integration.merged",
                                "task_id": tid,
                                "heads": integ.heads,
                            },
                        )
                elif integ.status == "conflict_resolver":
                    ti.status = "conflict_resolver"
                    ti.mode = "resolve"
                    ti.resolver_attempts += 1
                    ts.status = "pending"
                    run_log.info(
                        "integration.resolver_dispatched",
                        extra={"event": "integration.resolver_dispatched", "task_id": tid},
                    )
                    self._runstate.save(state)
                    return SettleResult(signal="requeue")
                elif integ.status == "conflict_rerun":
                    ti.status = "conflict_rerun"
                    ti.mode = "rerun"
                    ti.reruns += 1
                    ts.status = "pending"
                    run_log.info(
                        "integration.rerun_dispatched",
                        extra={"event": "integration.rerun_dispatched", "task_id": tid},
                    )
                    self._runstate.save(state)
                    return SettleResult(signal="requeue")
                else:  # "failed"
                    ti.status = "failed"
                    ti.last_error = integ.reason
                    ts.status = "failed"
                    run_log.error(
                        "integration.failed",
                        extra={
                            "event": "integration.failed",
                            "task_id": tid,
                            "reason": integ.reason,
                            # AC-10: names the conflicted paths, worktree path(s) and
                            # branch(es) directly on the event (T-Lr6Ka3), not only inside
                            # the `reason` string -- T-Cx4Jf1 part B observability hook.
                            "conflicted_paths": ti.conflicted_paths,
                            "worktrees": dict(ti.repos),
                            "branches": dict(ti.branches),
                        },
                    )
                    self._warn_if_retention_high(state, workflow, ctx)
                    # T4 (HLD §11 M5): retains the worktree AND branch regardless of
                    # keep_worktrees -- release() is deliberately NOT called here, so the
                    # operator can `cd <worktree>; git rebase --continue`.

        if ts.status == "succeeded":
            task_log.info(
                "Task succeeded",
                extra={
                    "event": "task.end",
                    "status": "succeeded",
                    "exit_code": result.exit_code,
                },
            )
            ctx.done.add(tid)
            ctx.quota_exhausted_since = None  # successful task resets the quota-wait timer

            # ---- Router-success hook (T-m2h5t7, LLD §5.2) ----
            # Runs BEFORE the circuit-breaker evaluation below so
            # route_decisions/not_taken are settled before any breaker
            # inspects RunState.
            router = self._router_for_task(workflow, tid)
            if router is not None:
                if self._on_router_success(router, state, ctx.cones, run_log) == "failed":
                    return SettleResult(signal="halt")
        else:
            task_log.warning(
                "Task ended with status %s",
                ts.status,
                extra={
                    "event": "task.end",
                    "status": ts.status,
                    "exit_code": result.exit_code,
                },
            )

        self._runstate.save(state)

        # ---- Circuit-breaker evaluation (T-x8v4d3, LLD §6.1) ----
        # Task boundary: after outcome handling + save, before the next dispatch.
        # Runs regardless of ts.status (a failure is itself a boundary a breaker may
        # react to, e.g. the future task_failures/consecutive_failures conditions).
        # No-op today: workflow.circuit_breakers defaults to [] and no built-ins are
        # wired yet (T-r3j9b6), so the loop body never executes for existing workflows.
        #
        # Consult Point A (E-XyfjuZ, epic doc Design Decisions D1-D3):
        # evaluate_breakers() itself is UNCHANGED here (D2) -- its byte-identical no-op
        # guarantee (no clock() call when there are no specs) and every existing test
        # that calls it directly are preserved. "Which specs newly tripped this
        # boundary" is instead derived AT THE CALL SITE by diffing state.tripped_breakers
        # ids before/after this one call. Built-in re-framed stops (budget/quota/429)
        # can never appear in that diff: each already breaks the run loop via its own
        # record_trip() call earlier in this same iteration, well before
        # evaluate_breakers is ever reached (D1) -- so they are unconditionally hard by
        # construction, never consultable, regardless of any workflow.circuit_breakers
        # declaration.
        _before_tripped_ids = {tb.id for tb in state.tripped_breakers}
        breaker_action = evaluate_breakers(workflow, state, self._clock, self._store, run_log)
        if breaker_action is not None:
            _newly_tripped_ids = {
                tb.id for tb in state.tripped_breakers if tb.id not in _before_tripped_ids
            }
            # Filtering workflow.circuit_breakers (rather than iterating the id set
            # directly) preserves DECLARED order (D3's "consult in declared order").
            _newly_tripped_specs = [
                b for b in workflow.circuit_breakers if b.id in _newly_tripped_ids
            ]
            _consultable = (
                len(_newly_tripped_specs) == len(_newly_tripped_ids)
                and bool(_newly_tripped_specs)
                and all(b.mode == "recommend" for b in _newly_tripped_specs)
            )
            # No self._runstate.save() happens between evaluate_breakers() recording
            # the trip(s) above and the consult resolving below (early-gate architect
            # finding) -- a half-consulted state must never hit disk.
            _consult_outcome = (
                self._consult_breaker_trips(_newly_tripped_specs, state, run_log)
                if _consultable
                else "halt"
            )
            if _consult_outcome == "halt":
                # fail/stop/pause all land on resumable status="failed" for MVP
                # (ADR-RC-004); the distinguishing action is preserved in
                # tripped_breakers[].action.
                state.status = "failed"
                self._runstate.save(state)
                return SettleResult(signal="halt")
            # _consult_outcome == "extend": _consult_breaker_trips already applied
            # apply_breaker_extension (bumping breaker_overrides + un-latching the
            # tripped record(s)) for every newly-tripped breaker -- fall through to the
            # rest of the loop body exactly as if breaker_action had been None (today's
            # no-trip path).
            self._runstate.save(state)

        if ts.status not in ("succeeded", "skipped"):
            state.status = "failed"
            return SettleResult(signal="halt")

        # ---- Dynamic expansion hooks (Area 2) ----

        # 2a: emit_tasks — read manifest, inject new tasks, rebuild DAG + order
        if task.emit_tasks and ts.status == "succeeded":
            try:
                new_specs = read_task_manifest(self._store, task.task_manifest_path)  # type: ignore[arg-type]
            except ValueError as exc:
                task_log.error(
                    "task_manifest_path read failed: %s",
                    exc,
                    extra={"event": "task.fail", "reason": "manifest_error"},
                )
                ts.status = "failed"
                ts.outputs_present = False
                state.status = "failed"
                self._runstate.save(state)
                return SettleResult(signal="halt")
            # E-Wk9Tz3: an emit_tasks-declared `isolation: "worktree"` (independent of
            # `workflow.defaults.isolation`) only exists once the manifest is read -- the
            # static `ao validate` pass at spec-load time can never see it. Re-validate the
            # would-be-expanded task list at injection time (spec.py's own HOOK POINT
            # docstring) so a misconfiguration fails fast here rather than at first isolated
            # dispatch mid-run.
            try:
                for warning in validate_isolation(workflow, [*workflow.tasks, *new_specs]):
                    task_log.warning(
                        "isolation validation: %s",
                        warning,
                        extra={"event": "isolation.validation_warning"},
                    )
            except SpecValidationError as exc:
                task_log.error(
                    "Injected tasks fail isolation validation: %s",
                    exc,
                    extra={"event": "task.fail", "reason": "isolation_validation_error"},
                )
                ts.status = "failed"
                state.status = "failed"
                self._runstate.save(state)
                return SettleResult(signal="halt")
            try:
                self._inject(new_specs, workflow, state, origin="injected", route=ts.route)
            except InjectionError as exc:
                task_log.error(
                    "Task injection failed: %s",
                    exc,
                    extra={"event": "task.fail", "reason": "injection_error"},
                )
                ts.status = "failed"
                state.status = "failed"
                self._runstate.save(state)
                return SettleResult(signal="halt")
            graph = build_dag(workflow)
            order, cursor = self._recompute_order(graph, ctx.done)
            task_log.info(
                "Injected %d tasks; order recomputed (%d remaining)",
                len(new_specs),
                len(order) - cursor,
                extra={"event": "task.injected"},
            )
            self._runstate.save(state)
            return SettleResult(signal="reshaped", graph=graph, order=order)

        # 2b/2c: loop gate — check if a completed task is a gate task
        loop = self._loop_for_gate(workflow, tid)
        if loop is not None and ts.status == "succeeded":
            cur_iter = state.loop_iterations.get(loop.id, 1)
            if cur_iter < loop.max_iterations:
                # Determine gate path for the current iteration
                gate_path = self._gate_path_for_iter(loop, cur_iter)
                try:
                    should_cont = read_gate(self._store, gate_path, loop.gate_field)
                except GateError as exc:
                    task_log.error(
                        "Gate read failed: %s",
                        exc,
                        extra={"event": "task.fail", "reason": "gate_error"},
                    )
                    ts.status = "failed"
                    state.status = "failed"
                    self._runstate.save(state)
                    return SettleResult(signal="halt")
                if should_cont:
                    next_iter = cur_iter + 1
                    clones = self._clone_body(loop, next_iter, workflow)
                    try:
                        self._inject(clones, workflow, state, origin="loop", route=ts.route)
                    except InjectionError as exc:
                        task_log.error(
                            "Loop clone injection failed: %s",
                            exc,
                            extra={"event": "task.fail", "reason": "injection_error"},
                        )
                        ts.status = "failed"
                        state.status = "failed"
                        self._runstate.save(state)
                        return SettleResult(signal="halt")
                    state.loop_iterations[loop.id] = next_iter
                    # T-j8YLGd: order's cursor is dead in the new ready-set model (only
                    # `order` is handed back to the caller) -- call `topological_order()`
                    # directly rather than `_recompute_order` to avoid computing a value
                    # nothing consumes; identical `order` result either way.
                    graph = build_dag(workflow)
                    order = graph.topological_order()
                    run_log.info(
                        "Loop %s starting iteration %d",
                        loop.id,
                        next_iter,
                        extra={
                            "event": "loop.iterate",
                            "loop_id": loop.id,
                            "iteration": next_iter,
                        },
                    )
                    self._runstate.save(state)
                    return SettleResult(signal="reshaped", graph=graph, order=order)
            # else: max_iterations reached or gate says stop — loop ends, proceed

        return SettleResult(signal="settled")

    def _drain_remaining(
        self,
        in_flight: dict[Future[WorkerOutcome], str],
        workflow: WorkflowSpec,
        state: RunState,
        ctx: _RunContext,
    ) -> None:
        """Wait out every still-running worker after a HALT/cancel decision.

        Settles each drained result on the main thread (ADR-0007 D7: drain, don't
        kill) so its artifacts + ``RunState`` mutations are persisted exactly as
        they would be on a normal wave -- the run stays resumable. Any further
        signal a drained settle returns (HALT/REQUEUE/RESHAPED) is intentionally
        NOT acted on: the run is already stopping, so re-deriving a fresh
        graph/order or flipping a task back to "pending" would be immediately
        discarded work. ``pool.shutdown(wait=True)`` (the ``with`` block in
        ``run()``) is the structural backstop against a thread leak (NFR-4)
        regardless of what happens in here -- draining exists so completed work
        is not silently thrown away, not to guarantee the shutdown itself.
        """
        for fut in as_completed(list(in_flight)):
            tid = in_flight.pop(fut)
            outcome = fut.result()
            self._settle_completed_task(tid, outcome, workflow, state, ctx)

    # -------------------------------------------------------------------------
    # Wave/barrier scheduler helpers (T-j8YLGd, ADR-0007 D3/D4/D6)
    # -------------------------------------------------------------------------

    def _resolve_general_instructions(
        self, workflow: WorkflowSpec, store: ArtifactStore | None = None
    ) -> list[str]:
        """Resolve the general-instruction paths that apply to every task (E-Ui7Kq2 FR-GI1).

        Merges the injected workspace-scoped list (``general_instructions=`` ctor arg, which
        the CLI fills from config file + env + ``--general-instruction``) with the workflow
        spec's own ``general_instructions``, de-duplicating by RESOLVED path so a file named
        in two layers is handed to the agent once.

        Merging the workflow layer here — rather than trusting the caller to have done it —
        is what makes the guarantee hold for direct library/API users of ``Orchestrator``
        too, not just for runs launched through ``ao``. The CLI passes an already-merged
        list that includes the workflow layer; de-duplication makes that harmless.

        Every path goes through ``ArtifactStore.resolve`` for the same workspace-root path
        guard as ``task.instruction``, so a traversal path in a config file cannot smuggle
        an out-of-workspace file into a prompt. A path that fails the guard is dropped with
        a warning rather than killing the run: general instructions are additive context,
        and one bad entry in a workspace config should not fail every task in every
        workflow. ``ao validate`` is the layer that reports such a path up front.

        *store* (E-Wk9Tz3, R-19/AC-15): defaults to ``self._store``; an isolated dispatch
        passes the task's `IsolatedArtifactView` so a general instruction that happens to
        live inside an isolated repo resolves into the worktree, same as every other path
        category.
        """
        st = store if store is not None else self._store
        resolved: list[str] = []
        seen: set[str] = set()
        for raw in (*self._general_instructions, *workflow.general_instructions):
            try:
                path = st.resolve(raw)
            except ArtifactPathError as exc:
                logger.warning(
                    "general_instruction.rejected",
                    extra={
                        "event": "general_instruction.rejected",
                        "path": raw,
                        "error": str(exc),
                    },
                )
                continue
            if path not in seen:
                seen.add(path)
                resolved.append(path)
        return resolved

    def _is_barrier(
        self, task: TaskSpec, workflow: WorkflowSpec, state: RunState | None = None
    ) -> bool:
        """Return True when *task* must run alone (ADR-0007 D4): nothing else in
        flight when it starts, and nothing new launched until it fully settles.

        A task is a barrier iff it reshapes the DAG (``emit_tasks``) or mutates
        route/loop bookkeeping the ready-set and budget gate read: a loop-gate
        task id (including ``__iter`` clones, via ``_loop_for_gate`` -- reused
        rather than hand-rolling id matching) or a router task id (via
        ``_router_for_task``).

        E-Wk9Tz3 (ADR-0013 D4/D5): additionally True for a NON-isolated task while
        integration is active -- a shared-checkout task must never overlap an isolated
        one, since the checkout sync that must precede it (FR-13) touches the same working
        tree every isolated worktree's own repo is cloned from. *state* is optional
        (defaults to None -- "no integration context", the byte-identical NFR-2 case) so
        every pre-epic direct unit-level call (``_is_barrier(task, workflow)``, 2-arg) keeps
        working unchanged; `run()`'s own call site always passes the live *state*.
        """
        if task.emit_tasks:
            return True
        if self._loop_for_gate(workflow, task.id) is not None:
            return True
        if self._router_for_task(workflow, task.id) is not None:
            return True
        if (
            state is not None
            and state.integration.active
            and resolve_task_isolation(task, workflow) == ISOLATION_NONE
        ):
            return True
        return False

    def _predecessors(self, graph: Graph) -> dict[str, set[str]]:
        """Invert *graph*'s adjacency map (successors) into a predecessor map.

        Computed once per DAG build (initial + every RESHAPED reshape), reused
        by every ``_ready_ids`` call in between.
        """
        preds: dict[str, set[str]] = {n: set() for n in graph.adjacency()}
        for node, succs in graph.adjacency().items():
            for s in succs:
                preds[s].add(node)
        return preds

    def _ready_ids(
        self,
        order: list[str],
        preds: dict[str, set[str]],
        state: RunState,
        done: set[str],
        in_flight_ids: set[str],
    ) -> list[str]:
        """Return the wave's candidate ids: tasks in *order* (deterministic
        sorted-Kahn tie-break, ADR-0007 D6) whose every predecessor has settled
        (succeeded/skipped/not_taken), that are not already done or in flight,
        and whose own status is not already succeeded/skipped/not_taken/running.

        A ``not_taken`` predecessor counts as settled (join resolution --
        propagate vs. skip -- is decided downstream in
        ``_prepare_and_maybe_dispatch`` via ``_apply_join``, unchanged from
        today).

        E-Wk9Tz3 (ADR-0013 D5, FR-9): a predecessor's settledness is now
        `_settled_for_dependents`, not a plain status-in-set test -- a predecessor whose
        integration status is not yet ``integrated``/``none`` never counts as settled here,
        even once its own ``TaskRunState.status == "succeeded"``, so a dependent's worktree
        is never based on a head still missing that predecessor's work.
        """
        excluded = ("succeeded", "skipped", "not_taken", "running")
        ready: list[str] = []
        for tid in order:
            if tid in done or tid in in_flight_ids:
                continue
            ts = state.tasks.get(tid)
            if ts is not None and ts.status in excluded:
                continue
            if all(self._settled_for_dependents(p, state) for p in preds.get(tid, set())):
                ready.append(tid)
        return ready

    def _settled_for_dependents(self, tid: str, state: RunState) -> bool:
        """HLD §11 M5 / FR-9 (ADR-0013 D5): a predecessor counts as settled for a
        dependent's readiness ONLY when its integration status is ``integrated`` (or it was
        never isolated -- no `task_integration` entry, or ``isolation == "none"``).
        ``not_taken``/``skipped`` predecessors are settled unconditionally (never isolated
        by construction -- a not_taken/skipped task never dispatches). A task still
        ``pending``/``running``/``failed`` is never settled regardless of integration.
        """
        ts = state.tasks.get(tid)
        if ts is None:
            return False
        if ts.status in ("not_taken", "skipped"):
            return True
        if ts.status != "succeeded":
            return False
        ti = state.task_integration.get(tid)
        return ti is None or ti.isolation == ISOLATION_NONE or ti.status in ("integrated", "none")

    # -------------------------------------------------------------------------
    # Task isolation helpers (E-Wk9Tz3 T-En8Hd4, HLD §11 M5)
    # -------------------------------------------------------------------------

    def _integration_allows_skip(self, tid: str, state: RunState) -> bool:
        """R-3/D5: the integration gate `RunStateStore.should_skip` needs, applied at the
        CALL SITE for BOTH of its branches -- `runstate.py` is off-limits to this ticket, so
        the base store's `should_skip` stays byte-identical (NFR-2) and this extra AND
        condition is layered on top instead. A task never isolated (no `task_integration`
        entry, or ``isolation == "none"``) is unaffected; an isolated task is only truly
        "done" (skippable) once its integration status is ``integrated``.
        """
        ti = state.task_integration.get(tid)
        if ti is None or ti.isolation == ISOLATION_NONE:
            return True
        return ti.status == "integrated"

    def _require_local_fs_store(self) -> LocalFsArtifactStore:
        """Isolated dispatch needs the CONCRETE `LocalFsArtifactStore` -- its read-only
        additions (`T-Wk3Nv6`) are not on the abstract `ArtifactStore` boundary, and
        `IsolatedArtifactView`'s constructor is typed against the concrete class for
        exactly that reason. Every production caller (`cli.py`) constructs `Orchestrator`
        with exactly this concrete type; a workflow declaring ``isolation: worktree``
        against any other `ArtifactStore` implementation is not a supported configuration.
        """
        if not isinstance(self._store, LocalFsArtifactStore):
            raise TypeError(
                "isolation: worktree requires a LocalFsArtifactStore artifact_store; got "
                f"{type(self._store).__name__}"
            )
        return self._store

    def _record_task_integration_pending(
        self, state: RunState, tid: str, task_iso: TaskIsolation
    ) -> None:
        """HLD §11 M5: record this dispatch's worktree roots/branches/base commits into
        `state.task_integration[tid]` -- ``status`` starts ``"pending"`` (transitions to
        ``"integrated"``/``"conflict_resolver"``/``"conflict_rerun"``/``"failed"`` at settle).
        """
        ti = state.task_integration.setdefault(tid, TaskIntegrationState())
        ti.isolation = ISOLATION_WORKTREE
        ti.status = "pending"
        ti.repos = {r.key: r.worktree_root for r in task_iso.repos}
        ti.branches = {r.key: r.branch for r in task_iso.repos}
        ti.base_commits = {r.key: r.base for r in task_iso.repos}

    def _build_task_env(self, task_iso: TaskIsolation, integration_branch: str) -> dict[str, str]:
        """AC-8: `TaskContext.env` for an isolated task -- `AO_ISOLATION`/`AO_TASK_BRANCH`/
        `AO_INTEGRATION_BRANCH` plus one `AO_WORKTREE_ROOT_<REPO_ID>` per reposet member,
        plus any `isolation.env` per-repo overlay (`self._isolation_env`, keyed by repo_id
        -- see the Orchestrator ctor's `isolation_env` param docstring: the CLI-level
        ``.ao/config.yaml: isolation.env`` plumbing that would populate this in production
        is a not-yet-built M9 ticket; the seam exists and is tested here regardless).

        All of a task's repos share the identical branch name (`paths.task_branch` is
        deterministic, independent of repo), so `AO_TASK_BRANCH` is a single value.
        """
        env: dict[str, str] = {
            "AO_ISOLATION": ISOLATION_WORKTREE,
            "AO_TASK_BRANCH": task_iso.repos[0].branch,
            "AO_INTEGRATION_BRANCH": integration_branch,
        }
        for repo in task_iso.repos:
            for member in repo.members:
                var = "AO_WORKTREE_ROOT_" + _env_safe(member.repo_id)
                env[var] = (
                    os.path.normpath(os.path.join(repo.worktree_root, member.rel))
                    if member.rel
                    else repo.worktree_root
                )
                env.update(self._isolation_env.get(member.repo_id, {}))
        return env

    def _build_integrator(self, workflow: WorkflowSpec, state: RunState) -> Integrator:
        if self._injected_integrator is not None:
            return self._injected_integrator
        return Integrator(
            workflow.integration,
            get_run_logger(state.run_id),
            self._clock,
            self._resolver_hook,
            self._escalation_hook,
        )

    def _integrate_task(
        self,
        integrator: Integrator,
        task_iso: TaskIsolation,
        run_integration: RunIntegrationSnapshot,
        task_integration: TaskIntegrationState,
        *,
        agent_id: str,
        attempt: int,
        resume: bool = False,
    ) -> IntegrationResult:
        """THE single call site into `isolation.integrator.Integrator` (`T-Ib5Qy9`, in
        review). Every `integrate()`/`resume_integration()` invocation in this file goes
        through here so a review-driven signature change on that side is a one-place fix
        (TASK.md's Concurrency boundary). ``resume=True`` is unused by this ticket (the
        real "resolve"/"rerun" redispatch mechanics are `T-Lr6Ka3`'s -- see
        `_run_and_integrate`'s own docstring) but wired ready for it.
        """
        if resume:
            return integrator.resume_integration(
                task_iso, run_integration, task_integration, attempt
            )
        return integrator.integrate(
            task_iso, run_integration, task_integration, attempt, agent_id=agent_id
        )

    def _activate_integration(
        self, state: RunState, workflow: WorkflowSpec, ctx: _RunContext
    ) -> bool:
        """HLD §11 M5 / AC-4-5: lazily activate isolation at the FIRST isolated dispatch
        (never at run start -- AC-5, load-bearing for a workflow whose first task creates
        and checks out a branch before any isolated task runs: activation reads each repo's
        CURRENT HEAD, so it inherits whatever branch that first task already established).
        Runs at most once per run: a degrade latches ``ctx.integration_degraded`` so a later
        isolated dispatch never re-probes or re-warns (its caller just sees
        ``state.integration.active`` still False and downgrades to ``isolation: none``).

        E-Wk9Tz3 T-Wl2Bq7 (HLD §12.3, ADR-0013 D8): claims the per-workspace
        `WorkspaceRunLock` BEFORE any ref is created, per `workflow.integration.
        workspace_lock` (``"require"`` default): a live holder degrades this run to
        `isolation: none` (or, under `isolation.strict`, fails it -- `_degrade` already
        implements that branch); ``"skip_sync"`` isolates regardless of the claim outcome
        (landing is already safe by construction -- only the checkout sync needs the lock,
        and `_sync_checkout` unconditionally skips it for this policy); ``"off"`` never
        calls `acquire()` at all ("no lock, no protection") and only warns.
        """
        run_log = ctx.run_log

        def _degrade(reason: str, **extra_fields: object) -> bool:
            state.integration.degraded_reason = reason
            ctx.integration_degraded = True
            log_extra = {"event": "integration.degraded", "reason": reason, **extra_fields}
            if self._isolation_strict:
                state.status = "failed"
                run_log.error("integration.degraded", extra={**log_extra, "strict": True})
            else:
                run_log.warning("integration.degraded", extra=log_extra)
            return False

        git_version = GitRepo.version()
        if git_version is None or git_version < GIT_MIN_VERSION:
            return _degrade("git_unavailable_or_old")

        repos, _skipped = group_repos(ctx.repo_paths)
        if not repos:
            return _degrade("no_git_repos")

        workspace_root = _workspace_root_from_run_dir(ctx.run_dir)
        resolved_state_dir = os.path.normpath(str(isolation_paths.state_dir()))
        for repo in repos:
            toplevel_norm = os.path.normpath(repo.toplevel)
            if resolved_state_dir == toplevel_norm or resolved_state_dir.startswith(
                toplevel_norm + os.sep
            ):
                return _degrade("unsafe_state_dir")

        # E-Wk9Tz3 T-Wl2Bq7 review W-3: every OTHER degrade check in this function runs
        # BEFORE the lock claim below (`git_unavailable_or_old`, `no_git_repos`,
        # `unsafe_state_dir`) -- the unborn-HEAD probe belongs in that same group, not
        # after the claim, so a workflow that will degrade anyway never needlessly holds
        # the workspace lock (denying a concurrent run's REAL isolation attempt) for the
        # rest of this run over a repo that was never going to activate isolation at all.
        git_repos: dict[str, GitRepo] = {}
        heads: dict[str, str] = {}
        base_heads: dict[str, str] = {}
        for repo in repos:
            git = GitRepo(repo.toplevel)
            git_repos[repo.key] = git
            head = git.rev_parse("HEAD")
            if head is None:
                return _degrade("unborn_branch")
            # R-12 run-start pre-flight: compute the dirty set once, up front, so the
            # collision risk a barrier-time sync might hit is visible before the first
            # barrier rather than at it.
            dirty_entries = git.status_porcelain(repo.toplevel, untracked=False)
            if dirty_entries:
                run_log.warning(
                    "worktree.checkout_dirty",
                    extra={
                        "event": "worktree.checkout_dirty",
                        "repo": repo.key,
                        "count": len(dirty_entries),
                    },
                )
            heads[repo.key] = head
            base_heads[repo.key] = head

        # R-4: claim the workspace lock only once every other degrade check has passed --
        # still before any ref is created (AC-4).
        policy = workflow.integration.workspace_lock
        if policy == _WORKSPACE_LOCK_OFF:
            run_log.warning(
                "integration.workspace_lock_off",
                extra={
                    "event": "integration.workspace_lock_off",
                    "risk": "no protection against a concurrent run's checkout sync",
                },
            )
        else:
            workspace_lock = WorkspaceRunLock(workspace_root, state.run_id, clock=self._clock)
            claim = workspace_lock.acquire()
            if claim.granted:
                ctx.workspace_lock = workspace_lock
                state.integration.workspace_lock_held = True
                if claim.reclaimed:
                    run_log.warning(
                        "integration.runlock_reclaimed",
                        extra={
                            "event": "integration.runlock_reclaimed",
                            "prior_run_id": claim.holder_run_id,
                            "prior_pid": claim.holder_pid,
                            "stale_reason": claim.stale_reason,
                        },
                    )
                else:
                    run_log.info(
                        "integration.runlock_acquired",
                        extra={"event": "integration.runlock_acquired"},
                    )
            elif policy == _WORKSPACE_LOCK_REQUIRE:
                return _degrade(
                    f"workspace_locked:{claim.holder_run_id}",
                    holder_run_id=claim.holder_run_id,
                    holder_pid=claim.holder_pid,
                )
            else:  # skip_sync, denied -- proceed unprotected; sync is disabled regardless
                run_log.warning(
                    "integration.runlock_denied",
                    extra={
                        "event": "integration.runlock_denied",
                        "holder_run_id": claim.holder_run_id,
                        "holder_pid": claim.holder_pid,
                        "policy": policy,
                    },
                )

        branch = isolation_paths.integration_branch(state.run_id)
        for repo in repos:
            git_repos[repo.key].create_ref(f"refs/heads/{branch}", heads[repo.key])
            _append_info_exclude(repo.common_dir)

        state.integration.active = True
        state.integration.branch = branch
        state.integration.repos = {r.key: r.common_dir for r in repos}
        state.integration.heads = heads
        state.integration.base_heads = base_heads
        ctx.integration_degraded = True  # latch: activated, never probe again this run
        run_log.info(
            "integration.activated",
            extra={"event": "integration.activated", "branch": branch, "repos": list(heads)},
        )

        ctx.worktree_manager = self._injected_worktree_manager or WorktreeManager(
            workspace_root, state.run_id, repos, state.integration.heads
        )
        ctx.integrator = self._build_integrator(workflow, state)
        return True

    def _reconcile_integration_on_resume(
        self, state: RunState, workflow: WorkflowSpec, ctx: _RunContext
    ) -> None:
        """HLD §12.1: `state.integration.active` can only be True here at run START when
        *run_state* was loaded from a previous (crashed/interrupted) run -- reconstruct the
        `WorktreeManager`/`Integrator` from the ALREADY-activated integration state (never
        re-probe/re-activate) and reap any worktree/ref whose task id is no longer known.

        E-Wk9Tz3 T-Wl2Bq7: resume takeover of the workspace lock. The crashed process's own
        `WorkspaceRunLock` (if it held one -- `state.integration.workspace_lock_held`,
        preserved verbatim across `prepare_resume`) is gone along with its process; its
        `flock` was released by the OS when that process died, so a fresh `acquire()` here
        either succeeds trivially (the common case -- logged as a reclaim) or, if some OTHER
        live run has since taken the workspace, is denied. A denied reclaim does not fail
        the resume: `state.integration.workspace_lock_held` is set False and
        `_sync_checkout` (gated on that flag for ``"require"``) simply stops syncing for the
        rest of this run rather than risk an unprotected checkout mutation.
        """
        repos, _skipped = group_repos(ctx.repo_paths)
        workspace_root = _workspace_root_from_run_dir(ctx.run_dir)
        ctx.worktree_manager = self._injected_worktree_manager or WorktreeManager(
            workspace_root, state.run_id, repos, dict(state.integration.heads)
        )
        ctx.integrator = self._build_integrator(workflow, state)
        ctx.integration_degraded = True  # already active; never (re-)probe this run

        policy = workflow.integration.workspace_lock
        if policy != _WORKSPACE_LOCK_OFF and state.integration.workspace_lock_held:
            workspace_lock = WorkspaceRunLock(workspace_root, state.run_id, clock=self._clock)
            claim = workspace_lock.acquire()
            if claim.granted:
                ctx.workspace_lock = workspace_lock
                if claim.reclaimed:
                    ctx.run_log.warning(
                        "integration.runlock_reclaimed",
                        extra={
                            "event": "integration.runlock_reclaimed",
                            "prior_run_id": claim.holder_run_id,
                            "prior_pid": claim.holder_pid,
                            "stale_reason": claim.stale_reason,
                            "resume": True,
                        },
                    )
            else:
                state.integration.workspace_lock_held = False
                ctx.run_log.warning(
                    "integration.runlock_denied",
                    extra={
                        "event": "integration.runlock_denied",
                        "holder_run_id": claim.holder_run_id,
                        "holder_pid": claim.holder_pid,
                        "policy": policy,
                        "resume": True,
                    },
                )
        report = ctx.worktree_manager.reconcile({t.id for t in workflow.tasks})
        ctx.run_log.info(
            "worktree.reconciled",
            extra={
                "event": "worktree.reconciled",
                "removed_worktrees": len(report.removed_worktrees),
                "deleted_refs": len(report.deleted_refs),
            },
        )

    def _sync_checkout(self, state: RunState, workflow: WorkflowSpec, ctx: _RunContext) -> bool:
        """FR-13: fast-forward every isolated repo's PRIMARY checkout (never a worktree) to
        the current integration head, at a barrier point (before a non-isolated dispatch)
        or at run end (best-effort). Returns True on success/skip/nothing-to-do, False on a
        structured failure (a dirty collision, a genuine non-fast-forward, or a sync
        anomaly).

        E-Wk9Tz3 T-Wl2Bq7 R-4/R-12: uses `GitRepo.fast_forward_checkout` -- a ref/index-safe
        plumbing sequence (`read-tree` + `update-ref`, never `merge`/`rebase`) -- resolving
        the T-En8Hd4 review W-1 interface gap (this file no longer reaches `GitRepo._run`
        directly). R-4: for ``workspace_lock: "skip_sync"`` this is unconditionally a no-op
        (the shared checkout must never see this run's work -- landing on the integration
        ref is still safe and complete, mergeable by hand); for ``"require"``, syncing only
        ever proceeds while `state.integration.workspace_lock_held` is True (never set for a
        denied/not-yet-attempted claim, and cleared by a denied resume-takeover reclaim) --
        the lock is what makes this fast-forward of the ONE shared checkout safe against a
        second run's own barrier-time sync (HLD §12.3).
        """
        run_log = ctx.run_log
        spec = workflow.integration
        if spec.sync_checkout == "never":
            run_log.info(
                "integration.sync_skipped",
                extra={"event": "integration.sync_skipped", "reason": "sync_checkout=never"},
            )
            return True
        if ctx.worktree_manager is None:
            return True  # nothing activated yet -- nothing to sync
        if spec.workspace_lock == _WORKSPACE_LOCK_SKIP_SYNC:
            run_log.info(
                "integration.sync_skipped",
                extra={"event": "integration.sync_skipped", "reason": "workspace_lock=skip_sync"},
            )
            return True
        require_but_unheld = (
            spec.workspace_lock == _WORKSPACE_LOCK_REQUIRE
            and not state.integration.workspace_lock_held
        )
        if require_but_unheld:
            run_log.info(
                "integration.sync_skipped",
                extra={"event": "integration.sync_skipped", "reason": "workspace_lock_not_held"},
            )
            return True

        ok = True
        for repo in ctx.worktree_manager.repos:
            git = GitRepo(repo.toplevel)
            target = state.integration.heads.get(repo.key)
            if target is None:
                continue  # nothing landed for this repo yet
            current = git.rev_parse("HEAD")
            if current == target:
                state.integration.checkout_synced_to[repo.key] = target
                continue
            if current is None or not git.is_ancestor(current, target):
                run_log.warning(
                    "integration.sync_failed",
                    extra={
                        "event": "integration.sync_failed",
                        "repo": repo.key,
                        "reason": "not_fast_forward",
                        "current": current,
                        "target": target,
                    },
                )
                ok = False
                continue

            # R-12: the collision set is computed BEFORE attempting the fast-forward,
            # purely to classify a failure's cause afterwards -- never to skip the
            # attempt itself (a non-colliding dirty file must still sync successfully,
            # AC-9). Review C-2: rename detection forced OFF (`diff_names_no_renames`,
            # not `diff_names`) so a pure rename upstream still surfaces its OLD path --
            # a local edit sitting at that now-renamed-away path is exactly as real a
            # collision as one at the new path, and default rename-collapsed output would
            # silently miss it. Review W-1: the dirty set now includes untracked entries
            # too (`status_porcelain`'s own default), so an untracked-file collision
            # (`read-tree`'s "would be overwritten" case) is also named, not just reported
            # as a generic anomaly.
            incoming = set(git.diff_names_no_renames(repo.toplevel, current, target))
            dirty = {e.path for e in git.status_porcelain(repo.toplevel)}
            colliding = sorted(incoming & dirty)

            if git.fast_forward_checkout(repo.toplevel, current, target):
                state.integration.checkout_synced_to[repo.key] = target
                run_log.info(
                    "integration.sync_ok",
                    extra={"event": "integration.sync_ok", "repo": repo.key, "head": target},
                )
                continue

            ok = False
            if colliding:
                run_log.warning(
                    "integration.sync_failed",
                    extra={
                        "event": "integration.sync_failed",
                        "repo": repo.key,
                        "reason": "dirty_checkout",
                        "colliding_paths": colliding,
                        "hint": (
                            f"cd {repo.toplevel!r} && commit or stash local changes to "
                            f"{', '.join(colliding)}, then "
                            f"git merge --ff-only {state.integration.branch} (or merge it "
                            "by hand later -- the integration branch is complete either way)"
                        ),
                    },
                )
            else:
                # R-12: collision set was empty (now covering rename-old-paths AND
                # untracked entries, C-2/W-1) yet the fast-forward still refused -- a
                # genuine anomaly: the checkout moved under us between the collision-set
                # computation above and the attempt itself. Distinct cause, distinct
                # report.
                run_log.warning(
                    "integration.sync_failed",
                    extra={
                        "event": "integration.sync_failed",
                        "repo": repo.key,
                        "reason": "sync_anomaly",
                        "hint": (
                            f"the checkout at {repo.toplevel!r} moved during sync -- retry, "
                            f"or merge {state.integration.branch} by hand"
                        ),
                    },
                )
        return ok

    def _copy_untracked_outputs(
        self,
        untracked: list[str],
        task_iso: TaskIsolation,
        policy: Literal["copy", "fail", "ignore"],
        run_log: logging.LoggerAdapter,
    ) -> bool:
        """§7.4: a declared output that lands in the worktree but stays untracked+gitignored
        there (e.g. a build/ or output/ directory the repo ignores) is copied back to its
        shared path after a successful integration -- without this it would silently vanish
        once the worktree is released. Returns False only for ``untracked_outputs: "fail"``
        with something to copy (the caller downgrades the task to failed); ``"ignore"`` and
        a successful ``"copy"`` both return True.
        """
        if not untracked:
            return True
        if policy == "ignore":
            return True
        if policy == "fail":
            run_log.error(
                "integration.untracked_outputs_blocked",
                extra={"event": "integration.untracked_outputs_blocked", "outputs": untracked},
            )
            return False
        base = self._require_local_fs_store()
        # T-Ee3Mn8/C-10: the abspath+symlink-resolution primitive `IsolatedArtifactView`
        # builds on is sanctioned ONLY inside `artifacts.py` and `isolation/view.py` (a
        # dedicated structural test asserts no other caller exists) -- so the worktree-side
        # path is derived through a fresh view instead of reaching into that primitive
        # directly.
        view = IsolatedArtifactView(base=base, task_isolation=task_iso)
        for output in untracked:
            src = view.resolve(output)
            dst = base.resolve(output)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
            run_log.info(
                "integration.artifact_copied",
                extra={"event": "integration.artifact_copied", "output": output},
            )
        return True

    def _warn_if_retention_high(
        self, state: RunState, workflow: WorkflowSpec, ctx: _RunContext
    ) -> None:
        """S-7: warn (never a hard cap -- deleting failure evidence to save disk is
        explicitly rejected, HLD §24 "Deferred") once retained (non-integrated) task
        worktrees cross `_WORKTREE_RETENTION_WARN_THRESHOLD`, naming the operator remedy.
        Emitted at most once per run (`ctx.retention_warned` latch).
        """
        if ctx.retention_warned or workflow.integration.keep_worktrees == "never":
            return
        retained = sum(
            1
            for ti in state.task_integration.values()
            if ti.status in ("failed", "conflict_resolver", "conflict_rerun")
        )
        if retained >= _WORKTREE_RETENTION_WARN_THRESHOLD:
            ctx.retention_warned = True
            ctx.run_log.warning(
                "worktree.retention_high",
                extra={
                    "event": "worktree.retention_high",
                    "retained": retained,
                    "threshold": _WORKTREE_RETENTION_WARN_THRESHOLD,
                    "remedy": "ao prune --worktrees-only",
                },
            )

    def _log_integration_summary(self, state: RunState, run_log: logging.LoggerAdapter) -> None:
        integrated = sum(1 for ti in state.task_integration.values() if ti.status == "integrated")
        conflicts = sum(
            1
            for ti in state.task_integration.values()
            if ti.status in ("conflict_resolver", "conflict_rerun")
        )
        failed_count = sum(1 for ti in state.task_integration.values() if ti.status == "failed")
        run_log.info(
            "integration.summary",
            extra={
                "event": "integration.summary",
                "branch": state.integration.branch,
                "heads": state.integration.heads,
                "integrated": integrated,
                "conflicts": conflicts,
                "failed": failed_count,
            },
        )

    # -------------------------------------------------------------------------
    # Budget helpers (T-algywf)
    # -------------------------------------------------------------------------

    @staticmethod
    def _sum_actuals(result: TaskResult) -> int:
        """Sum all token fields from a TaskResult (input + output + cache fields)."""
        total = 0
        for usage_field in (
            result.input_tokens,
            result.output_tokens,
            result.cache_creation_input_tokens,
            result.cache_read_input_tokens,
        ):
            if usage_field is not None:
                total += usage_field
        return total

    @staticmethod
    def _accumulate_actuals(ts: TaskRunState, result: TaskResult) -> None:
        """Add *result*'s actual usage onto *ts*'s cumulative totals (E-9h3m7k FR-2).

        Shared by every cross-call requeue site (self-heal's Consult Point B retry,
        and the settle-time accumulation that also covers the T2/T3 conflict-ladder
        `conflict_resolver`/`conflict_rerun` requeue, R-1a/E-Wk9Tz3 T-Ac6Vd9) so there
        is exactly ONE implementation of "accumulate before a cross-call requeue can
        discard this cycle's actuals" rather than a near-duplicate per call site
        (CLAUDE.md no-duplicate-logic). `+=` (not `=`): a prior cycle's already-
        accumulated actuals (added by an earlier call to this same helper) must never
        be clobbered -- safe for every caller, since a fresh `TaskRunState` (a task's
        first dispatch, or `prepare_resume`'s reset) always starts `cumulative_*` at 0,
        so `+=` is byte-identical to `=` whenever nothing was accumulated first.
        """
        if not result.actuals_available:
            return
        ts.cumulative_input_tokens += result.input_tokens or 0
        ts.cumulative_output_tokens += result.output_tokens or 0
        ts.cumulative_cache_creation_input_tokens += result.cache_creation_input_tokens or 0
        ts.cumulative_cache_read_input_tokens += result.cache_read_input_tokens or 0
        ts.cumulative_cost_usd += result.cost_usd or 0.0

    def _is_unsatisfiable(self, estimate: int, decision: BudgetDecision) -> bool:
        """Return True if this estimate can NEVER be admitted (estimate alone exceeds limit).

        This prevents an infinite wait loop when a single task's estimate exceeds the
        entire window budget or total budget.
        """
        if self._budget_manager is None:
            return False
        spec = getattr(self._budget_manager, "_spec", None)
        if spec is None:
            return False
        if decision.blocked_by == "total" and spec.total_tokens is not None:
            return estimate > spec.total_tokens
        if decision.blocked_by == "rate" and spec.rate is not None:
            return estimate > spec.rate.tokens
        return False

    # -------------------------------------------------------------------------
    # Routing helpers (T-m2h5t7, LLD §5)
    # -------------------------------------------------------------------------

    def _router_for_task(self, workflow: WorkflowSpec, task_id: str) -> RouterSpec | None:
        """Return the RouterSpec whose ``router_task_id`` matches *task_id*, if any."""
        for router in workflow.branches:
            if router.router_task_id == task_id:
                return router
        return None

    def _apply_join(
        self,
        task: TaskSpec,
        state: RunState,
        membership: dict[str, set[tuple[str, str]]],
        producer_of: Callable[[str], str | None],
    ) -> str | None:
        """Evaluate join policy pre-dispatch (LLD §5.4).

        Returns ``"not_taken"`` when the task must be skipped without dispatching
        (its join policy says so — see below), else ``None`` (dispatch normally).

        ``membership`` is accepted (unused in the resolution below) to mirror the
        LLD §5.4 signature and leave room for future join diagnostics; resolving
        the join only needs each *effective dependency*'s settled status.

        - ``join == "all"`` (default): any not_taken effective dependency
          propagates not_taken to *task* (a full convergence needs every branch).
        - ``join == "any"``: *task* is not_taken only when EVERY effective
          dependency is not_taken; it dispatches once at least one is live (its
          missing-input check is separately relaxed for not_taken producers,
          §5.4a, at the call site).

        "Effective dependencies" = declared ``depends_on`` plus every inferred
        producer of a declared input not already in ``depends_on`` (mirrors the
        DAG's own inferred-edge rule, ``dag.build_dag``).
        """
        effective_deps: list[str] = list(task.depends_on)
        for inp in task.inputs:
            producer = producer_of(inp)
            if producer is not None and producer not in effective_deps:
                effective_deps.append(producer)

        not_taken_deps = [
            d
            for d in effective_deps
            if state.tasks.get(d) is not None and state.tasks[d].status == "not_taken"
        ]

        if task.join == "all":
            if not_taken_deps:
                ts = state.tasks.setdefault(task.id, TaskRunState())
                ts.status = "not_taken"
                ts.not_taken_reason = "join=all; dep(s) not_taken: " + ",".join(not_taken_deps)
                return "not_taken"
            return None  # topo order already guarantees deps are settled

        # join == "any": dispatch once at least one effective dependency is live.
        live_deps = [d for d in effective_deps if d not in not_taken_deps]
        if not live_deps:
            ts = state.tasks.setdefault(task.id, TaskRunState())
            ts.status = "not_taken"
            ts.not_taken_reason = "join=any; all dep(s) not_taken: " + ",".join(not_taken_deps)
            return "not_taken"
        return None

    def _on_router_success(
        self,
        router: RouterSpec,
        state: RunState,
        cones: dict[str, dict[str, set[str]]],
        run_log: logging.LoggerAdapter,
    ) -> Literal["ok", "failed"]:
        """Router-success hook (LLD §5.2), called once ``router.router_task_id``
        settles ``succeeded``: read the verdict, persist ``route_decisions`` as
        the source of truth, mark every unselected route's exclusive cone
        ``not_taken``, and tag activated tasks' ``route``.

        Returns ``"failed"`` when the run must fail (verdict unreadable, or an
        empty/unknown verdict with no ``default_route``) — ``_route_fail`` has
        already set ``state.status="failed"`` and persisted; the caller sets the
        run loop's ``failed=True`` and breaks (mirrors the loop's existing
        break-on-terminal-failure pattern). Returns ``"ok"`` otherwise.
        """
        try:
            selected_raw = read_routes(self._store, router.verdict_path, router.verdict_field)
        except ControlFileError as exc:
            self._route_fail(state, router, f"verdict unreadable: {exc}", run_log)
            return "failed"

        known = set(router.routes.keys())
        selected = [r for r in selected_raw if r in known]  # drop unknown route ids

        if not selected:
            if router.default_route is not None:
                selected = [router.default_route]
            else:
                self._route_fail(state, router, "empty/unknown verdict, no default_route", run_log)
                return "failed"

        state.route_decisions[router.id] = selected  # source of truth, persisted below

        router_cones = cones.get(router.id, {})
        not_taken_ids: list[str] = []
        for route_id, route_cone in router_cones.items():
            if route_id in selected:
                continue
            for t in route_cone:
                ts = state.tasks.setdefault(t, TaskRunState())
                if ts.status in ("succeeded", "skipped", "failed"):
                    continue  # never override an already-settled task
                ts.status = "not_taken"
                ts.route = f"{router.id}:{route_id}"
                ts.not_taken_reason = (
                    f"router={router.router_task_id} route={route_id} not selected"
                )
                not_taken_ids.append(t)

        for route_id in selected:
            for t in router_cones.get(route_id, set()):
                state.tasks.setdefault(t, TaskRunState()).route = f"{router.id}:{route_id}"

        run_log.info(
            "branch.route",
            extra={
                "event": "branch.route",
                "router_id": router.id,
                "router_task_id": router.router_task_id,
                "selected": selected,
                "not_taken_count": len(not_taken_ids),
            },
        )
        self._runstate.save(state)
        return "ok"

    def _route_fail(
        self,
        state: RunState,
        router: RouterSpec,
        reason: str,
        run_log: logging.LoggerAdapter,
    ) -> None:
        """Validation-style routing failure (LLD §5.2/§5.6) — distinct from a
        circuit breaker. Sets ``state.status="failed"``, emits ``branch.route``
        with an ``error`` field, and persists. The caller is responsible for
        setting the run loop's ``failed=True`` and breaking (mirrors the engine's
        existing break-on-terminal-failure pattern)."""
        state.status = "failed"
        run_log.error(
            "branch.route",
            extra={
                "event": "branch.route",
                "router_id": router.id,
                "router_task_id": router.router_task_id,
                "error": reason,
            },
        )
        self._runstate.save(state)

    # -------------------------------------------------------------------------
    # Monitoring / self-healing helpers (E-XyfjuZ, Consult Point A)
    # -------------------------------------------------------------------------

    def _consult_breaker_trips(
        self,
        newly_tripped_specs: list[CircuitBreakerSpec],
        state: RunState,
        run_log: logging.LoggerAdapter,
    ) -> Literal["extend", "halt"]:
        """Consult Point A (epic doc Design Decisions D1-D3): per newly-tripped
        recommend-mode breaker, in DECLARED order, ask ``self._monitor`` whether to
        extend or halt.

        All-or-nothing (D3): if EVERY consulted breaker resolves to ``"extend"``, every
        extension is applied (via the existing ``apply_breaker_extension``, un-latching
        each breaker) and ``"extend"`` is returned so the caller lets the run continue. If
        ANY breaker resolves to ``"halt"`` — an explicit monitor answer, or a
        bound/cap already exhausted — NO extension is applied at all and ``"halt"`` is
        returned (today's halt path). The caller is responsible for persisting ``state``
        — this method mutates it in-memory only, mirroring
        ``evaluate_breakers``/``apply_breaker_extension``'s own persistence convention.

        Defense-in-depth: every *newly_tripped_specs* entry must be ``mode="recommend"``
        (the caller already filters for this before calling). Raised (not an ``assert``,
        which ``python -O`` strips) because built-in hard stops must NEVER reach this
        method by construction (D1) — this is enforcement in code, not convention.

        Reviewer-flagged edge case: an EMPTY *newly_tripped_specs* would make the later
        ``all(...)`` vacuously ``True`` (Python's `all([])` is `True`), which would
        wrongly return ``"extend"``. The current call site never invokes this with an
        empty list (it only calls in when `_newly_tripped_specs` is non-empty), but this
        guard makes that precondition explicit and safe for any future caller.
        """
        if not newly_tripped_specs:
            return "halt"
        if any(b.mode != "recommend" for b in newly_tripped_specs):
            raise AssertionError(
                "_consult_breaker_trips must only ever be called with recommend-mode breakers"
            )

        decisions: list[tuple[CircuitBreakerSpec, BreakerVerdict | None]] = []
        for spec in newly_tripped_specs:
            prior = count_monitor_breaker_extensions(state, spec.id)
            if prior >= self._max_extensions_per_breaker:
                # Bound exhausted -> forced halt contribution WITHOUT consulting at all
                # (never even calls the monitor, per the epic brief's "bound exhausted ->
                # halt regardless of monitor answer").
                decisions.append((spec, None))
                continue
            if count_monitor_calls_made(state) >= self._max_monitor_calls_per_run:
                run_log.warning(
                    "monitor call cap reached; falling back to the safe default (halt)",
                    extra={
                        "event": "monitor.cap_exceeded",
                        "consult_point": "breaker_trip",
                        "subject_id": spec.id,
                        "max_monitor_calls_per_run": self._max_monitor_calls_per_run,
                    },
                )
                decisions.append((spec, None))
                continue

            trip_record = next((tb for tb in state.tripped_breakers if tb.id == spec.id), None)
            trip = BreakerTripSummary(
                breaker_id=spec.id,
                condition=spec.condition,
                action=spec.action,
                detail=trip_record.detail if trip_record is not None else {},
                prior_extensions=prior,
            )
            run_log.info(
                "monitor.consult",
                extra={
                    "event": "monitor.consult",
                    "consult_point": "breaker_trip",
                    "subject_id": spec.id,
                    "monitor": self._monitor.name,
                },
            )
            try:
                verdict = self._monitor.decide_breaker_trip(trip, run_id=state.run_id)
            except Exception as exc:
                # NFR-2: a monitor bug must never make the run less safe than today --
                # fall back to the safe default rather than propagating.
                run_log.error(
                    "Monitor.decide_breaker_trip raised; falling back to the safe default: %s",
                    exc,
                    extra={
                        "event": "monitor.decision",
                        "consult_point": "breaker_trip",
                        "subject_id": spec.id,
                        "monitor": self._monitor.name,
                        "decision": SAFE_DEFAULT_BREAKER_VERDICT.decision,
                    },
                )
                state.monitor_decisions.append(
                    MonitorDecisionRecord(
                        at=self._clock().isoformat(),
                        consult_point="breaker_trip",
                        subject_id=spec.id,
                        decision=SAFE_DEFAULT_BREAKER_VERDICT.decision,
                        monitor=self._monitor.name,
                        detail={"reason": f"monitor raised: {exc}"},
                    )
                )
                decisions.append((spec, SAFE_DEFAULT_BREAKER_VERDICT))
                continue

            state.monitor_decisions.append(
                MonitorDecisionRecord(
                    at=self._clock().isoformat(),
                    consult_point="breaker_trip",
                    subject_id=spec.id,
                    decision=verdict.decision,
                    monitor=self._monitor.name,
                    detail={
                        "extend_by_seconds": verdict.extend_by_seconds,
                        "reason": verdict.reason,
                    },
                )
            )
            run_log.info(
                "monitor.decision",
                extra={
                    "event": "monitor.decision",
                    "consult_point": "breaker_trip",
                    "subject_id": spec.id,
                    "monitor": self._monitor.name,
                    "decision": verdict.decision,
                },
            )
            decisions.append((spec, verdict))

        if all(v is not None and v.decision == "extend" for _, v in decisions):
            for spec, resolved_verdict in decisions:
                assert resolved_verdict is not None  # narrowed by the all(...) check above
                apply_breaker_extension(
                    state,
                    spec,
                    extend_by_seconds=resolved_verdict.extend_by_seconds,
                    extend_by_same=resolved_verdict.extend_by_seconds is None,
                    clock=self._clock,
                    run_log=run_log,
                )
            return "extend"
        return "halt"

    def _consult_task_failure_heal(
        self,
        tid: str,
        result: TaskResult,
        state: RunState,
        run_log: logging.LoggerAdapter,
    ) -> HealVerdict | None:
        """Consult Point B (epic doc Design Decision D4): ask ``self._monitor`` whether a
        task that just settled ``"failed"`` (after exhausting its ``RetryPolicy``) should
        get one bounded extra retry.

        Returns ``None`` when the bound (``max_heal_retries_per_task``) or the shared cap
        (``max_monitor_calls_per_run``) is already exhausted — the caller treats ``None``
        identically to an explicit ``"accept_failure"`` verdict: fall through to the
        existing, unmodified failure-handling code without even asking the monitor.
        Returns the Monitor's actual verdict otherwise (which may itself be
        ``"accept_failure"``).
        """
        prior = count_monitor_heal_retries(state, tid)
        if prior >= self._max_heal_retries_per_task:
            return None  # bound exhausted -> accept the failure, never even consult
        if count_monitor_calls_made(state) >= self._max_monitor_calls_per_run:
            run_log.warning(
                "monitor call cap reached; falling back to the safe default (accept_failure)",
                extra={
                    "event": "monitor.cap_exceeded",
                    "consult_point": "task_failure",
                    "subject_id": tid,
                    "max_monitor_calls_per_run": self._max_monitor_calls_per_run,
                },
            )
            return None

        summary = build_task_failure_summary(result, prior_heal_retries=prior)
        run_log.info(
            "monitor.consult",
            extra={
                "event": "monitor.consult",
                "consult_point": "task_failure",
                "subject_id": tid,
                "monitor": self._monitor.name,
            },
        )
        try:
            verdict = self._monitor.decide_task_failure(summary, run_id=state.run_id)
        except Exception as exc:
            # NFR-2: a monitor bug must never make the run less safe than today -- fall
            # back to the safe default rather than propagating.
            run_log.error(
                "Monitor.decide_task_failure raised; falling back to the safe default: %s",
                exc,
                extra={
                    "event": "monitor.decision",
                    "consult_point": "task_failure",
                    "subject_id": tid,
                    "monitor": self._monitor.name,
                    "decision": SAFE_DEFAULT_HEAL_VERDICT.decision,
                },
            )
            state.monitor_decisions.append(
                MonitorDecisionRecord(
                    at=self._clock().isoformat(),
                    consult_point="task_failure",
                    subject_id=tid,
                    decision=SAFE_DEFAULT_HEAL_VERDICT.decision,
                    monitor=self._monitor.name,
                    detail={"reason": f"monitor raised: {exc}"},
                )
            )
            return SAFE_DEFAULT_HEAL_VERDICT

        state.monitor_decisions.append(
            MonitorDecisionRecord(
                at=self._clock().isoformat(),
                consult_point="task_failure",
                subject_id=tid,
                decision=verdict.decision,
                monitor=self._monitor.name,
                detail={"wait_seconds": verdict.wait_seconds, "reason": verdict.reason},
            )
        )
        run_log.info(
            "monitor.decision",
            extra={
                "event": "monitor.decision",
                "consult_point": "task_failure",
                "subject_id": tid,
                "monitor": self._monitor.name,
                "decision": verdict.decision,
            },
        )
        return verdict

    def _run_and_integrate(
        self,
        task: TaskSpec,
        workflow: WorkflowSpec,
        agents: dict,
        repo_paths: dict,
        state: RunState,
        dynamic_input_paths: list[str] | None,
        task_manifest_path: str | None,
        gate_output_path: str | None,
        store: ArtifactStore | None,
        task_iso: TaskIsolation | None,
        integrator: Integrator | None,
        run_integration: RunIntegrationSnapshot | None,
        env_overlay: dict[str, str] | None,
        agent_id: str,
        cycle: int = 1,
    ) -> WorkerOutcome:
        """Runs on a WORKER thread (ADR-0007 D3): `_run_with_retries` is unchanged, then --
        for a succeeded, isolated task ONLY -- `integrate()` through `_integrate_task` (the
        ONE adapter into `isolation.integrator.Integrator`). Performs NO `RunState`
        mutation and NO ``save()`` (NFR-3): everything read off *state*/*task*/*task_iso*
        here is read-only; a T3 worktree reset (below) is a git operation scoped to this
        task's own exclusively-owned worktree, not a `RunState` write.

        *cycle* (E-Wk9Tz3 T-Ac6Vd9, R-21): this dispatch's ``TaskRunState.dispatch_cycle``,
        forwarded verbatim to `_run_with_retries` to key its capture directory -- see that
        function's own docstring.

        E-Wk9Tz3 T-Lr6Ka3 (HLD §8.4-8.5): a task requeued in "resolve"/"rerun" mode
        (``state.task_integration[tid].mode``, set by `_settle_completed_task`'s conflict
        switch) is dispatched through this SAME function again on its next wave.
        ``mode == "resolve"``: `_prepare_resolver_dispatch` first calls
        `Integrator.materialize_conflict` (review C-1 rework -- `WorktreeManager.ensure()`'s
        own AC-10c `git rebase --abort` on any reused mid-rebase worktree, run on the MAIN
        thread before this worker ever starts, means the worktree is never genuinely
        mid-rebase by dispatch time on its own; this re-derives that state deterministically
        instead of assuming it survived). If a live conflict re-materializes, it substitutes
        the resolver agent/instruction/conflict-manifest input (S-2's forced containment,
        manifest built from the LIVE re-derived state, never `ti`'s possibly-stale one) and
        the R-2 outputs gate is skipped (a resolver doesn't produce `task.outputs`) in favour
        of `_integrate_task(..., resume=True)` (continues the now-genuine mid-rebase state).
        If it no longer conflicts (the head moved since and the durable squash now applies
        cleanly) or couldn't be re-derived (lock timeout/git error), the resolver is never
        dispatched at all -- straight to `_integrate_task(..., resume=True)`, which
        idempotently re-derives the same result (or surfaces the same failure) without
        spending any LLM budget on a conflict that no longer needs a resolver.
        ``mode == "rerun"``: `_prepare_rerun_dispatch` resets the worktree to a fresh
        integration head + exports the superseded squash as `previous-<n>.patch`, then the
        ORIGINAL agent is redispatched unchanged and the normal R-2 gate + a fresh (never
        resumed) `integrate()` call apply, exactly like ``mode == "normal"``.
        """
        ti = state.task_integration.get(task.id)
        mode = ti.mode if ti is not None else _INTEGRATION_MODE_NORMAL
        rerun_base: dict[str, str] | None = None

        if mode == _INTEGRATION_MODE_RESOLVE and ti is not None and task_iso is not None:
            assert integrator is not None and run_integration is not None
            dispatch_task, dispatch_agents, dispatch_env, materialized = (
                self._prepare_resolver_dispatch(
                    task,
                    agents,
                    env_overlay,
                    workflow.integration,
                    ti,
                    task_iso,
                    integrator,
                    run_integration,
                )
            )
            if not materialized:
                # No live conflict to hand a resolver (already clean, or couldn't be
                # re-derived) -- skip the dispatch entirely: no agent execution, no LLM
                # spend, straight to resume_integration to land (or report the same
                # failure resume_integration's own error handling already covers).
                # `attempt=ti.attempts` (NOT +1): resume_integration looks up the durable
                # squash ref this SAME attempt number recorded originally.
                skip_result = TaskResult(
                    task_id=task.id, status="succeeded", attempts=0, actuals_available=False
                )
                integration = self._integrate_task(
                    integrator,
                    task_iso,
                    run_integration,
                    ti,
                    agent_id=agent_id,
                    attempt=ti.attempts,
                    resume=True,
                )
                return WorkerOutcome(skip_result, integration, task_iso=task_iso)
        elif mode == _INTEGRATION_MODE_RERUN and ti is not None and task_iso is not None:
            assert run_integration is not None
            dispatch_task, rerun_base = self._prepare_rerun_dispatch(
                task, ti, task_iso, run_integration, state.run_id
            )
            dispatch_agents = agents
            dispatch_env = env_overlay
        else:
            dispatch_task = task
            dispatch_agents = agents
            dispatch_env = env_overlay

        result = self._run_with_retries(
            dispatch_task,
            workflow,
            dispatch_agents,
            repo_paths,
            state,
            dynamic_input_paths,
            task_manifest_path=task_manifest_path,
            gate_output_path=gate_output_path,
            store=store,
            env_overlay=dispatch_env,
            cycle=cycle,
        )
        if result.status != "succeeded" or task_iso is None:
            return WorkerOutcome(result, None, task_iso=task_iso, rerun_base=rerun_base)

        if mode == _INTEGRATION_MODE_RESOLVE and ti is not None:
            # AC-6/AC-7: a resolver dispatch never produces `task.outputs` -- the R-2 gate
            # below is for "normal"/"rerun" mode only. `attempt=ti.attempts` (NOT +1):
            # `resume_integration` looks up the durable squash ref this SAME attempt number
            # recorded when the conflict first happened (`_settle_completed_task` already
            # incremented `ti.attempts` to that value when it recorded the conflict) -- a
            # fresh `attempt=ti.attempts + 1` would look up a squash ref that was never
            # created, since resuming doesn't allocate a new one.
            assert integrator is not None and run_integration is not None
            integration = self._integrate_task(
                integrator,
                task_iso,
                run_integration,
                ti,
                agent_id=agent_id,
                attempt=ti.attempts,
                resume=True,
            )
            return WorkerOutcome(result, integration, task_iso=task_iso)

        # R-2: outputs gate integration -- on the worker, through the isolated view, BEFORE
        # integrate() is called. The main-thread check in _settle_completed_task is left
        # UNTOUCHED (it re-runs against self._store and reaches the same verdict for the
        # consumer's outputs-outside-repo convention -- NFR-2).
        st = store if store is not None else self._store
        missing = [o for o in task.outputs if not st.exists(o)]
        if missing:
            return WorkerOutcome(
                result, None, missing_outputs=missing, task_iso=task_iso, rerun_base=rerun_base
            )

        assert integrator is not None and run_integration is not None
        ti = state.task_integration[task.id]
        integration = self._integrate_task(
            integrator,
            task_iso,
            run_integration,
            ti,
            agent_id=agent_id,
            attempt=ti.attempts + 1,
        )
        return WorkerOutcome(result, integration, task_iso=task_iso, rerun_base=rerun_base)

    def _prepare_resolver_dispatch(
        self,
        task: TaskSpec,
        agents: dict,
        env_overlay: dict[str, str] | None,
        spec: IntegrationSpec,
        ti: TaskIntegrationState,
        task_iso: TaskIsolation,
        integrator: Integrator,
        run_integration: RunIntegrationSnapshot,
    ) -> tuple[TaskSpec, dict, dict[str, str] | None, bool]:
        """T2 (``mode == "resolve"``) dispatch-context override (AC-3/AC-4, S-2, review
        C-1 rework). Returns ``(task, agents, env, materialized)`` -- ``materialized`` is
        ``False`` only when a live conflict was confirmed NOT to exist (or could not be
        re-derived); the caller then skips dispatching a resolver entirely.

        Defensive fallback (checked FIRST, before touching `integrator` at all): if
        ``spec.resolver_agent`` is unset or not a known agent id (a directly-injected
        test-double ``escalation_hook`` can set ``mode == "resolve"`` without ever going
        through `escalation.escalate`, whose own preconditions guarantee this can't happen
        for a REAL ladder), dispatch the task's own agent/instruction unchanged and report
        ``materialized=True`` (proceed normally) -- this also means `Integrator.
        materialize_conflict` is never called for this path, so a test double that only
        implements `integrate`/`resume_integration` (pre-existing scripted-integrator
        tests elsewhere in this repo that never configure `resolver_agent`) is unaffected.

        Otherwise, calls `Integrator.materialize_conflict` to re-derive the LIVE conflict
        state (`WorktreeManager.ensure()`'s own AC-10c abort means nothing survives from a
        previous wave -- see `_run_and_integrate`'s own docstring). Only when it reports a
        genuine, live conflict does this build the conflict manifest / copy the
        instruction / substitute the agent -- and the manifest's `conflicted_paths` /
        `rebase_in_progress` are computed from THAT live result, never from `ti`'s
        (possibly stale) recorded values.
        """
        resolver_id = spec.resolver_agent
        if resolver_id is None or resolver_id not in agents:
            return task, agents, env_overlay, True

        materialize = integrator.materialize_conflict(
            task_iso, run_integration, ti.attempts, base_commits=ti.base_commits
        )
        if materialize.status != "conflict":
            return task, agents, env_overlay, False

        n = ti.resolver_attempts
        run_id = run_integration.run_id
        manifest_relpath = resolver_escalation.conflict_manifest_relpath(run_id, task.id, n)
        instruction_relpath = resolver_escalation.resolver_instruction_relpath(run_id, task.id)

        # Live ti snapshot (AC-2): the manifest's conflicted_paths/rebase_in_progress
        # reflect what `materialize_conflict` JUST confirmed on disk, not `ti`'s own
        # (possibly stale, pre-materialization) recorded value.
        live_ti = ti.model_copy(
            update={
                "conflicted_paths": materialize.conflicted_paths,
                "tier_reached": materialize.tier_reached or ti.tier_reached,
            }
        )
        resolver_escalation.write_conflict_manifest(
            self._store.resolve(manifest_relpath),
            run_id=run_id,
            task_id=task.id,
            attempt=n,
            task_integration=live_ti,
            task_iso=task_iso,
            rebase_in_progress=(materialize.status == "conflict"),
        )
        instruction_source = (
            self._store.resolve(spec.resolver_instruction)
            if spec.resolver_instruction
            else _RESOLVER_INSTRUCTION_ASSET
        )
        resolver_escalation.copy_resolver_instruction(
            instruction_source, self._store.resolve(instruction_relpath)
        )

        dispatch = resolver_escalation.build_resolver_dispatch(
            task,
            agents,
            env_overlay,
            spec,
            manifest_relpath=manifest_relpath,
            instruction_relpath=instruction_relpath,
        )
        return dispatch.task, dispatch.agents, dispatch.env, True

    def _prepare_rerun_dispatch(
        self,
        task: TaskSpec,
        ti: TaskIntegrationState,
        task_iso: TaskIsolation,
        run_integration: RunIntegrationSnapshot,
        run_id: str,
    ) -> tuple[TaskSpec, dict[str, str]]:
        """T3 (``mode == "rerun"``) dispatch-context override (AC-8): resets every one of
        this task's repos' worktrees HARD to the CURRENT integration head (read live off
        the ref -- the same race-tolerant technique `Integrator` itself already uses:
        `integrate()` re-rebases onto whatever the head actually is at land time
        regardless, so a reset that is a beat stale is wasted work, never a correctness
        bug), exporting the superseded squash's diff against its old base to
        ``previous-<n>.patch`` for the PRIMARY repo only first (``task_iso.repos[0]``,
        deterministic -- same "one task-level artifact keyed off the primary repo"
        convention `_run_verify_command` already uses).

        Returns the task -- with the patch path appended to its inputs ONLY when the
        export actually happened (review W-1: AC-8 pairs "exported" and "appended to
        inputs" unconditionally; decoupling them would tell the agent an input exists
        that doesn't) -- and the new per-repo base (the caller reports it back to the
        main thread via `WorkerOutcome.rerun_base`, since this function itself performs no
        `RunState` mutation -- NFR-3).
        """
        primary = task_iso.repos[0]
        patch_relpath = resolver_escalation.previous_patch_relpath(run_id, task.id, ti.reruns)
        patch_exported = False
        new_base: dict[str, str] = {}
        for repo in task_iso.repos:
            git = GitRepo(repo.worktree_root)
            fresh_head = git.rev_parse(f"refs/heads/{run_integration.branch}") or repo.base
            if repo.key == primary.key:
                squash = ti.squash_commits.get(repo.key)
                if squash:
                    old_base = ti.base_commits.get(repo.key, repo.base)
                    resolver_escalation.export_previous_patch(
                        self._store.resolve(patch_relpath),
                        git,
                        cwd=repo.worktree_root,
                        base=old_base,
                        head=squash,
                    )
                    patch_exported = True
                else:
                    logger.warning(
                        "T3 rerun: no recorded squash for repo %s (task %s) -- "
                        "previous-<n>.patch not exported, not appended to inputs",
                        repo.key,
                        task.id,
                        extra={
                            "event": "integration.rerun_patch_skipped",
                            "task_id": task.id,
                            "repo": repo.key,
                        },
                    )
            git.reset_hard(repo.worktree_root, fresh_head)
            new_base[repo.key] = fresh_head
        dispatch_task = (
            resolver_escalation.build_rerun_task(task, patch_relpath) if patch_exported else task
        )
        return dispatch_task, new_base

    def _run_with_retries(
        self,
        task,
        workflow: WorkflowSpec,
        agents: dict,
        repo_paths: dict,
        state: RunState,
        dynamic_input_paths: list[str] | None = None,
        task_manifest_path: str | None = None,
        gate_output_path: str | None = None,
        store: ArtifactStore | None = None,
        env_overlay: dict[str, str] | None = None,
        cycle: int = 1,
    ) -> TaskResult:
        """Execute *task* with the configured retry policy.

        Builds a TaskContext containing paths only (NFR-1 invariant).

        Parameters
        ----------
        task_manifest_path:
            Resolved path for an emit_tasks task to write its task manifest.
        gate_output_path:
            Resolved path for a loop gate task to write its verdict (iteration-suffixed).
        store:
            E-Wk9Tz3 R-19/AC-15 (THE primary execution path): the store every remappable
            path below resolves through. Defaults to ``self._store`` (byte-identical to
            today, NFR-2) -- an isolated dispatch passes the task's `IsolatedArtifactView`
            so an "isolated" task actually reads/writes its own worktree instead of the
            shared checkout every other concurrent worker shares. ``self._store`` itself is
            NEVER swapped (it is shared across every worker); only the LOCAL ``st`` binding
            below changes per call.
        env_overlay:
            E-Wk9Tz3 AC-8: ``TaskContext.env`` -- empty (the default) for a non-isolated
            task, which is what keeps ``ClaudeCliExecutor``'s ``env=`` argument byte-
            identical (``None``) for every pre-epic workflow.
        cycle:
            E-Wk9Tz3 T-Ac6Vd9 (R-21): this dispatch's ``TaskRunState.dispatch_cycle``,
            defaulting to ``1`` (the value a task's first-ever dispatch always carries,
            since the engine increments it unconditionally before every dispatch --
            R-21/T-En8Hd4). Keys the capture directory (see ``output_dir`` below) so a
            T2/T3 conflict-ladder redispatch, a self-heal retry, or a quota/429 requeue
            never overwrites an earlier cycle's already-captured transcript.
        """
        st: ArtifactStore = store if store is not None else self._store
        retry = task.retries or workflow.defaults.retries
        timeout = task.timeout_seconds or workflow.defaults.timeout_seconds
        agent_spec = agents[task.agent]
        # Task-level model/effort/max_turns win over the agent's own (ADR-0003 decision 2,
        # fill-in not clobber) -- resolved once here so the executor never has to know
        # about task-level overrides; it just reads ctx.agent like before.
        effective_agent = resolve_effective_agent(task, agent_spec)

        # Resolve all paths through the artifact store (no content reads). E-Wk9Tz3 R-19:
        # SIX of the seven remappable path categories -- `st`, not `self._store` (repo_paths,
        # the seventh, is already a parameter and the caller supplies the isolated dict).
        instruction_path = st.resolve(task.instruction)
        general_instruction_paths = self._resolve_general_instructions(workflow, store=st)
        input_paths = [st.resolve(p) for p in task.inputs]
        output_paths = [st.resolve(p) for p in task.outputs]
        output_manifest_path = st.resolve(task.output_manifest) if task.output_manifest else None

        # Resolve task_manifest_path for emit_tasks (already resolved at call site, passed in)
        resolved_task_manifest_path: str | None = task_manifest_path

        # Working directory the agent runs in: AgentSpec.working_dir override (resolved
        # against, and path-guarded to, the workspace root) or the workspace root itself.
        # Ensures agents' relative output paths land inside the workspace deterministically.
        # (working_dir has no task-level override, so effective_agent == agent_spec here.)
        agent_cwd = st.resolve(effective_agent.working_dir or ".")

        # Capture directory: .orchestrator/runs/<run_id>/<task_id>/[cycle-<cycle>/]  (FR-4,
        # R-21/E-Wk9Tz3 T-Ac6Vd9). NOT through `st` -- lives under .orchestrator/, read
        # back by the engine (R-15) -- always self._store regardless of isolation,
        # exactly like task_manifest_path/gate_output_path above.
        #
        # R-21 / AC-7's "state in one place" decision: cycle 1 (the common, never-
        # requeued case) keeps the LEGACY FLAT layout, "<run_dir>/<task_id>/attempt-<n>/"
        # -- byte-identical to every pre-this-ticket run (NFR-2), confirmed by
        # tests/playground/test_sum_of_array_deterministic.py's own hardcoded
        # "<task_id>/attempt-1/..." path assertions, which regressed under an earlier
        # "nest every cycle, including 1" draft of this fix and is the concrete evidence
        # this decision is based on, not just an estimate. Cycle 2+ (a T2/T3 conflict-
        # ladder redispatch, a self-heal retry, or a quota/429 requeue -- all brand-new
        # calls to this method, whose own `for attempt in range(1, ...)` loop below
        # restarts at 1) nests under "cycle-<n>/" instead, so it can never collide with
        # cycle 1's own "attempt-<n>/" (the exact bug class E-9h3m7k already fixed once
        # for retries WITHIN one call). `ui/runs.py` (verified, not edited here -- out of
        # this ticket's file scope) never parses this path itself: it only ever surfaces
        # `TaskRunState.output_artifact_path` as an opaque string, so both shapes "just
        # work" as display strings with no reader-side change needed -- flagged in
        # STATUS.md for T-Dr5Yq6 in case a future capture-path-browsing feature ever
        # parses this shape directly.
        _task_run_dir = os.path.join(".orchestrator", "runs", state.run_id, task.id)
        _cycle_relpath = (
            _task_run_dir
            if cycle <= 1
            else os.path.join(_task_run_dir, f"{_CYCLE_DIR_PREFIX}{cycle}")
        )
        output_dir = self._store.resolve(_cycle_relpath)

        last_result: TaskResult | None = None
        # Running sums across every attempt of THIS call (E-9h3m7k FR-2): a task that
        # fails on attempt 1 and succeeds on attempt 2 must report the actual cost of
        # BOTH attempts, not just the winning one — money was spent on attempt 1 too.
        cum_input_tokens = 0
        cum_output_tokens = 0
        cum_cache_creation_input_tokens = 0
        cum_cache_read_input_tokens = 0
        cum_cost_usd = 0.0
        any_actuals = False

        for attempt in range(1, retry.max_attempts + 1):
            if self._cancel_fn():
                return TaskResult(
                    task_id=task.id,
                    status="cancelled",
                    attempts=attempt,
                )

            # Attempt-suffixed capture dir (E-9h3m7k): each retry gets its own
            # transcript.jsonl/result.json instead of the next attempt overwriting the
            # previous one's — a failed attempt's output is real observability data, not
            # noise to discard.
            attempt_output_dir = os.path.join(output_dir, f"attempt-{attempt}")

            ctx = TaskContext(
                run_id=state.run_id,
                task_id=task.id,
                agent=effective_agent,
                instruction_path=instruction_path,
                general_instruction_paths=general_instruction_paths,
                input_paths=input_paths,
                output_paths=output_paths,
                output_manifest_path=output_manifest_path,
                dynamic_input_paths=dynamic_input_paths or [],
                repo_paths=repo_paths,
                timeout_seconds=timeout,
                cwd=agent_cwd,
                output_dir=attempt_output_dir,
                task_manifest_path=resolved_task_manifest_path,
                gate_output_path=gate_output_path,
                env=dict(env_overlay or {}),
            )

            result = self._executor.execute(ctx)
            result.attempts = attempt

            if result.actuals_available:
                any_actuals = True
                cum_input_tokens += result.input_tokens or 0
                cum_output_tokens += result.output_tokens or 0
                cum_cache_creation_input_tokens += result.cache_creation_input_tokens or 0
                cum_cache_read_input_tokens += result.cache_read_input_tokens or 0
                cum_cost_usd += result.cost_usd or 0.0

            # Redefine the result's token/cost fields to mean "cumulative across every
            # attempt of this task so far" — the correct semantic for a task-level result.
            # Degenerates to today's single-attempt values when there's no retry.
            result.actuals_available = any_actuals
            result.input_tokens = cum_input_tokens if any_actuals else None
            result.output_tokens = cum_output_tokens if any_actuals else None
            result.cache_creation_input_tokens = (
                cum_cache_creation_input_tokens if any_actuals else None
            )
            result.cache_read_input_tokens = cum_cache_read_input_tokens if any_actuals else None
            result.cost_usd = cum_cost_usd if any_actuals else None

            last_result = result

            if result.status == "succeeded":
                return result

            # Quota exhaustion is handled by the engine's outer loop (wait + re-run);
            # don't burn retry attempts on it.
            if result.claude_quota_exhausted:
                return result

            logger.warning(
                "Task %s attempt %d/%d failed: %s",
                task.id,
                attempt,
                retry.max_attempts,
                result.error,
            )

            if attempt < retry.max_attempts and retry.backoff_seconds > 0:
                self._sleeper(retry.backoff_seconds)

        # All attempts exhausted
        assert last_result is not None
        return last_result

    # -------------------------------------------------------------------------
    # Dynamic-expansion helpers (Area 2)
    # -------------------------------------------------------------------------

    def _inject(
        self,
        new: list,  # list[TaskSpec]
        workflow: WorkflowSpec,
        state: RunState,
        origin: _TaskOrigin,
        route: str | None = None,
    ) -> None:
        """Merge *new* TaskSpec objects into the live workflow and RunState.

        Raises InjectionError if any id in *new* already exists in the workflow
        (NFR-6 — duplicate-id rejection).

        Parameters
        ----------
        route:
            The emitting task's ``TaskRunState.route`` (LLD §5.5, R4) — inherited
            by every injected task so branch bookkeeping (not_taken tagging,
            breaker counts) stays consistent across dynamic expansion. None when
            the emitter is not on an activated branch (no ``branches`` declared,
            or a pre-routing workflow).
        """
        existing = {t.id for t in workflow.tasks}
        for spec in new:
            if spec.id in existing:
                raise InjectionError(
                    f"Cannot inject task {spec.id!r}: id already exists in the workflow"
                )
            workflow.tasks.append(spec)
            existing.add(spec.id)
            state.injected_tasks.append(spec)
            state.tasks[spec.id] = TaskRunState(origin=origin, route=route)

    def _recompute_order(self, graph, done: set[str]) -> tuple[list[str], int]:
        """Recompute full topological order and return (order, cursor).

        The cursor is set to the index of the first task not yet in *done*
        so the while loop resumes from the right point after injection.
        """
        order = graph.topological_order()
        cursor = 0
        for i, tid in enumerate(order):
            if tid not in done:
                cursor = i
                break
        else:
            # All tasks are done
            cursor = len(order)
        return order, cursor

    def _loop_for_gate(self, workflow: WorkflowSpec, task_id: str) -> LoopSpec | None:
        """Return the LoopSpec whose gate_task_id matches *task_id* (or its iter variant).

        Uses `models.strip_iter_suffix` -- the single shared implementation also used by
        `models._is_structural_task` -- so this and the isolation resolver can never
        independently drift on what counts as "the same loop-gate task across iterations"
        (E-Wk9Tz3 review finding C-1).
        """
        base_id = strip_iter_suffix(task_id)
        for loop in workflow.loops:
            if base_id == loop.gate_task_id:
                return loop
        return None

    def _gate_path_for_iter(self, loop: LoopSpec, iteration: int) -> str:
        """Return the gate_output_path for the given iteration number.

        Iteration 1 uses the un-suffixed authored path.
        Iteration N (N >= 2) uses a path suffixed with ``__iter{N}`` inserted
        before the file extension (or appended if no extension).
        """
        if iteration == 1:
            return loop.gate_output_path
        # Insert suffix before extension, e.g. gate.json -> gate__iter2.json
        path = loop.gate_output_path
        dot = path.rfind(".")
        slash = path.rfind("/")
        if dot > slash:  # has an extension
            return path[:dot] + f"__iter{iteration}" + path[dot:]
        return path + f"__iter{iteration}"

    def _clone_body(
        self, loop: LoopSpec, iter_n: int, workflow: WorkflowSpec
    ) -> list:  # list[TaskSpec]
        """Clone the loop body tasks for iteration *iter_n* (>= 2).

        Rules (HLD §4.5, AC from T-sfdybw):
        - Task ids are suffixed ``__iter{N}``.
        - Intra-body ``depends_on`` are rewritten to suffixed ids.
        - The first task in the clone depends on the last task of the previous iteration.
        - The gate_output_path for the gate task is suffixed to isolate each verdict.
        """
        suffix = f"__iter{iter_n}"
        prev_suffix = f"__iter{iter_n - 1}" if iter_n > 2 else ""
        # Last task id of the previous iteration
        prev_last = loop.body[-1] + prev_suffix

        body_set = set(loop.body)
        clones = []

        for i, tid in enumerate(loop.body):
            base = workflow.task(tid)
            # Rewrite depends_on: intra-body deps get the new suffix; others unchanged
            new_depends_on = [(d + suffix if d in body_set else d) for d in base.depends_on]
            # Chain: first task of this iteration depends on last task of previous iteration
            if i == 0:
                new_depends_on.append(prev_last)

            # Copy all fields; override id and depends_on.
            # Clear inputs and outputs on clones to avoid spurious DAG inferred edges:
            # cloned tasks use the same artifact paths as the original body (they overwrite),
            # but if we keep inputs/outputs the DAG inferred-edge logic would create cycles
            # (e.g. develop__iter2 outputs impl.md -> inferred edge to review which already ran).
            # Execution ordering is fully handled by the rewritten depends_on chain.
            # gate_output_path lives on LoopSpec, not TaskSpec — suffixing is handled
            # via _gate_path_for_iter when the gate verdict is read.
            clone = base.model_copy(
                deep=True,
                update={
                    "id": tid + suffix,
                    "depends_on": new_depends_on,
                    "inputs": [],
                    "outputs": [],
                    "output_manifest": None,
                    # Clones never emit tasks (they are already clones of static body tasks)
                    "emit_tasks": False,
                    "task_manifest_path": None,
                },
            )

            clones.append(clone)

        return clones
