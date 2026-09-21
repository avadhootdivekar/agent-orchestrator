"""Pydantic v2 models for the agent orchestrator."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Budget constants (NFR-7 — no magic literals in budget logic)
WINDOW_SECONDS: dict[str, int] = {"minute": 60, "ten_minutes": 600, "hour": 3600}
# Effort-level to --max-turns mapping; keeps spec constants named, not magic literals.
# `--max-turns` is a runaway-loop breaker, not the cost guard (the token budget is),
# so values are generous enough for a real agent to Read instruction + inputs and
# Write outputs with headroom. Too-low caps (e.g. 5) fail flaky before writing output.
# "xhigh" (ADR-0003 decision 2 follow-through, per-task settings) sits above "high" for
# the rare task that genuinely needs a long, many-turn session.
EFFORT_MAX_TURNS: dict[str, int] = {"low": 15, "medium": 30, "high": 60, "xhigh": 120}
# Shared Literal so AgentSpec and TaskSpec can't drift on the allowed effort values.
EffortLevel = Literal["low", "medium", "high", "xhigh"]
DEFAULT_CHARS_PER_TOKEN: int = 4
DEFAULT_PESSIMISM_BUFFER: float = 1.3
DEFAULT_OUTPUT_ALLOWANCE_TOKENS: int = 1000
DEFAULT_429_BACKOFF_SECONDS: int = 60
# Claude usage-quota exhaustion defaults (quota is the 5-hour/daily/weekly session limit)
DEFAULT_QUOTA_POLL_SECONDS: int = 900  # poll interval while waiting for quota reset (15 min)
DEFAULT_QUOTA_MAX_WAIT_SECONDS: int = 21600  # give up after 6 hours of exhaustion by default
# Max independent ready tasks the engine may dispatch concurrently (ADR-0007 D1). 1 = fully
# serial -- byte-identical to the pre-epic engine. Invocation-scoped (ADR-0007 D5): rides the
# same CLI/env/config precedence chain as the quota fields above, never a workflow-spec field.
DEFAULT_MAX_PARALLEL: int = 1

# MVP built-in (re-framed) breaker ids — named, not magic literals (LLD §2.2, §8).
# These identify the pre-existing budget/quota stops once they are routed through the
# breaker trip->record->act pipeline; the underlying events/status/exit-code stay byte-identical.
BUILTIN_BUDGET_EXHAUSTED = "builtin.budget_exhausted"
BUILTIN_BUDGET_UNSATISFIABLE = "builtin.budget_unsatisfiable"
BUILTIN_QUOTA_MAX_WAIT = "builtin.quota_max_wait"
# Bounded-control-file read guard (NFR-1 hardening) — shared by the loop gate, router verdict,
# and verdict-breaker readers so no control JSON read is ever unbounded.
MAX_CONTROL_FILE_BYTES: int = 65536

# Agent-based monitoring & self-healing defaults (E-XyfjuZ) — named, not magic literals.
# Bounds are enforced centrally in engine.py (never trusted to a Monitor implementation) so a
# misbehaving/compromised monitor cannot exceed them regardless of what it recommends.
DEFAULT_MAX_EXTENSIONS_PER_BREAKER: int = 1
DEFAULT_MAX_HEAL_RETRIES_PER_TASK: int = 1
DEFAULT_MAX_MONITOR_CALLS_PER_RUN: int = 10

# ---------------------------------------------------------------------------
# Per-task git isolation & rebase-based integration (E-Wk9Tz3 / HLD §10.2) —
# named constants, no magic literals anywhere downstream (AC-1).
# ---------------------------------------------------------------------------

DEFAULT_VERIFY_TIMEOUT_SECONDS: int = 1800
DEFAULT_INTEGRATION_LOCK_TIMEOUT_SECONDS: int = 1800
# S-6: a regenerate command runs while the per-repo integration lock is held; without
# its own bound a hung command stalls every other task on that repo until
# lock_timeout_seconds.
DEFAULT_REGENERATE_TIMEOUT_SECONDS: int = 120
DEFAULT_HOTSPOTS_PATH: str = ".ao/hotspots.json"
# S-2: force-injected onto the resolver dispatch, UNIONed with the named agent's own
# disallowed_tools -- closed by construction, not by prompt text (cf. ADR-0005). Must
# never default to [] -- an empty default would silently reopen the hole V11 exists to
# close (a test asserts this default is non-empty).
DEFAULT_RESOLVER_DISALLOWED_TOOLS: list[str] = ["WebFetch", "WebSearch"]
# C-3 (as-built security review 2026-09-07): the resolver's NON-CONFIGURABLE tool floor,
# unioned into every T2 dispatch's `AgentSpec.forced_disallowed_tools` by
# `isolation.escalation.resolver_agent_spec` on top of whatever
# `IntegrationSpec.resolver_disallowed_tools` adds. Unlike that (operator-editable) field
# this one cannot be configured away, because the containment it provides is structural
# rather than advisory:
#   - `Bash`  -- a linked worktree shares the MAIN repository's object database, refs,
#     config and hook directory (`--git-common-dir`). A shell in that worktree can rewrite
#     the run's integration ref, poison `.git/rr-cache` so a bad resolution auto-replays in
#     every future run, plant `.git/hooks/pre-commit` in the operator's own checkout, and
#     `git push`. No environment overlay can take a shell back (see `resolver_env`).
#   - `Task`  -- a spawned subagent would carry its own tool policy, laundering around this
#     denial entirely.
#   - `WebFetch`/`WebSearch` -- the egress half of S-2: the resolver reads raw, unreviewed
#     conflict hunks authored by two tasks nobody reviewed against each other.
# The resolver never needs any of these: it edits the conflicted files and stops, and the
# ENGINE stages the result (`Integrator.resume_integration`) -- see merge-resolve.md.
RESOLVER_FORCED_DISALLOWED_TOOLS: list[str] = ["Bash", "Task", "WebFetch", "WebSearch"]
# S-3: globs that must never be swept into an auto-commit. Matched against paths that
# are UNTRACKED at auto-commit time only -- an already-tracked file is the repo
# author's decision, not the engine's.
DEFAULT_COMMIT_DENYLIST: list[str] = [
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "*.p12",
    "id_rsa*",
    "id_dsa*",
    "id_ecdsa*",
    "id_ed25519*",
    "*credentials*.json",
    "*.kdbx",
]

# Isolation mode values (task level: none|worktree|inherit; workflow-default level:
# none|worktree). Named so comparisons never repeat a bare string literal.
ISOLATION_NONE: Literal["none"] = "none"
ISOLATION_WORKTREE: Literal["worktree"] = "worktree"
ISOLATION_INHERIT: Literal["inherit"] = "inherit"

# Conflict-resolution ladder tier names (HLD §8.4/§10.1), cost-ordered.
TIER_AUTO: Literal["auto"] = "auto"
TIER_MECHANICAL: Literal["mechanical"] = "mechanical"
TIER_LLM: Literal["llm"] = "llm"
TIER_RERUN: Literal["rerun"] = "rerun"

# Scheduler overlap-preference values (§9.1), returned by resolve_overlap_preference.
OVERLAP_OFF: Literal["off"] = "off"
OVERLAP_SOFT: Literal["soft"] = "soft"

# integration.on_denylisted_path actions (S-3).
ON_DENYLISTED_FAIL: Literal["fail"] = "fail"
ON_DENYLISTED_WARN: Literal["warn"] = "warn"
ON_DENYLISTED_ALLOW: Literal["allow"] = "allow"

IsolationMode = Literal["none", "worktree", "inherit"]  # task level
WorkflowIsolation = Literal["none", "worktree"]  # workflow default level
ResolverTier = Literal["auto", "mechanical", "llm", "rerun"]
OverlapPreference = Literal["off", "soft"]

DEFAULT_LADDER: list[ResolverTier] = [TIER_AUTO, TIER_MECHANICAL, TIER_LLM, TIER_RERUN]


class RepoRef(BaseModel):
    id: str
    path: str
    role: Literal["primary", "support"] = "support"


class RepoSet(BaseModel):
    description: str = ""
    repos: list[RepoRef]
    workspace_root: str


class RetryPolicy(BaseModel):
    max_attempts: int = 1
    backoff_seconds: float = 0.0


class AgentSpec(BaseModel):
    executor: Literal["claude_cli", "fake"]
    command_template: list[str] = ["claude", "-p", "{prompt}"]
    prompt_template: str = (
        "Follow the instructions in {instruction}. "
        "Input artifacts: {inputs}. Write outputs to: {outputs}. Repos: {repos}."
    )
    context_window: Literal["isolated", "shared"] = "isolated"
    extra_args: list[str] = []
    model: str | None = None  # e.g. "claude-haiku-4-5-20251001"; passed as --model
    effort: EffortLevel | None = None  # → --max-turns via EFFORT_MAX_TURNS
    max_turns: int | None = None  # explicit --max-turns; overrides effort-derived value when set
    # Working directory the agent process runs in. Resolved against the workspace root
    # (reposet.workspace_root) and path-guarded to stay inside it. None -> workspace root.
    # Lets agents' relative output paths (from specs/instructions) resolve deterministically.
    working_dir: str | None = None
    # Opt-in tools to disable for this agent (claude_cli). Default [] = allow all
    # (web/TodoWrite/subagents stay enabled). Passed as `--disallowedTools <names>`;
    # an explicit policy flag in command_template/extra_args wins over this list.
    # See executors.claude_cli.RECOMMENDED_HEADLESS_DISALLOWED_TOOLS for the
    # background-shell set that is meaningless under headless `claude -p`.
    disallowed_tools: list[str] = []
    # C-1 (as-built security review 2026-09-07): a NON-OVERRIDABLE denial set, appended to
    # the child argv unconditionally -- after, and in addition to, any tool-policy flag the
    # agent itself supplies in `command_template`/`extra_args`. `disallowed_tools` above
    # keeps its "an explicit agent flag wins" semantics (correct for an ordinary opt-in
    # denial); this field is the ENGINE's own forced set and is never dropped, so a
    # resolver AgentSpec that names its own `--allowedTools` can no longer silently
    # re-enable what the T2 containment set exists to strip. Engine-populated only (see
    # `isolation.escalation.resolver_agent_spec`); it is not part of the authored workflow
    # surface, and a spec that sets it anyway can only ever ADD denials, never remove one.
    forced_disallowed_tools: list[str] = []


class TaskSpec(BaseModel):
    id: str
    agent: str
    instruction: str
    inputs: list[str] = []
    outputs: list[str] = []
    output_manifest: str | None = None
    depends_on: list[str] = []
    retries: RetryPolicy | None = None
    timeout_seconds: int | None = None
    skip_if_outputs_exist: bool = True
    # Dynamic task injection (FR-7, FR-8, Area 2).
    emit_tasks: bool = False
    task_manifest_path: str | None = None
    # Convergence policy when a task depends on tasks from >1 route (FR-B5).
    # "all" (default): every declared dependency must reach a terminal success state.
    # "any": at least one declared dependency must succeed (used at route re-joins).
    join: Literal["all", "any"] = "all"
    # Per-task overrides of the dispatched agent's model/effort/max_turns (ADR-0003
    # decision 2). None (the default) means "no override" -- fields fall through to the
    # agent's own value, never the reverse (fill-in, not clobber). See
    # `resolve_effective_agent` for the one place this precedence is applied.
    model: str | None = None
    effort: EffortLevel | None = None
    max_turns: int | None = None
    # Per-task git isolation (E-Wk9Tz3 FR-1). "inherit" (default) takes
    # workflow.defaults.isolation; "worktree" runs the task in a private git worktree on
    # branch ao/<run_id>/<task_id> and integrates by squash+rebase; "none" opts a task out
    # even when the workflow default is "worktree". A router/loop-gate/emit_tasks task is
    # always forced to "none" regardless of this value -- see resolve_task_isolation, the
    # ONE place this resolution happens (never re-derive it at a call site).
    isolation: IsolationMode = ISOLATION_INHERIT
    # SOFT hint: glob patterns (workspace-relative -- no leading '/', no '..' segment) this
    # task is expected to modify. Used ONLY to prefer co-scheduling non-overlapping tasks
    # (scheduling.overlap_preference); never a gate, and may be incomplete or wrong. Command/
    # argv fields (verify_command, resolvers.regenerate[].command, commit_denylist,
    # resolver_*) deliberately live only on WorkflowSpec.integration, never here -- so an
    # agent-authored emit_tasks manifest (parsed into TaskSpec alone by
    # artifacts.read_task_manifest) can contribute this mode enum and these advisory globs
    # but nothing that executes (AC-15).
    touches: list[str] = []


def resolve_effective_agent(task: TaskSpec, agent: AgentSpec) -> AgentSpec:
    """Merge *task*'s model/effort/max_turns overrides onto *agent* (ADR-0003 decision 2).

    Precedence at dispatch: task > agent > (whatever the agent already resolved from the
    invocation-level CLI/env/config fill-in chain, see `cli._resolve_run_settings`). Each
    field is independent: an unset task field falls through to the agent's own value
    rather than clobbering it, so a task can pin just `effort` while leaving `model`
    agent-controlled. This is the ONE place the precedence is applied -- callers (the
    engine's dispatch path) must route every `TaskContext.agent` through here rather than
    passing the raw `AgentSpec` so the executor's argv-injection logic never has to know
    about task-level overrides.

    Returns *agent* unchanged (no copy) when the task declares no overrides at all, so the
    common case (no per-task tuning) allocates nothing new.
    """
    if task.model is None and task.effort is None and task.max_turns is None:
        return agent
    return agent.model_copy(
        update={
            "model": task.model if task.model is not None else agent.model,
            "effort": task.effort if task.effort is not None else agent.effort,
            "max_turns": task.max_turns if task.max_turns is not None else agent.max_turns,
        }
    )


class WorkflowDefaults(BaseModel):
    retries: RetryPolicy = RetryPolicy()
    timeout_seconds: int = 1800
    # Workflow-level default isolation mode (E-Wk9Tz3 FR-1). A task's own
    # isolation="inherit" (the TaskSpec default) resolves to this value. Default "none"
    # keeps every pre-epic workflow byte-identical (NFR-2).
    isolation: WorkflowIsolation = ISOLATION_NONE


class Trigger(BaseModel):
    type: Literal["manual", "cron", "event"]
    schedule: str | None = None
    timezone: str = "UTC"
    event: str | None = None


class LoopSpec(BaseModel):
    """Defines a repeating body of tasks driven by a gate verdict (FR-9, FR-10, FR-11)."""

    id: str
    body: list[str]  # ordered task ids forming one iteration
    gate_task_id: str  # body task whose output decides continue/stop
    gate_output_path: str  # path to the gate's JSON control file (per iteration, suffixed on clone)
    gate_field: str = "continue"  # boolean field read from the gate JSON
    max_iterations: int = 5  # hard cap; >= 1


class RateLimit(BaseModel):
    tokens: int
    window: Literal["minute", "ten_minutes", "hour"] | None = None
    window_seconds: int | None = None

    def seconds(self) -> int:
        """Return the window duration in seconds."""
        if self.window_seconds is not None:
            return self.window_seconds
        return WINDOW_SECONDS[self.window]  # type: ignore[index]


class EstimatorConfig(BaseModel):
    chars_per_token: int = DEFAULT_CHARS_PER_TOKEN
    pessimism_buffer: float = DEFAULT_PESSIMISM_BUFFER
    output_allowance_tokens: int = DEFAULT_OUTPUT_ALLOWANCE_TOKENS


class BudgetSpec(BaseModel):
    total_tokens: int | None = None
    rate: RateLimit | None = None
    on_exhaustion: Literal["stop", "wait"] = "stop"
    estimator: EstimatorConfig = EstimatorConfig()


class BudgetCounters(BaseModel):
    """Persisted budget accounting counters inside RunState (FR-9, NFR-3)."""

    consumed_tokens: int = 0
    window_start_epoch: float | None = None
    window_consumed_tokens: int = 0
    charged_estimate: dict[str, int] = {}
    reconciled_tasks: list[str] = []
    # R-1b: "<task_id>#<dispatch_cycle>" entries, one per independently-reconciled dispatch
    # cycle. `DefaultBudgetManager.reconcile()` latches one-shot per `task_id` and runs at
    # the FIRST settle of a completed dispatch -- before the conflict outcome is even
    # known -- so every later T2/T3 reconcile for the SAME task_id is silently a no-op, and
    # `reverse_estimate()` does not un-latch it. Keying by "<task_id>#<dispatch_cycle>"
    # instead makes each redispatch independently gateable, chargeable and reconcilable.
    # This field only adds the persisted key; the `budget.py` logic switch that consumes it
    # is T-En8Hd4's. `reconciled_tasks` is left in place untouched (backward compat).
    reconciled_cycles: list[str] = []


# ---------------------------------------------------------------------------
# Routing (conditional branching / multi-endpoint) — FR-B1, FR-B2, FR-B5.
# ---------------------------------------------------------------------------


class RouteSpec(BaseModel):
    """One named route within a router: entry task ids; cone = their exclusive descendants."""

    entry: list[str]


class RouterSpec(BaseModel):
    """A routing gate: after ``router_task_id`` succeeds, its verdict JSON selects routes."""

    id: str
    router_task_id: str
    verdict_path: str
    verdict_field: str = "routes"
    routes: dict[str, RouteSpec]
    default_route: str | None = None


# ---------------------------------------------------------------------------
# Circuit breakers — FR-CB1..CB5.
# ---------------------------------------------------------------------------

# Conditions wired into the engine registry in this release (§7 of the LLD). The schema's
# `condition` enum additionally accepts the remainder of the HLD §6 catalog so specs can
# reference them ahead of time; CircuitBreakerSpec.condition is typed as plain `str` (not this
# Literal) so those not-yet-implemented names still parse. The breaker registry raises a clean
# SpecValidationError for any condition outside this set (never a silent no-op).
BreakerCondition = Literal[
    "task_failures",
    "consecutive_failures",
    "run_wall_clock_seconds",
    "run_active_seconds",
    "verdict",
    "injected_task_count",
    "stop_file",
]


class CircuitBreakerSpec(BaseModel):
    id: str
    condition: str  # validated against BreakerCondition + the engine registry (cross_validate)
    action: Literal["fail", "stop", "pause"]
    scope: Literal["run"] = "run"
    window: Literal["run"] = "run"
    # float (not int): shared by count-based conditions (task_failures, ...) AND USD-cost
    # conditions (task_cost_usd, run_cost_usd, E-9h3m7k FR-4) which need fractional
    # thresholds (e.g. 2.50). Count-based comparisons (`count >= threshold`) are unaffected
    # by the widened type.
    threshold: float | None = None
    task_id: str | None = None
    verdict_path: str | None = None
    field: str = "halt"
    path: str | None = None
    # Guardrail mode (E-XyfjuZ FR-1): "hard" (default) is today's behavior, byte-identical —
    # never consultable, never extendable by the monitoring subsystem. "recommend" opts this
    # SPECIFIC breaker into the monitor-consult path at the evaluate_breakers boundary
    # (engine.py's Consult Point A): a monitor may extend the threshold (bounded) or halt.
    # Built-in re-framed stops (BUILTIN_BUDGET_EXHAUSTED/BUILTIN_BUDGET_UNSATISFIABLE/
    # BUILTIN_QUOTA_MAX_WAIT) never construct a CircuitBreakerSpec at all (they call
    # record_trip() directly at their own dedicated sites) and so have no `mode` to set —
    # they are unconditionally hard by construction, not by a flag here.
    mode: Literal["hard", "recommend"] = "hard"


class TrippedBreaker(BaseModel):
    """A recorded breaker trip (persisted in RunState.tripped_breakers, FR-CB4)."""

    id: str
    condition: str
    action: str
    at: str  # ISO-8601 UTC
    detail: dict = {}  # e.g. {"failures": 3, "threshold": 3}


class MonitorDecisionRecord(BaseModel):
    """One monitor consult, persisted in RunState.monitor_decisions (E-XyfjuZ FR-6).

    This is the ONLY new persisted field the monitoring subsystem adds to RunState — every
    other piece of monitor bookkeeping (how many times a breaker id has been extended, how
    many heal retries a task id has used, how many monitor calls have been made this run) is
    DERIVED from this list on demand (see count_monitor_breaker_extensions/
    count_monitor_heal_retries/count_monitor_calls_made below), never duplicated into
    separate counters that could drift from the audit trail (Design Decision D9 — mirrors
    this codebase's existing "derive, don't duplicate bookkeeping" convention used by
    breakers.py's `_consecutive_failure_streak`/`_settled_task_active_seconds`).

    One record is appended per ACTUAL consult (a real call to Monitor.decide_breaker_trip/
    decide_task_failure) -- never for a bound/cap-exhausted skip (those never call the
    monitor at all, so there is nothing to record; the engine falls back to the safe default
    silently in that case, observable only via the absence of a corresponding record plus a
    distinct `monitor.cap_exceeded` log event when the cap specifically was the cause).
    """

    at: str  # ISO-8601 UTC
    consult_point: Literal["breaker_trip", "task_failure"]
    subject_id: str  # breaker id (breaker_trip) or task id (task_failure)
    decision: str  # "extend"/"halt" (breaker_trip) or "retry"/"accept_failure" (task_failure)
    monitor: str  # Monitor.name -- "rules" or the configured agent name
    detail: dict = {}  # e.g. {"extend_by_seconds": None, "reason": "..."}


# ---------------------------------------------------------------------------
# Per-task git isolation & rebase-based integration (E-Wk9Tz3, HLD §10.2).
# ---------------------------------------------------------------------------


class RegenerateRule(BaseModel):
    """T1 mechanical resolver: regenerate a conflicted file from *command* (HLD §11 M6)."""

    glob: str
    command: list[str]
    take: Literal["ours", "theirs"] = "theirs"
    # S-6: this command runs while the per-repo integration lock is held (T-Ib5Qy9's
    # locks.py), so without its own bound a hung command stalls every OTHER task on that
    # repo until integration.lock_timeout_seconds -- a much coarser, run-wide bound.
    # C-5: ge=1 matches specs/workflow.schema.json's minimum so pydantic -- the ONLY gate
    # for an installed wheel, per cross_validate's packaging note -- rejects a bogus value
    # too, not just the (unpackaged) JSON Schema.
    timeout_seconds: int = Field(default=DEFAULT_REGENERATE_TIMEOUT_SECONDS, ge=1)


class ResolverConfig(BaseModel):
    """T1 mechanical resolver configuration (HLD §11 M6)."""

    rerere: bool = True
    union: list[str] = []
    regenerate: list[RegenerateRule] = []


class IntegrationSpec(BaseModel):
    """Squash+rebase integration policy for isolated (worktree) tasks (HLD §8, §10.1).

    Every field is optional/defaulted so a workflow that never uses ``isolation: worktree``
    is unaffected by this block's mere presence (NFR-5); V5 warns when it IS configured but
    no task actually resolves to worktree isolation.
    """

    strategy: Literal["rebase", "merge"] = "rebase"  # "merge" reserved; rejected by V1
    branch: str | None = None  # default: ao/<run_id>/integration
    verify_command: list[str] = []  # argv, never a shell string; unset = builtin structural check
    # C-5: ge=... constraints below match specs/workflow.schema.json's `minimum` for each
    # field -- pydantic is the ONLY gate for an installed wheel (schema not packaged), so
    # this parity matters as much for basic numeric bounds as for the V1-V12 rules.
    verify_timeout_seconds: int = Field(default=DEFAULT_VERIFY_TIMEOUT_SECONDS, ge=1)
    ladder: list[ResolverTier] = DEFAULT_LADDER
    resolvers: ResolverConfig = ResolverConfig()
    # S-3: the engine runs `git add -A` + commit in the worktree at task end. Set false to
    # require tasks to commit their own work (the engine then integrates whatever the
    # branch already holds).
    auto_commit: bool = True
    # S-3: globs that must never be swept into an auto-commit, matched against paths that
    # are UNTRACKED at auto-commit time only -- an already-tracked file is the repo
    # author's decision, not this engine's.
    commit_denylist: list[str] = DEFAULT_COMMIT_DENYLIST
    # S-3: what the auto-commit does when an untracked path matches commit_denylist.
    # "fail" (default) aborts integration with a structured error naming the path.
    on_denylisted_path: Literal["fail", "warn", "allow"] = ON_DENYLISTED_FAIL
    resolver_agent: str | None = None  # required when "llm" is in ladder (V2)
    resolver_instruction: str | None = None  # default: builtin merge-resolve.md
    # S-2: force-injected onto the T2 resolver dispatch, UNIONed with the named agent's own
    # disallowed_tools. The resolver reads raw, unreviewed conflict content from two
    # different tasks, so its egress tools are closed BY CONSTRUCTION, not by prompt text
    # (cf. ADR-0005). Must never be emptied while "llm" is in the ladder (V11 fatal).
    resolver_disallowed_tools: list[str] = DEFAULT_RESOLVER_DISALLOWED_TOOLS
    # S-2: for the duration of a T2 dispatch, neutralize the resolver worktree's push path
    # (empty credential.helper, unreachable proxy, GIT_TERMINAL_PROMPT=0) so a hijacked
    # `git push` has nowhere to go.
    resolver_deny_push: bool = True
    # R-4/T-Wl2Bq7 (HLD §12.3, ADR-0013 D8): policy for the per-workspace, cross-process
    # `WorkspaceRunLock` (`isolation/runlock.py`) that serializes the ONE unsafe part of
    # running two isolated `ao` runs against the same workspace concurrently -- fast-
    # forwarding the shared physical checkout (`Orchestrator._sync_checkout`); landing onto
    # each run's own integration ref is already safe by construction via the per-repo
    # `IntegrationLock` + update-ref CAS and needs no help from this field.
    # "require" (default): claim the lock before creating any integration ref; a live
    #   holder degrades this run to `isolation: none` (or fails it outright under
    #   `isolation.strict`) rather than risk an unprotected checkout sync.
    # "skip_sync": still attempts the claim (best-effort, logged if denied) but isolates
    #   regardless of the outcome -- `_sync_checkout` unconditionally skips the fast-
    #   forward for this policy, so no lock is actually needed for correctness.
    # "off": never calls `acquire()` at all -- no lock, no protection against a concurrent
    #   run's checkout sync; only a warning is logged.
    workspace_lock: Literal["require", "skip_sync", "off"] = "require"
    max_resolver_attempts: int = Field(default=1, ge=0)
    max_reruns_per_task: int = Field(default=1, ge=0)
    untracked_outputs: Literal["copy", "fail", "ignore"] = "copy"
    keep_worktrees: Literal["never", "on_failure", "always"] = "on_failure"
    sync_checkout: Literal["on_demand", "never"] = "on_demand"
    commit_message_template: str | None = None
    lock_timeout_seconds: int = Field(default=DEFAULT_INTEGRATION_LOCK_TIMEOUT_SECONDS, ge=1)


class SchedulingSpec(BaseModel):
    """Soft overlap-aware scheduling preference (HLD §9.1, R-5)."""

    # None => derived by resolve_overlap_preference: "soft" iff any task in the workflow
    # resolves to isolation="worktree", else "off". An explicit value here always wins.
    overlap_preference: OverlapPreference | None = None
    hotspots_path: str = DEFAULT_HOTSPOTS_PATH


class WorkflowSpec(BaseModel):
    version: str
    id: str
    name: str = ""
    repo_set: str
    defaults: WorkflowDefaults = WorkflowDefaults()
    budget: BudgetSpec | None = None
    triggers: list[Trigger] = [Trigger(type="manual")]
    tasks: list[TaskSpec]
    loops: list[LoopSpec] = []
    branches: list[RouterSpec] = []
    circuit_breakers: list[CircuitBreakerSpec] = []
    # PATHS to instruction files handed to EVERY task in this workflow, in addition to the
    # task's own `instruction` (E-Ui7Kq2 FR-GI1). Workflow-declared layer of the general-
    # instruction set; the workspace-scoped layers (config file / env / CLI) are merged on
    # top by cli.resolve_general_instructions. Paths only — never contents (NFR-1).
    general_instructions: list[str] = []
    # PATH to this workflow's prompt artifact — the file `ao run --prompt/--prompt-file`
    # writes into before the run starts (E-Ui7Kq2 FR-P1). Declaring it is what makes a
    # workflow "promptable"; a task consumes it by listing the same path in its `inputs`.
    # Per-RUN input, deliberately distinct from `general_instructions` (per-WORKSPACE).
    prompt_path: str | None = None
    # Squash+rebase integration policy for isolated tasks (E-Wk9Tz3 FR-6/FR-7). Absent =>
    # every field takes its documented default, which is a byte-identical no-op for a
    # workflow that never isolates a task (NFR-2/NFR-5).
    integration: IntegrationSpec = IntegrationSpec()
    # Soft overlap-aware scheduling preference (E-Wk9Tz3 FR-10). Absent =>
    # resolve_overlap_preference derives "off" unless the workflow isolates a task.
    scheduling: SchedulingSpec = SchedulingSpec()

    def task(self, task_id: str) -> TaskSpec:
        for t in self.tasks:
            if t.id == task_id:
                return t
        raise KeyError(task_id)


# Suffix used for loop-iteration cloning (mirrors `engine.py::_clone_body` and `spec.py`'s
# own `_ITER_SUFFIX_MARKER`). Defined again here, not imported from either, so `models.py`
# stays import-cycle-free (it sits below both `spec.py` and `engine.py` in the dependency
# graph) while still being the ONE place `strip_iter_suffix` (below) implements the
# suffix-stripping both `_is_structural_task` and `engine.py::_loop_for_gate` need --
# review finding C-1 was exactly this logic having silently drifted between the two.
_ITER_SUFFIX_MARKER = "__iter"


def strip_iter_suffix(task_id: str) -> str:
    """Return *task_id* with any ``__iter<N>`` loop-clone suffix removed.

    A loop body task -- including its gate task -- is re-dispatched every iteration via
    `engine.py::_clone_body`, which suffixes the clone's id ``__iter{N}`` for N>=2;
    iteration 1 keeps the unsuffixed, authored id. This is the ONE place that
    suffix-stripping logic lives: `_is_structural_task` (below) and
    `engine.py::_loop_for_gate` both call it, so they can never again independently drift
    on what counts as "the same loop-gate task across iterations" (C-1).
    """
    if _ITER_SUFFIX_MARKER in task_id:
        return task_id.split(_ITER_SUFFIX_MARKER)[0]
    return task_id


def _is_structural_task(task: TaskSpec, workflow: WorkflowSpec) -> bool:
    """True if *task* is an emit_tasks task, a router task, or a loop-gate task.

    Structural tasks never touch tracked repo files themselves (they emit dynamic tasks,
    decide routing, or gate loop continuation), so they are ALWAYS resolved to
    isolation="none" regardless of what they (or workflow.defaults) declare (HLD §11 M2,
    D6, cross-validation rule V4).

    A loop-gate task is re-dispatched every iteration under an ``__iter<N>``-suffixed id
    (`engine.py::_clone_body`) -- `strip_iter_suffix` is applied before comparing against
    `gate_task_id` so a gate clone at iteration 2+ is recognized too (C-1: an earlier
    version of this branch did a bare equality check, so a workflow with
    `defaults.isolation="worktree"` would correctly force iteration 1's gate task to
    "none" but silently isolate iteration 2+'s clone instead). The router branch below
    intentionally does NOT strip the suffix: `engine.py::_router_for_task` does the same
    bare match with no suffix-handling, so this stays consistent with existing behavior
    (a router task is never itself a loop-body member).
    """
    if task.emit_tasks:
        return True
    if any(router.router_task_id == task.id for router in workflow.branches):
        return True
    base_id = strip_iter_suffix(task.id)
    if any(loop.gate_task_id == base_id for loop in workflow.loops):
        return True
    return False


def _declared_isolation(task: TaskSpec, workflow: WorkflowSpec) -> WorkflowIsolation:
    """*task*'s isolation resolved through "inherit" only -- BEFORE the structural-task
    override.

    Shared by `resolve_task_isolation` (dispatch-time) and `spec.cross_validate`'s V4 rule
    (validate-time) so both compute the identical "what would this resolve to if it
    weren't structural" value from one place, never two independently-drifting copies.
    """
    if task.isolation == ISOLATION_INHERIT:
        return workflow.defaults.isolation
    return task.isolation


def resolve_task_isolation(task: TaskSpec, workflow: WorkflowSpec) -> WorkflowIsolation:
    """The ONE place per-task isolation is resolved (HLD §11 M2, FR-1).

    A router task, a loop-gate task, or an emit_tasks task always resolves to "none" (with
    a single warning when it explicitly asked for "worktree", either directly or via an
    inherited workflow default -- D6/V4). Otherwise "inherit" takes
    `workflow.defaults.isolation`; any other declared value passes through unchanged.

    Callers (the engine's dispatch path, the scheduler, cross_validate) MUST route every
    isolation decision through this function rather than re-deriving it, so a future change
    to the resolution rule cannot silently drift between call sites.
    """
    if _is_structural_task(task, workflow):
        if _declared_isolation(task, workflow) == ISOLATION_WORKTREE:
            logger.warning(
                "Task %s: isolation=%r (declared or inherited from defaults.isolation) has "
                "no effect -- forced to isolation='none' because it is an emit_tasks/"
                "router/loop-gate task",
                task.id,
                ISOLATION_WORKTREE,
            )
        return ISOLATION_NONE
    return _declared_isolation(task, workflow)


def resolve_overlap_preference(workflow: WorkflowSpec) -> OverlapPreference:
    """The ONE place §9.1's derived scheduling default is computed (R-5).

    An explicit `workflow.scheduling.overlap_preference` always wins; otherwise "soft" iff
    any task in the workflow resolves (via `resolve_task_isolation`) to isolation=
    "worktree", else "off" -- so a workflow with no isolation anywhere keeps today's exact
    co-scheduling order (NFR-2). The engine's wave-fill call site and every test must call
    this function rather than re-deriving the default, so they can never disagree.
    """
    if workflow.scheduling.overlap_preference is not None:
        return workflow.scheduling.overlap_preference
    if any(resolve_task_isolation(t, workflow) == ISOLATION_WORKTREE for t in workflow.tasks):
        return OVERLAP_SOFT
    return OVERLAP_OFF


# ---------------------------------------------------------------------------
# Runtime-only models
# ---------------------------------------------------------------------------


class TaskContext(BaseModel):
    """NFR-1 boundary: paths/ids only — NO file contents."""

    run_id: str
    task_id: str
    agent: AgentSpec
    instruction_path: str
    # Resolved absolute paths of the workspace/workflow general instructions that apply to
    # EVERY task (E-Ui7Kq2 FR-GI1). Paths only — NFR-1 safe. Empty list = none configured,
    # which keeps every pre-epic prompt byte-identical.
    general_instruction_paths: list[str] = []
    input_paths: list[str]
    output_paths: list[str]
    output_manifest_path: str | None = None
    dynamic_input_paths: list[str] = []
    repo_paths: dict[str, str]
    timeout_seconds: int
    # Resolved absolute working directory the agent process runs in (its cwd).
    # Defaults to the workspace root; honours AgentSpec.working_dir when set.
    # Paths only — NFR-1 safe. Empty string -> executor lets the OS inherit cwd.
    cwd: str = ""
    # Resolved path where executor writes stdout.txt / stderr.txt (FR-4).
    # Paths only — NFR-1 safe.
    output_dir: str = ""
    # Resolved path where the executor writes the task manifest for emit_tasks tasks (Area 2).
    # Paths only — NFR-1 safe. None when task.emit_tasks is False.
    task_manifest_path: str | None = None
    # Resolved path where the executor writes the gate verdict for loop gate tasks (Area 2).
    # Paths only — NFR-1 safe. None when the task is not a gate task.
    gate_output_path: str | None = None
    # Extra environment variables overlaid on os.environ by the executor (E-Wk9Tz3 FR-4):
    # e.g. AO_ISOLATION/AO_TASK_BRANCH, or isolation.env build-cache variables. Empty dict
    # (the default) means "inherit os.environ wholesale, unchanged" -- the executor does
    # `env=({**os.environ, **ctx.env} if ctx.env else None)`, so the no-isolation path is
    # byte-identical to today (NFR-2). `ClaudeCliExecutor` passes no `env=` to Popen at all
    # today, so this field is the injection point that did not previously exist.
    env: dict[str, str] = {}


class TaskResult(BaseModel):
    task_id: str
    status: Literal["succeeded", "failed", "cancelled", "timed_out"]
    attempts: int
    exit_code: int | None = None
    error: str | None = None
    # Path (directory) where captured stdout/stderr live (FR-5).
    # Engine records this without reading file content (NFR-1).
    output_artifact_path: str | None = None
    # Token accounting fields (T-oh5gl5 / FR-5, FR-8). Cumulative across all retry
    # attempts of this task (engine._execute_with_retry sums per-attempt actuals into
    # these fields before returning) — NOT just the last attempt (E-9h3m7k FR-2).
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_creation_input_tokens: int | None = None
    cache_read_input_tokens: int | None = None
    # Actual USD cost from the Claude CLI's `total_cost_usd` (E-9h3m7k FR-1), cumulative
    # across retry attempts like the token fields above. None when actuals unavailable.
    cost_usd: float | None = None
    actuals_available: bool = False
    provider_rate_limited: bool = False
    provider_retry_after_epoch: float | None = None
    # Set when the Claude CLI output matches _CLAUDE_QUOTA_PATTERN (usage-quota exhaustion).
    # Distinct from provider_rate_limited (API 429) — quota is a session/time-based limit.
    claude_quota_exhausted: bool = False


TaskStatus = Literal[
    "pending",
    "running",
    "succeeded",
    "failed",
    "skipped",
    "cancelled",
    "timed_out",
    # Distinct terminal status for tasks on an unselected route (ADR-RC-001, LLD §0-R1).
    # Never "skipped"+reason: skipped self-heals from should_skip on every run; not_taken is
    # a routing verdict that must survive resume unchanged (source of truth: route_decisions).
    "not_taken",
]


class TaskIntegrationState(BaseModel):
    """Per-task integration bookkeeping (E-Wk9Tz3 HLD §10.3), persisted on ``RunState``
    (never on ``TaskRunState``).

    Deliberately lives on ``RunState.task_integration`` rather than on ``TaskRunState``:
    ``runstate.prepare_resume`` replaces every non-terminal ``TaskRunState`` with a FRESH
    object, which would silently discard integration bookkeeping if it lived there instead.
    """

    isolation: Literal["none", "worktree"] = "none"
    repos: dict[str, str] = {}  # repo_key -> worktree root
    branches: dict[str, str] = {}  # repo_key -> branch name
    base_commits: dict[str, str] = {}  # repo_key -> base sha at worktree creation
    squash_commits: dict[str, str] = {}  # repo_key -> last squash sha
    status: Literal[
        "none",
        "pending",
        "integrating",
        "integrated",
        "conflict_resolver",
        "conflict_rerun",
        "failed",
    ] = "none"
    mode: Literal["normal", "resolve", "rerun"] = "normal"  # what the NEXT dispatch does
    tier_reached: ResolverTier | None = None
    attempts: int = 0
    resolver_attempts: int = 0
    reruns: int = 0
    conflicted_paths: list[str] = []  # paths only (NFR-1)
    verify_status: Literal["not_run", "passed", "failed"] = "not_run"
    last_error: str | None = None


class RunIntegrationState(BaseModel):
    """Run-wide integration bookkeeping (E-Wk9Tz3 HLD §10.3), persisted on ``RunState``."""

    active: bool = False
    # S-5: {"auto": N, "mechanical": N, "llm": N, "rerun": N} -- makes a run's free-tier
    # (specifically rerere) resolution volume visible without a new field later.
    tier_counts: dict[str, int] = {}
    # R-4/T-Wl2Bq7: True while this run holds the per-workspace `WorkspaceRunLock`
    # (`isolation/runlock.py`), claimed by `_activate_integration` per `IntegrationSpec.
    # workspace_lock` and released in `run()`'s `finally`. Drives whether `_sync_checkout`
    # is permitted to fast-forward the shared physical checkout at a barrier/run end.
    workspace_lock_held: bool = False
    branch: str | None = None  # e.g. "ao/<run_id>/integration"
    repos: dict[str, str] = {}  # repo_key -> git common dir
    heads: dict[str, str] = {}  # repo_key -> current integration head
    base_heads: dict[str, str] = {}  # repo_key -> sha the run branched from
    checkout_synced_to: dict[str, str] = {}  # repo_key -> sha the main checkout holds
    degraded_reason: str | None = None  # set when isolation fell back to none


class TaskRunState(BaseModel):
    status: TaskStatus = "pending"
    attempts: int = 0
    started_at: str | None = None
    ended_at: str | None = None
    outputs_present: bool = False
    dynamic_outputs: list[str] = []
    # Mirror of TaskResult.output_artifact_path for traceability (FR-5, §3.4).
    output_artifact_path: str | None = None
    # Provenance: "static" for spec-declared tasks; "injected"/"loop" added by Area 2.
    # Default "static"; Area 2 can set the real value without breaking existing code.
    origin: Literal["static", "injected", "loop"] = "static"
    # "<router_id>:<route_id>" this task belongs to (observability only; FR-CB4).
    route: str | None = None
    # e.g. "router=classify route=bug not selected" — set only when status == "not_taken".
    not_taken_reason: str | None = None
    # Cumulative ACTUAL usage across every retry attempt of this task (E-9h3m7k FR-2),
    # mirrored from the final TaskResult once the task settles. Zero/None until then.
    # Not reset on resume — a resumed task starts a fresh TaskResult (fresh attempts),
    # so these reflect only the attempts of the run that actually produced them.
    cumulative_input_tokens: int = 0
    cumulative_output_tokens: int = 0
    cumulative_cache_creation_input_tokens: int = 0
    cumulative_cache_read_input_tokens: int = 0
    cumulative_cost_usd: float = 0.0
    # R-21: incremented by the main thread exactly once per dispatch; monotonic across
    # requeues (T2/T3 conflict-ladder escalation, self-heal) and resumes. `attempts` cannot
    # serve this purpose: it is ASSIGNED from `result.attempts` (the per-call retry-loop
    # count), not accumulated, so it does not increase monotonically across separate calls
    # to `_run_with_retries` -- a requeue's own internal loop restarts at 1, which would
    # otherwise clobber the original dispatch's `attempt-1` transcript capture directory.
    # Keys the capture directory as `<run_dir>/<task_id>/cycle-<dispatch_cycle>/attempt-<n>/`
    # once the engine wiring (T-En8Hd4) adopts it.
    dispatch_cycle: int = 0


class RunState(BaseModel):
    run_id: str
    workflow_id: str
    repo_set: str
    started_at: str
    updated_at: str
    status: Literal["running", "succeeded", "failed", "cancelled"] = "running"
    tasks: dict[str, TaskRunState] = {}
    # Full specs of every task injected at run time (emit + loop clones), in injection order.
    # Persisted so resume can rebuild the expanded workflow (FR-8, FR-12).
    injected_tasks: list[TaskSpec] = []
    # loop_id -> highest iteration number already materialized (idempotent on resume, FR-12).
    loop_iterations: dict[str, int] = {}
    # Budget accounting counters (T-oh5gl5 / FR-9, NFR-3).
    # default_factory ensures old serialized states without this field load with defaults.
    budget_counters: BudgetCounters = Field(default_factory=BudgetCounters)
    # Source of truth for routing (FR-CB4/CB5, ADR-RC-001): router_id -> selected route ids.
    # Per-task "not_taken" status/route is *derived* from this on activation/resume, never
    # from a fresh verdict re-read (NFR-2 determinism). Defaulted for NFR-5 backward-compat.
    route_decisions: dict[str, list[str]] = {}
    # Every circuit-breaker trip recorded during the run (FR-CB4). Defaulted for NFR-5.
    tripped_breakers: list[TrippedBreaker] = []
    # breaker id -> absolute overridden threshold (native unit: seconds for time-based
    # conditions, USD for cost conditions, etc.), set only via an explicit operator
    # `ao resume --extend-breaker` (E-3JTmVu FR-2a). Empty for every breaker that was never
    # extended -- `evaluate_breakers` falls back to `CircuitBreakerSpec.threshold` in that case,
    # so this is purely additive and never changes behaviour for an untouched breaker. Defaulted
    # for NFR-5 backward-compat (old state.json files predate this field).
    breaker_overrides: dict[str, float] = {}
    # Every agent-based monitor consult made during the run (E-XyfjuZ FR-6) -- the ONLY new
    # persisted field the monitoring subsystem adds. Bound-tracking counters
    # (monitor_breaker_extensions/monitor_heal_retries/monitor_calls_made) are DERIVED from
    # this list on demand (Design Decision D9), never separately persisted. Defaulted for
    # NFR-5 backward-compat (old state.json files predate this field).
    monitor_decisions: list[MonitorDecisionRecord] = []
    # Run-wide + per-task git isolation/integration bookkeeping (E-Wk9Tz3 HLD §10.3).
    # Defaulted for NFR-5 backward-compat (old state.json files predate these fields):
    # `integration.active` is False and `task_integration` is empty, which is exactly the
    # "isolation never happened in this run" state -- correct for every pre-epic run.
    integration: RunIntegrationState = RunIntegrationState()
    task_integration: dict[str, TaskIntegrationState] = {}  # keyed by task id


class RunUsageTotals(BaseModel):
    """Run-wide actual usage, summed over every task's cumulative fields (E-9h3m7k FR-3).

    Deliberately NOT a persisted/incrementally-mutated RunState field: a mutated running
    total risks double-counting on resume (the same double-charge class of bug
    `BudgetCounters.reconciled_tasks` guards against for estimates). Instead this is pure
    and derived on demand from `RunState.tasks[*].cumulative_*` — recomputing it is O(tasks)
    and always resume-safe by construction, with a single source of truth.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    cost_usd: float = 0.0


def compute_run_usage_totals(state: RunState) -> RunUsageTotals:
    """Sum actual usage across every task in *state* (E-9h3m7k FR-3).

    Pure function of `state.tasks` — safe to call at any point (mid-run, on resume, or
    after completion); tasks that haven't settled yet simply contribute zero (their
    `cumulative_*` fields default to 0 / 0.0 until the engine sets them on settle).
    """
    totals = RunUsageTotals()
    for ts in state.tasks.values():
        totals.input_tokens += ts.cumulative_input_tokens
        totals.output_tokens += ts.cumulative_output_tokens
        totals.cache_creation_input_tokens += ts.cumulative_cache_creation_input_tokens
        totals.cache_read_input_tokens += ts.cumulative_cache_read_input_tokens
        totals.cost_usd += ts.cumulative_cost_usd
    return totals


# ---------------------------------------------------------------------------
# Monitor bookkeeping — derived from RunState.monitor_decisions (Design Decision D9,
# E-XyfjuZ). Pure functions, same "derive, don't duplicate persisted bookkeeping" pattern
# as compute_run_usage_totals above and breakers.py's _consecutive_failure_streak /
# _settled_task_active_seconds — resume-safe by construction, single source of truth.
# ---------------------------------------------------------------------------


def compute_run_active_seconds(state: RunState) -> float:
    """Sum ``(ended_at - started_at)`` over every SETTLED task in *state*.

    The "actual time the engine was genuinely busy" measure, as opposed to wall-clock
    elapsed (``updated_at - started_at``), which also counts operator pause/resume gaps.
    A task contributes only once BOTH timestamps are present (the engine sets them together
    on dispatch/settle); pending/running/not_taken tasks contribute 0. In-task waits
    (quota/429/budget retry sleeps) happen inside a task's own window and so DO count — the
    engine really was blocked on that task.

    Pure reconstruction from persisted per-task timestamps, so it yields the same answer
    mid-run, after a resume, or long after the run ended (same "derive, don't duplicate
    bookkeeping" convention as ``compute_run_usage_totals``). Shared by the
    ``run_active_seconds`` circuit breaker and the dashboard's per-run stats.
    """
    total = 0.0
    for ts in state.tasks.values():
        if ts.started_at is None or ts.ended_at is None:
            continue
        started = datetime.fromisoformat(ts.started_at)
        ended = datetime.fromisoformat(ts.ended_at)
        total += (ended - started).total_seconds()
    return total


def count_monitor_breaker_extensions(state: RunState, breaker_id: str) -> int:
    """How many times *breaker_id* has been extended by a monitor consult this run.

    Counts `monitor_decisions` entries for `consult_point == "breaker_trip"`,
    `subject_id == breaker_id`, and `decision == "extend"` — engine.py checks this against
    `max_extensions_per_breaker` BEFORE consulting the monitor again for that breaker id.
    """
    return sum(
        1
        for d in state.monitor_decisions
        if d.consult_point == "breaker_trip"
        and d.subject_id == breaker_id
        and d.decision == "extend"
    )


def count_monitor_heal_retries(state: RunState, task_id: str) -> int:
    """How many heal retries *task_id* has already used this run.

    Counts `monitor_decisions` entries for `consult_point == "task_failure"`,
    `subject_id == task_id`, and `decision == "retry"` — engine.py checks this against
    `max_heal_retries_per_task` BEFORE consulting the monitor again for that task id.
    """
    return sum(
        1
        for d in state.monitor_decisions
        if d.consult_point == "task_failure" and d.subject_id == task_id and d.decision == "retry"
    )


def count_monitor_calls_made(state: RunState) -> int:
    """Total number of ACTUAL monitor consults made this run (both consult points combined).

    Every `monitor_decisions` entry corresponds to exactly one real `Monitor.decide_*` call
    (bound/cap-exhausted skips never append a record — see `MonitorDecisionRecord`'s
    docstring) — so this is simply the list length. engine.py checks this against
    `max_monitor_calls_per_run` BEFORE every consult, across both consult points.
    """
    return len(state.monitor_decisions)
