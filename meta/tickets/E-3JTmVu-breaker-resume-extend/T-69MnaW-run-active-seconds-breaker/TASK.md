# TASK: T-69MnaW-run-active-seconds-breaker

## Metadata
- Task ID: `T-69MnaW-run-active-seconds-breaker`
- Epic ID: `E-3JTmVu-breaker-resume-extend`
- Owner: developer
- Created: 2026-07-14
- Last Updated: 2026-07-14
- Status: Done
- Estimate: 1 day

## Requirements Mapping
- Requirement IDs: FR-1, NFR-2

## Description
Add a new breaker condition `run_active_seconds`, immune to operator pause/resume gaps by
construction (unlike `run_wall_clock_seconds`, which measures from the run's original
`started_at`). Elapsed = sum of `(ts.ended_at - ts.started_at)` over every SETTLED task in
`state.tasks` (both timestamps present, ISO-8601 parsed). This naturally excludes any gap
between a task's `ended_at` and the next task's `started_at` (e.g. an operator-initiated pause),
while still including in-task waits (quota/429/budget retry sleeps happen inside a task's own
`started_at..ended_at` window). No "currently running task" partial contribution is needed —
breaker evaluation only ever happens right after a task settles (`engine.py`'s
`evaluate_breakers` call site, right after `self._runstate.save(state)`), so summing only
settled tasks is complete.

## Acceptance Criteria
1. `"run_active_seconds"` added to the schema's `condition` enum in
   `specs/workflow.schema.json`, with `threshold` in its conditional-required list (same as
   `run_wall_clock_seconds`).
2. `RunActiveSecondsBreaker(Breaker)` registered in `BREAKER_REGISTRY["run_active_seconds"]`,
   following `RunWallClockSecondsBreaker`'s docstring/style conventions.
3. Unit test proves correct summation across multiple settled tasks.
4. Unit test proves a large gap between one task's `ended_at` and the next task's `started_at`
   does NOT count toward elapsed (the key differentiator vs `run_wall_clock_seconds` — same
   fixture must show `run_wall_clock_seconds` tripping while `run_active_seconds` does not).
5. Unit test proves resume-safety: reconstructs identically after a `RunState.model_validate_json
   (state.model_dump_json())` round-trip (mirrors `_consecutive_failure_streak`'s existing
   round-trip test pattern).
6. `run_wall_clock_seconds` is completely unchanged (byte-identical) — both conditions coexist.
7. Schema validation test (jsonschema) accepts `run_active_seconds` with a `threshold`, rejects
   it without one.

## Risks
- None significant — purely additive; no existing condition's behavior changes.

## Dependencies
- None (first task in the epic).

## Pseudocode / Algorithm
```text
def _settled_task_active_seconds(state: RunState) -> float:
    total = 0.0
    for ts in state.tasks.values():
        if ts.started_at is not None and ts.ended_at is not None:
            total += (datetime.fromisoformat(ts.ended_at) - datetime.fromisoformat(ts.started_at)).total_seconds()
    return total

class RunActiveSecondsBreaker(Breaker):
    condition = "run_active_seconds"
    def evaluate(self, spec, ctx):
        threshold = spec.threshold
        if threshold is None:
            return None
        elapsed = _settled_task_active_seconds(ctx.state)
        if elapsed >= threshold:
            return TripResult(detail={"elapsed": elapsed, "threshold": threshold})
        return None
```

## Schemas / Interface Notes
- Interface: `Breaker.evaluate(spec, ctx) -> TripResult | None` (existing ABC, no change).
- Spec/data schema: `specs/workflow.schema.json` `$defs.circuitBreaker.properties.condition.enum`
  gains `"run_active_seconds"`; `allOf[0].if.properties.condition.enum` (threshold-required list)
  also gains it.
- Triggers/events: N/A.
- Artifacts: none new (reads only `RunState.tasks[*].started_at/ended_at`, already persisted).

## Handoff Boundary
- Upstream: none.
- Downstream: `T-yX1Oi5-breaker-extend-core` reuses the same `evaluate_breakers` call site this
  task does not modify; `T-gzG0EI-docs-and-verification` bumps the module docstring's condition
  count (eight -> nine) to include this one.

## Artifacts
- Docs/comments: `meta/tickets/E-3JTmVu-breaker-resume-extend/T-69MnaW-run-active-seconds-breaker/`
- Large outputs: none.
