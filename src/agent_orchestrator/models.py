"""Pydantic v2 models for the agent orchestrator."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

# Budget constants (NFR-7 — no magic literals in budget logic)
WINDOW_SECONDS: dict[str, int] = {"minute": 60, "ten_minutes": 600, "hour": 3600}
# Effort-level to --max-turns mapping; keeps spec constants named, not magic literals.
# `--max-turns` is a runaway-loop breaker, not the cost guard (the token budget is),
# so values are generous enough for a real agent to Read instruction + inputs and
# Write outputs with headroom. Too-low caps (e.g. 5) fail flaky before writing output.
EFFORT_MAX_TURNS: dict[str, int] = {"low": 15, "medium": 30, "high": 60}
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
    effort: Literal["low", "medium", "high"] | None = None  # → --max-turns via EFFORT_MAX_TURNS
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


class WorkflowDefaults(BaseModel):
    retries: RetryPolicy = RetryPolicy()
    timeout_seconds: int = 1800


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

    def task(self, task_id: str) -> TaskSpec:
        for t in self.tasks:
            if t.id == task_id:
                return t
        raise KeyError(task_id)


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
