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

This module ships with the six MVP conditions registered (T-q5n7k2, LLD §7):
`task_failures`, `consecutive_failures`, `run_wall_clock_seconds`, `verdict`,
`injected_task_count`, `stop_file` — see the bottom of this module for the
registrations. A workflow that declares a `circuit_breakers` entry whose `condition`
is one of the non-MVP names accepted by the schema (e.g. `failure_ratio`,
`same_task_exhausted`, ...) but not yet implemented here fails fast with
`SpecValidationError` at evaluation time (never a silent no-op) — see
`evaluate_breakers`.
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
from .models import CircuitBreakerSpec, RunState, TrippedBreaker, WorkflowSpec

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


# condition name -> Breaker instance. The six MVP conditions (LLD §7) are registered
# below at import time, so they are active as soon as this module is imported.
# `evaluate_breakers` raises SpecValidationError for any declared `condition` not
# present here (never a silent no-op) — this is how a non-MVP condition name (accepted
# by the schema's full §6 catalog but not yet implemented) is rejected.
BREAKER_REGISTRY: dict[str, Breaker] = {}
BREAKER_REGISTRY["task_failures"] = TaskFailuresBreaker()
BREAKER_REGISTRY["consecutive_failures"] = ConsecutiveFailuresBreaker()
BREAKER_REGISTRY["run_wall_clock_seconds"] = RunWallClockSecondsBreaker()
BREAKER_REGISTRY["verdict"] = VerdictBreaker()
BREAKER_REGISTRY["injected_task_count"] = InjectedTaskCountBreaker()
BREAKER_REGISTRY["stop_file"] = StopFileBreaker()


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
    then). For each spec, looks up `BREAKER_REGISTRY[spec.condition]` and calls
    `.evaluate(spec, ctx)`. A NEW trip (its `spec.id` not already present in
    `state.tripped_breakers`) is recorded via `record_trip()`; an id already latched is
    skipped (no duplicate record) even if it re-trips.

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
        trip = breaker.evaluate(spec, ctx)
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
