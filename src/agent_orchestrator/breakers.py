"""Pluggable circuit-breaker framework (T-x8v4d3, epic E-rc7k2v).

Mirrors the Executor/BudgetManager DI style already used in the engine: a small ABC
(`Breaker`) plus a name -> instance registry (`BREAKER_REGISTRY`), evaluated by a pure
function (`evaluate_breakers`) that the engine calls at each task boundary.

Design invariants (LLD §6):
- `BreakerContext` carries paths/ids/counters only — NFR-1: breakers never see payload
  content, only `RunState` (ids/statuses/counters) and an `ArtifactStore` for bounded
  existence/read checks (verdict/stop_file conditions, landed in T-q5n7k2).
- Breakers are pure w.r.t. the injected clock: `ctx.clock_epoch` is already resolved by
  the caller: a `Breaker.evaluate()` implementation must never call `time.time()`.
- A trip is latched by `CircuitBreakerSpec.id` — recorded at most once per id, ever
  (re-evaluating an already-tripped id on a later boundary is a no-op).
- `record_trip()` is deliberately a standalone function (not inlined in the evaluation
  loop) so a later re-frame of the pre-existing budget/quota stops (T-r3j9b6, LLD §8.2's
  `trip_builtin`) can call the exact same recording primitive without going through the
  full registry-driven evaluation loop.

This module ships with nine conditions registered: the six MVP conditions
(T-q5n7k2, LLD §7) — `task_failures`, `consecutive_failures`,
`run_wall_clock_seconds`, `verdict`, `injected_task_count`, `stop_file` — plus two
actual-cost conditions added by E-9h3m7k FR-4: `task_cost_usd`, `run_cost_usd` — plus
`run_active_seconds` added by E-3JTmVu FR-1 (sums settled-task execution time only,
immune to operator pause/resume gaps — see `RunActiveSecondsBreaker`). See the bottom
of this module for the registrations. A workflow that declares a `circuit_breakers`
entry whose `condition` is one of the non-MVP names accepted by the schema (e.g.
`failure_ratio`, `same_task_exhausted`, `projected_cost_exceeds`, ...) but not yet
implemented here fails fast with `SpecValidationError` at evaluation time (never a
silent no-op) — see `evaluate_breakers`.

`task_cost_usd`/`run_cost_usd` are distinct from the schema's reserved
`projected_cost_exceeds` name: that one is a pre-flight ESTIMATE check tied to
`budget.py`/`estimator.py` (already reframed onto `record_trip()` by T-r3j9b6). These
two trip on ACTUAL dollars spent (`TaskRunState.cumulative_cost_usd`, sourced from the
Claude CLI's `total_cost_usd`), evaluated after the fact at each task boundary like
every other condition in this module.

E-3JTmVu FR-2 additionally added a scoped, resume-time extension mechanism:
`RunState.breaker_overrides` (id -> operator-overridden threshold) is resolved
centrally in `evaluate_breakers` (never per-Breaker-subclass), and
`apply_breaker_extension()` (standalone, same style as `record_trip()`) lets `ao
resume --extend-breaker <id>` bump a SPECIFIC already-tripped breaker's threshold and
un-latch it — addressing the always-latched gap flagged by T-t4m8x1, without a
blanket un-latch-everything-on-resume that would regress every other breaker's
existing resumability.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from datetime import datetime
from logging import LoggerAdapter
from typing import Literal

from pydantic import BaseModel, ConfigDict

from .artifacts import ArtifactStore, read_bool_field
from .errors import SpecValidationError
from .models import (
    CircuitBreakerSpec,
    RunState,
    TrippedBreaker,
    WorkflowSpec,
    compute_run_active_seconds,
    compute_run_usage_totals,
)

# Runtime action a tripped breaker triggers. Mirrors CircuitBreakerSpec.action (run scope
# only, MVP — see ADR-RC-004 / LLD §6.4).
Action = Literal["fail", "stop", "pause"]


class BreakerContext(BaseModel):
    """Everything a `Breaker.evaluate()` call needs — paths/ids/counters only (NFR-1).

    `store` is an `ArtifactStore` ABC instance (not a pydantic model), so
    `arbitrary_types_allowed` is required; it is used only for bounded existence/size
    checks (e.g. `stop_file`'s `store.exists(...)`), never content reads.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    state: RunState
    clock_epoch: float
    store: ArtifactStore


class TripResult(BaseModel):
    """Returned by `Breaker.evaluate()` when a breaker's condition has tripped."""

    detail: dict = {}


class Breaker(ABC):
    """Pluggable breaker condition (mirrors the `Executor` / `BudgetManager` DI style).

    Implementations must be pure w.r.t. the injected clock: read `ctx.clock_epoch` for
    any time math, never call `time.time()` directly (determinism, NFR-2-style).
    """

    condition: str

    @abstractmethod
    def evaluate(self, spec: CircuitBreakerSpec, ctx: BreakerContext) -> TripResult | None:
        """Return a `TripResult(detail={...})` when *spec*'s condition has tripped, else None."""
        ...


# ---------------------------------------------------------------------------
# The six MVP breaker conditions (T-q5n7k2, LLD §7 — exact trip logic table)
# ---------------------------------------------------------------------------
#
# All six are defensive about an unset `spec.threshold`/`spec.task_id`/`spec.verdict_path`/
# `spec.path`: the JSON schema's conditional `required` (LLD §2.1) guarantees these are
# populated for a spec loaded via `ao validate`/`load_workflow`, but `CircuitBreakerSpec`
# itself keeps them `Optional` because the fields are shared across conditions (a `verdict`
# spec has no `threshold`, a `task_failures` spec has no `task_id`, etc.). A breaker built
# directly (bypassing schema validation, e.g. in a unit test) with a required field left
# unset simply never trips rather than raising a `TypeError` mid-comparison — the registry
# framework (`evaluate_breakers`) is the layer responsible for rejecting bad specs.


def _consecutive_failure_streak(state: RunState) -> int:
    """Return the trailing run-length of failed/timed-out *settled* tasks.

    Completion order is derived purely from `TaskRunState.ended_at` (parsed as ISO-8601),
    excluding any task that hasn't settled yet (`ended_at is None` — this also excludes
    `not_taken`/`pending`/`running` tasks, since only the engine's settle path sets
    `ended_at`). This is intentionally a pure function of `state.tasks` with no extra
    persisted bookkeeping (LLD §7 note): it reconstructs identically whether called mid-run
    or freshly after a resume, because `ended_at` already survives serialization.
    """
    settled = sorted(
        (
            (datetime.fromisoformat(ts.ended_at), ts)
            for ts in state.tasks.values()
            if ts.ended_at is not None
        ),
        key=lambda pair: pair[0],
    )
    streak = 0
    for _, ts in reversed(settled):
        if ts.status in ("failed", "timed_out"):
            streak += 1
        else:
            break
    return streak


class TaskFailuresBreaker(Breaker):
    """`task_failures`: total failed/timed-out task count across the whole run."""

    condition = "task_failures"

    def evaluate(self, spec: CircuitBreakerSpec, ctx: BreakerContext) -> TripResult | None:
        threshold = spec.threshold
        if threshold is None:
            return None
        failures = sum(1 for ts in ctx.state.tasks.values() if ts.status in ("failed", "timed_out"))
        if failures >= threshold:
            return TripResult(detail={"failures": failures, "threshold": threshold})
        return None


class ConsecutiveFailuresBreaker(Breaker):
    """`consecutive_failures`: trailing run of failed/timed-out tasks in completion order.

    Resets to 0 the moment a settled task's streak is broken by any other terminal
    status (success, skip, cancel) — see `_consecutive_failure_streak`.
    """

    condition = "consecutive_failures"

    def evaluate(self, spec: CircuitBreakerSpec, ctx: BreakerContext) -> TripResult | None:
        threshold = spec.threshold
        if threshold is None:
            return None
        streak = _consecutive_failure_streak(ctx.state)
        if streak >= threshold:
            return TripResult(detail={"streak": streak, "threshold": threshold})
        return None


class TaskCostUsdBreaker(Breaker):
    """`task_cost_usd`: any single task's cumulative ACTUAL cost exceeds threshold.

    Runaway-task guard (E-9h3m7k FR-4) — e.g. a workflow config sets `threshold: 3` to
    cap any one task at $3 regardless of which task it is (mirrors `task_failures`'s
    "across the whole run" shape, not a specific `task_id` like `verdict`). Scans
    `TaskRunState.cumulative_cost_usd`, which is the SUM across every retry attempt of
    that task (`engine._run_with_retries`), so a task that burns $1 on two failed
    attempts before a $1.50 success trips at cumulative $2.50, not just the winning
    attempt's cost.
    """

    condition = "task_cost_usd"

    def evaluate(self, spec: CircuitBreakerSpec, ctx: BreakerContext) -> TripResult | None:
        threshold = spec.threshold
        if threshold is None:
            return None
        for task_id, ts in ctx.state.tasks.items():
            if ts.cumulative_cost_usd >= threshold:
                return TripResult(
                    detail={
                        "task_id": task_id,
                        "cost_usd": ts.cumulative_cost_usd,
                        "threshold": threshold,
                    }
                )
        return None


class RunCostUsdBreaker(Breaker):
    """`run_cost_usd`: run-wide cumulative ACTUAL cost (all tasks) exceeds threshold.

    E-9h3m7k FR-4 — the run-level counterpart to `task_cost_usd`. Reuses
    `compute_run_usage_totals` (also the CLI's totals-line source) rather than summing
    `ctx.state.tasks` locally, so the breaker and the displayed total can never disagree.
    """

    condition = "run_cost_usd"

    def evaluate(self, spec: CircuitBreakerSpec, ctx: BreakerContext) -> TripResult | None:
        threshold = spec.threshold
        if threshold is None:
            return None
        total_cost_usd = compute_run_usage_totals(ctx.state).cost_usd
        if total_cost_usd >= threshold:
            return TripResult(detail={"cost_usd": total_cost_usd, "threshold": threshold})
        return None


class RunWallClockSecondsBreaker(Breaker):
    """`run_wall_clock_seconds`: elapsed run time since `RunState.started_at`.

    Measured from the *original* run start (not resume time), so a run resumed long
    after the deadline trips immediately at the first post-resume boundary — this is the
    documented "deadline" semantic (LLD §2.2), not a "time actively running" semantic.
    Uses only `ctx.clock_epoch` (the framework's injected clock) — never calls
    `time.time()`/`datetime.now()` directly (determinism, NFR-2-style).
    """

    condition = "run_wall_clock_seconds"

    def evaluate(self, spec: CircuitBreakerSpec, ctx: BreakerContext) -> TripResult | None:
        threshold = spec.threshold
        if threshold is None:
            return None
        started_epoch = datetime.fromisoformat(ctx.state.started_at).timestamp()
        elapsed = ctx.clock_epoch - started_epoch
        if elapsed >= threshold:
            return TripResult(detail={"elapsed": elapsed, "threshold": threshold})
        return None


def _settled_task_active_seconds(state: RunState) -> float:
    """Sum `(ended_at - started_at)` over every SETTLED task in `state.tasks`.

    Thin alias for `models.compute_run_active_seconds`, which is where this logic now lives
    so the dashboard's per-run "actual time" stat and this breaker cannot drift apart
    (CLAUDE.md's DRY rule). Kept as a module-private name because `RunActiveSecondsBreaker`
    and this module's tests refer to it.
    """
    return compute_run_active_seconds(state)


class RunActiveSecondsBreaker(Breaker):
    """`run_active_seconds`: total real time actually spent executing/retrying tasks.

    Distinct from `run_wall_clock_seconds` (which measures from the run's ORIGINAL
    `started_at` -- a "deadline" semantic that counts any operator pause/resume gap as
    elapsed): this condition sums only settled-task `(ended_at - started_at)` deltas
    (`_settled_task_active_seconds`), so a run paused for hours/days and resumed does NOT
    count that gap toward elapsed -- only genuine task execution/retry time does. Both
    conditions coexist; a workflow author picks whichever semantic fits (E-3JTmVu FR-1).

    Takes `ctx` for signature consistency with every other `Breaker`, but does not actually
    need `ctx.clock_epoch` -- there is no "currently running task" partial contribution to
    add, since breaker evaluation only ever happens right after a task settles (engine.py's
    `evaluate_breakers` call site, immediately after `self._runstate.save(state)`), so there
    is never a still-running task at evaluation time.
    """

    condition = "run_active_seconds"

    def evaluate(self, spec: CircuitBreakerSpec, ctx: BreakerContext) -> TripResult | None:
        threshold = spec.threshold
        if threshold is None:
            return None
        elapsed = _settled_task_active_seconds(ctx.state)
        if elapsed >= threshold:
            return TripResult(detail={"elapsed": elapsed, "threshold": threshold})
        return None


class VerdictBreaker(Breaker):
    """`verdict`: a named task's succeeded verdict file has `field == True`.

    Only reads the control file once `spec.task_id`'s task is settled `succeeded` —
    short-circuits `None` (no trip) beforehand so this never reads a verdict file before
    the task that writes it has actually finished (e.g. mid-run while it's still
    `pending`/`running`, or if it ended `failed`). Uses the shared, bounded
    `read_bool_field` reader (§3 of the LLD) — no second ad-hoc JSON reader.

    A malformed/missing/oversized verdict file raises `ControlFileError` (a subclass of
    `OrchestratorError`) out of `read_bool_field`. That is deliberately NOT caught here:
    once the named task has genuinely succeeded, an unreadable verdict file is a real
    spec/artifact defect, not a "not yet tripped" case, and `engine.py`'s
    `evaluate_breakers` call site has no local try/except around it (by design — see
    engine.py ~:637) so the error propagates to the CLI's existing top-level
    `except OrchestratorError` handler for a clean, non-zero-exit failure instead of
    being silently swallowed into "never trips" (CLAUDE.md: never swallow errors).
    """

    condition = "verdict"

    def evaluate(self, spec: CircuitBreakerSpec, ctx: BreakerContext) -> TripResult | None:
        task_id = spec.task_id
        verdict_path = spec.verdict_path
        if task_id is None or verdict_path is None:
            return None
        task_state = ctx.state.tasks.get(task_id)
        if task_state is None or task_state.status != "succeeded":
            return None
        if read_bool_field(ctx.store, verdict_path, spec.field):
            return TripResult(detail={"task_id": task_id, "field": spec.field})
        return None


class InjectedTaskCountBreaker(Breaker):
    """`injected_task_count`: total dynamically-injected task count across the run.

    Counts `len(state.injected_tasks)`, which already includes nested-emitted tasks
    (tasks injected by a task that was itself injected) by construction — `engine.py`
    appends every emit/loop-injected `TaskSpec` to this single run-level list regardless
    of nesting depth, so no extra depth-tracking is needed here. This is the E2 enabler
    epic `E-gd8m4x` consumes for its runaway fan-out cap (`injection_depth` itself is a
    separate, non-MVP condition — schema-declared, not registered).
    """

    condition = "injected_task_count"

    def evaluate(self, spec: CircuitBreakerSpec, ctx: BreakerContext) -> TripResult | None:
        threshold = spec.threshold
        if threshold is None:
            return None
        count = len(ctx.state.injected_tasks)
        if count >= threshold:
            return TripResult(detail={"count": count, "threshold": threshold})
        return None


class StopFileBreaker(Breaker):
    """`stop_file`: an operator/external kill-switch file exists.

    Existence-only (`ArtifactStore.exists`) — never reads content (NFR-1). A path that
    escapes the workspace root makes `exists()` return False (the store swallows
    `ArtifactPathError` internally), so a traversal path simply never trips rather than
    raising here; `ao validate` is the layer responsible for rejecting such a path
    statically (LLD §11).
    """

    condition = "stop_file"

    def evaluate(self, spec: CircuitBreakerSpec, ctx: BreakerContext) -> TripResult | None:
        path = spec.path
        if path is None:
            return None
        if ctx.store.exists(path):
            return TripResult(detail={"path": path})
        return None


# condition name -> Breaker instance. The six MVP conditions (LLD §7) plus the two
# actual-cost conditions (E-9h3m7k FR-4) are registered below at import time, so they
# are active as soon as this module is imported. `evaluate_breakers` raises
# SpecValidationError for any declared `condition` not present here (never a silent
# no-op) — this is how a non-MVP condition name (accepted by the schema's full §6
# catalog but not yet implemented) is rejected.
BREAKER_REGISTRY: dict[str, Breaker] = {}
BREAKER_REGISTRY["task_failures"] = TaskFailuresBreaker()
BREAKER_REGISTRY["consecutive_failures"] = ConsecutiveFailuresBreaker()
BREAKER_REGISTRY["run_wall_clock_seconds"] = RunWallClockSecondsBreaker()
BREAKER_REGISTRY["run_active_seconds"] = RunActiveSecondsBreaker()
BREAKER_REGISTRY["verdict"] = VerdictBreaker()
BREAKER_REGISTRY["injected_task_count"] = InjectedTaskCountBreaker()
BREAKER_REGISTRY["stop_file"] = StopFileBreaker()
BREAKER_REGISTRY["task_cost_usd"] = TaskCostUsdBreaker()
BREAKER_REGISTRY["run_cost_usd"] = RunCostUsdBreaker()


def record_trip(
    state: RunState,
    breaker_id: str,
    condition: str,
    action: str,
    detail: dict,
    clock: Callable[[], datetime],
    run_log: LoggerAdapter,
) -> TrippedBreaker:
    """Append a `TrippedBreaker` to `state.tripped_breakers` and emit `breaker.trip`.

    Pure recording primitive: does not itself persist `state` (the caller owns
    persistence, matching the engine's existing `self._runstate.save(state)` convention
    elsewhere in the run loop) and does not decide *whether* to record — callers are
    responsible for the latch check (an id must be recorded at most once, ever).

    Deliberately reusable outside the registry-driven `evaluate_breakers` loop: T-r3j9b6
    re-frames the pre-existing budget/quota stops to call this exact function (LLD §8.2's
    `trip_builtin`) so both paths produce byte-identical `TrippedBreaker` records and
    `breaker.trip` events.
    """
    rec = TrippedBreaker(
        id=breaker_id,
        condition=condition,
        action=action,
        at=clock().isoformat(),
        detail=detail,
    )
    state.tripped_breakers.append(rec)
    run_log.warning(
        "breaker.trip",
        extra={
            "event": "breaker.trip",
            "breaker_id": breaker_id,
            "condition": condition,
            "action": action,
            "detail": detail,
        },
    )
    return rec


def apply_breaker_extension(
    state: RunState,
    spec: CircuitBreakerSpec,
    *,
    extend_by_seconds: float | None,
    extend_by_same: bool,
    clock: Callable[[], datetime],
    run_log: LoggerAdapter,
) -> float:
    """Bump *spec*'s effective threshold and un-latch it (E-3JTmVu FR-2b).

    Standalone, reusable function -- same style/signature shape as `record_trip` (required
    *clock*/*run_log*, caller owns both persistence of *state* and logging infra) -- so `ao
    resume --extend-breaker` (cli.py) can call it directly without going through the full
    registry-driven `evaluate_breakers` loop. Addresses the T-t4m8x1-documented gap directly,
    but SCOPED -- only the named *spec.id*, not a blanket un-latch-everything-on-resume (which
    would regress the existing, intentional "resumed run doesn't immediately re-trip on a
    still-true condition" resumability behaviour for every OTHER breaker).

    Steps:
      (a) `current_effective = state.breaker_overrides.get(spec.id, spec.threshold)`.
      (b) `new_threshold = current_effective + (extend_by_seconds if given else spec.threshold)`
          -- "extend by the same amount set at startup" means adding the ORIGINAL
          `spec.threshold` again, not the current effective value.
      (c) `state.breaker_overrides[spec.id] = new_threshold`.
      (d) Remove any existing `TrippedBreaker` record(s) for *spec.id* from
          `state.tripped_breakers` -- un-latches it so `evaluate_breakers`'s `already_tripped`
          set no longer contains it and it becomes eligible to trip again later if the new,
          extended threshold is ALSO exceeded.
      (e) Log a structured `breaker.extend` event (mirrors `record_trip`'s logging style).
      (f) Return *new_threshold* for the caller (e.g. the CLI) to print.

    Raises `SpecValidationError` if both `extend_by_seconds` and `extend_by_same` are given, or
    neither is given -- exactly one is required. Raises `SpecValidationError` if
    `extend_by_seconds` is given but not strictly positive (a zero/negative "extension" would
    silently shrink or leave unchanged the effective threshold, defeating the point of
    extending and bypassing the schema's own `exclusiveMinimum: 0` invariant on thresholds).
    Raises `SpecValidationError` if *spec.threshold* is unset (a breaker with no threshold has
    nothing to extend from).
    """
    if (extend_by_seconds is not None) == extend_by_same:
        raise SpecValidationError(
            "apply_breaker_extension: exactly one of extend_by_seconds/extend_by_same is "
            "required (both or neither were given)",
            path=f"circuit_breakers.{spec.id}",
        )
    if extend_by_seconds is not None and extend_by_seconds <= 0:
        raise SpecValidationError(
            f"apply_breaker_extension: extend_by_seconds must be > 0, got {extend_by_seconds!r}",
            path=f"circuit_breakers.{spec.id}",
        )
    if spec.threshold is None:
        raise SpecValidationError(
            f"Circuit breaker {spec.id!r} has no threshold to extend",
            path=f"circuit_breakers.{spec.id}.threshold",
        )

    current_effective = state.breaker_overrides.get(spec.id, spec.threshold)
    delta = extend_by_seconds if extend_by_seconds is not None else spec.threshold
    new_threshold = current_effective + delta

    state.breaker_overrides[spec.id] = new_threshold
    state.tripped_breakers = [tb for tb in state.tripped_breakers if tb.id != spec.id]

    run_log.warning(
        "breaker.extend",
        extra={
            "event": "breaker.extend",
            "breaker_id": spec.id,
            "old_threshold": current_effective,
            "new_threshold": new_threshold,
            "at": clock().isoformat(),
        },
    )
    return new_threshold


def map_action(action: str) -> Action:
    """Map a `CircuitBreakerSpec.action` value to the runtime effect it triggers.

    Deliberate MVP simplification (ADR-RC-004, LLD §6.4): `fail`/`stop`/`pause` all map
    to the *same* runtime effect — terminate the run on resumable `status="failed"`. Kept
    as an explicit function (not a passthrough) so the mapping stays a single, greppable
    place to change if/when `stop`/`pause` grow distinct `RunState.status` values
    (explicitly non-MVP; do not add new status literals here).
    """
    if action not in ("fail", "stop", "pause"):
        raise SpecValidationError(f"Unknown circuit-breaker action: {action!r}", path="action")
    return action  # type: ignore[return-value]  # narrowed by the check above


def evaluate_breakers(
    workflow: WorkflowSpec,
    state: RunState,
    clock: Callable[[], datetime],
    store: ArtifactStore,
    run_log: LoggerAdapter,
    builtin_specs: list[CircuitBreakerSpec] | None = None,
) -> Action | None:
    """Evaluate declared + built-in breakers at a task boundary (trip -> record -> act).

    Evaluation order is deterministic: `workflow.circuit_breakers` in declared order,
    then *builtin_specs* (the extension point T-r3j9b6 will populate; empty/None until
    then). For each spec, the EFFECTIVE threshold is resolved once, centrally, as
    `state.breaker_overrides.get(spec.id, spec.threshold)` (E-3JTmVu FR-2c) -- a spec with no
    override entry evaluates against its own unchanged `spec.threshold`, byte-identical to
    before overrides existed. When an override is present, a `spec.model_copy(update=
    {"threshold": effective_threshold})` is passed to `.evaluate()` instead of the original
    spec, so every `Breaker` subclass (current and future) honours an operator's `ao resume
    --extend-breaker` bump uniformly, with zero per-condition special-casing. Looks up
    `BREAKER_REGISTRY[spec.condition]` and calls `.evaluate(effective_spec, ctx)`. A NEW trip
    (its `spec.id` not already present in `state.tripped_breakers`) is recorded via
    `record_trip()`; an id already latched is skipped (no duplicate record) even if it
    re-trips -- unless an operator has explicitly un-latched it via `apply_breaker_extension`
    (FR-2b), which removes it from `state.tripped_breakers` so it becomes eligible again.

    If one or more breakers trip NEW this boundary, the FIRST one in evaluation order
    owns the returned action — deterministic, documented (LLD §6.3/§6.4). Callers must
    persist `state` after calling this (mirrors the engine's existing
    `self._runstate.save(state)` convention; this function only mutates the in-memory
    object) and, when an Action is returned, execute it: set
    `state.status="failed"` and stop dispatching (all three actions land on the same
    resumable status per ADR-RC-004 — the audit distinction lives in
    `tripped_breakers[].action`).

    Returns None if nothing tripped NEW this boundary (including the case where
    `workflow.circuit_breakers` and *builtin_specs* are both empty — the common case
    today, since T-q5n7k2/T-r3j9b6 have not landed).

    Byte-identical no-op guarantee: when there are no specs to evaluate, *clock* is
    never called at all. This matters beyond a micro-optimisation — several existing
    engine tests inject a "stepping" clock that advances one step per call and assert
    exact call counts / derived sleep durations; an unconditional `clock()` call here
    would silently consume a step on every task boundary and desync those assertions
    even though no breaker is declared.
    """
    specs = [*workflow.circuit_breakers, *(builtin_specs or [])]
    if not specs:
        return None

    already_tripped = {tb.id for tb in state.tripped_breakers}
    newly_triggered_actions: list[str] = []
    clock_epoch = clock().timestamp()
    ctx = BreakerContext(state=state, clock_epoch=clock_epoch, store=store)

    for spec in specs:
        if spec.condition not in BREAKER_REGISTRY:
            raise SpecValidationError(
                f"Circuit breaker {spec.id!r}: no breaker registered for condition "
                f"{spec.condition!r}",
                path=f"circuit_breakers.{spec.id}.condition",
            )
        breaker = BREAKER_REGISTRY[spec.condition]
        # Centralized override resolution (FR-2c): a spec with no override entry evaluates
        # against its own unchanged threshold (byte-identical to pre-override behaviour); an
        # extended id evaluates against the operator-bumped value instead. Resolved here ONCE
        # so no individual Breaker subclass needs to know about breaker_overrides.
        effective_threshold = state.breaker_overrides.get(spec.id, spec.threshold)
        effective_spec = (
            spec
            if effective_threshold == spec.threshold
            else spec.model_copy(update={"threshold": effective_threshold})
        )
        trip = breaker.evaluate(effective_spec, ctx)
        if trip is None:
            continue
        if spec.id in already_tripped:
            continue  # latch: record at most once per id, ever

        record_trip(
            state=state,
            breaker_id=spec.id,
            condition=spec.condition,
            action=spec.action,
            detail=trip.detail,
            clock=clock,
            run_log=run_log,
        )
        already_tripped.add(spec.id)
        newly_triggered_actions.append(spec.action)

    if not newly_triggered_actions:
        return None
    return map_action(newly_triggered_actions[0])
