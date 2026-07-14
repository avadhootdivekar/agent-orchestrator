# TASK: T-yX1Oi5-breaker-extend-core

## Metadata
- Task ID: `T-yX1Oi5-breaker-extend-core`
- Epic ID: `E-3JTmVu-breaker-resume-extend`
- Owner: developer
- Created: 2026-07-14
- Last Updated: 2026-07-14
- Status: Done
- Estimate: 1.5 days

## Requirements Mapping
- Requirement IDs: FR-2a, FR-2b, FR-2c, NFR-1, NFR-3, NFR-4, NFR-5

## Description
Add the core (non-CLI) mechanism for scoped breaker-threshold extension + un-latch, addressing
`T-t4m8x1`'s explicitly-flagged always-latched gap — but SCOPED to only breakers an operator
explicitly asks to extend (never a blanket un-latch-everything-on-resume, which would regress
existing resumability for every OTHER breaker).

1. `RunState.breaker_overrides: dict[str, float] = {}` — breaker id -> absolute overridden
   threshold, defaulted for NFR-5 exactly like `tripped_breakers`/`route_decisions`.
2. `evaluate_breakers` resolves `state.breaker_overrides.get(spec.id, spec.threshold)` ONCE,
   centrally, and evaluates against `spec.model_copy(update={"threshold": effective_threshold})`
   — never a per-`Breaker`-subclass special case (DRY, works uniformly for every condition,
   present and future).
3. Standalone `apply_breaker_extension(state, spec, *, extend_by_seconds, extend_by_same) ->
   float` in `breakers.py` (same standalone style as `record_trip`): computes
   `current_effective = state.breaker_overrides.get(spec.id, spec.threshold)`, then
   `new_threshold = current_effective + (extend_by_seconds if given else spec.threshold)`
   ("by same amount set at startup" = add the ORIGINAL `spec.threshold` again), writes
   `state.breaker_overrides[spec.id] = new_threshold`, removes matching `TrippedBreaker` records
   for that id from `state.tripped_breakers` (un-latch), logs `breaker.extend` (mirrors
   `record_trip`'s `breaker.trip` logging shape), returns `new_threshold`. Raises
   `SpecValidationError` if both/neither of `extend_by_seconds`/`extend_by_same` given.

## Acceptance Criteria
1. `RunState.breaker_overrides` field added, defaults to `{}`; an old `state.json` fixture
   without it loads fine (NFR-5 — reuse `tests/fixtures/state_pre_routing_breakers.json` pattern).
2. `apply_breaker_extension` unit tests (isolated, no engine): `extend_by_seconds` path,
   `extend_by_same` path, un-latch verified (matching `TrippedBreaker` removed from
   `tripped_breakers`, others untouched), both-given error, neither-given error.
3. `evaluate_breakers` integration test: an override on a NON-wall-clock condition (e.g.
   `task_failures`) changes trip behavior too — proves the resolution is centralized/uniform,
   not wall-clock-only.
4. Regression proof: `uv run pytest tests/test_resume_replay.py tests/test_stop_reframe_parity.py
   -q` stays green, same pass count as baseline, zero new failures — non-extended breakers'
   latch-forever behavior is unchanged.
5. `breaker.extend` log event emitted with `breaker_id`/`old_threshold`/`new_threshold` fields
   (same `extra={...}` convention as `record_trip`'s `breaker.trip`).

## Risks
- Risk: touching `evaluate_breakers` could regress the latch behavior for untouched breakers.
  Mitigation: the override lookup uses `.get(spec.id, spec.threshold)` — a spec with no override
  entry evaluates against its own unchanged `spec.threshold`, byte-identical to before this
  change. Proven by AC4 (existing regression suites unmodified and green).

## Dependencies
- None on `T-69MnaW` (independent files touched: this task's `evaluate_breakers` change applies
  uniformly to `run_active_seconds` once that task lands, but does not require it to exist first
  — `evaluate_breakers` iterates whatever conditions are already registered).

## Pseudocode / Algorithm
```text
def apply_breaker_extension(state, spec, *, extend_by_seconds=None, extend_by_same=False) -> float:
    if (extend_by_seconds is not None) == extend_by_same:
        raise SpecValidationError("exactly one of extend_by_seconds/extend_by_same required")
    current_effective = state.breaker_overrides.get(spec.id, spec.threshold)
    delta = extend_by_seconds if extend_by_seconds is not None else spec.threshold
    new_threshold = current_effective + delta
    state.breaker_overrides[spec.id] = new_threshold
    state.tripped_breakers = [tb for tb in state.tripped_breakers if tb.id != spec.id]
    run_log.warning("breaker.extend", extra={...})
    return new_threshold

# in evaluate_breakers, per spec:
effective_threshold = state.breaker_overrides.get(spec.id, spec.threshold)
eval_spec = spec.model_copy(update={"threshold": effective_threshold}) if effective_threshold != spec.threshold else spec
trip = breaker.evaluate(eval_spec, ctx)
```

## Schemas / Interface Notes
- Interface: new standalone function `apply_breaker_extension` in `breakers.py`; no ABC change.
- Spec/data schema: `models.py` `RunState.breaker_overrides: dict[str, float] = {}`.
- Triggers/events: new `breaker.extend` structured log event.
- Artifacts: none new; mutates the already-persisted `RunState`/`state.json`.

## Handoff Boundary
- Upstream: independent of `T-69MnaW`.
- Downstream: `T-nVWE1W-resume-extend-breaker-cli` calls `apply_breaker_extension` directly from
  `cli.py`'s `resume` command — must not need any CLI-side re-implementation of the un-latch or
  threshold-math logic.

## Artifacts
- Docs/comments: `meta/tickets/E-3JTmVu-breaker-resume-extend/T-yX1Oi5-breaker-extend-core/`
- Large outputs: none.
