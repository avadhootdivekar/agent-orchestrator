# TASK: T-TdildW-breaker-consult-engine

## Metadata
- Task ID: `T-TdildW-breaker-consult-engine`
- Epic ID: `E-XyfjuZ-agent-monitoring-self-healing`
- Owner: dev-epic agent (self-implemented)
- Created: 2026-07-14
- Last Updated: 2026-07-14
- Status: Draft
- Estimate: 1 day

## Requirements Mapping
- Requirement IDs: FR-2, FR-6 (breaker side), NFR-1, NFR-5

## Description
Wire Consult Point A into `engine.py` at the existing `evaluate_breakers` call site (~L781),
per Design Decisions D1-D3 in the epic doc. `evaluate_breakers()` itself is NOT modified (D2) —
the engine call site diffs `state.tripped_breakers` ids before/after the existing call to
determine which `workflow.circuit_breakers` specs newly tripped this boundary, in declared order.

New `Orchestrator.__init__` params: `monitor: Monitor | None = None` (defaults to a fresh
`RuleBasedMonitor()` inside `__init__` if not provided), `max_extensions_per_breaker: int = 1`,
`max_monitor_calls_per_run: int = 10`.

New `RunState` fields (all defaulted, NFR-5-style): `monitor_decisions: list[MonitorDecisionRecord]
= []`, `monitor_breaker_extensions: dict[str, int] = {}`, `monitor_calls_made: int = 0`.

New private engine method `_consult_breaker_trips(newly_tripped_specs, state, run_log) ->
Literal["extend", "halt"]`: per spec in declared order, check `max_extensions_per_breaker` bound
and `max_monitor_calls_per_run` cap (skip consulting, contribute `halt` if either exhausted —
log `monitor.cap_exceeded` for the cap case only); otherwise log `monitor.consult`, call
`self._monitor.decide_breaker_trip(...)`, log `monitor.decision`, append a
`MonitorDecisionRecord`. All-or-nothing: apply all extensions (via the existing
`apply_breaker_extension`) only if every decision was `extend`; any `halt` → no extension applied,
overall `halt`.

## Acceptance Criteria
1. A boundary where all newly-tripped breakers are `mode: "hard"` (including the pre-epic default
   for every existing spec) behaves BYTE-IDENTICALLY to today — proven by re-running
   `tests/test_stop_reframe_parity.py` and the existing breaker/engine test suites unmodified,
   green.
2. A boundary where all newly-tripped breakers are `mode: "recommend"` consults
   `self._monitor.decide_breaker_trip` once per breaker in `workflow.circuit_breakers` declared
   order; `RuleBasedMonitor`'s default extend-once-then-halt policy is exercised end-to-end
   (extend → run continues; a later re-trip past the extended threshial → halts, since
   `prior_extensions` is now 1).
3. A boundary with a MIX of `hard` and `recommend` newly-tripped breakers halts WITHOUT calling
   the monitor at all (test with a monitor double that raises `AssertionError` if invoked).
4. `max_extensions_per_breaker` (default 1) is enforced in engine code — a monitor double that
   always answers `extend` still gets forced to `halt` once `state.monitor_breaker_extensions[id]`
   reaches the bound; the monitor is not even called once the bound is hit (proven with a counter
   on the double).
5. `max_monitor_calls_per_run` (default 10) exhaustion falls back to `halt` (the safe default)
   without calling the monitor; a `monitor.cap_exceeded` event is logged.
6. Resume: a persisted `monitor_breaker_extensions`/`monitor_calls_made` count carries over
   unchanged (no `prepare_resume` code change needed — verify by test, not just by inspection).
7. `evaluate_breakers`'s own signature, behavior, and the existing stepping-clock/no-op tests are
   completely unmodified (D2, D5 in the LLD sense — no new `clock()` call added inside
   `evaluate_breakers` itself).
8. Old `state.json` (predating this epic, missing the 3 new fields) loads via
   `RunState.model_validate_json` without error (pydantic defaults).

## Risks
- Medium-high: this is the most cross-cutting engine change in the epic. Mitigation: the
  call-site diffing approach (D2) keeps `evaluate_breakers` untouched, isolating blast radius to
  one new block around the existing call plus a new private helper method.

## Dependencies
- `T-mYMiPK-guardrail-mode-schema-model` (needs `CircuitBreakerSpec.mode`).
- `T-h2XLxe-monitor-abstraction` (needs `Monitor`/`RuleBasedMonitor`/`BreakerTripSummary`/
  `BreakerVerdict`).

## Pseudocode / Algorithm
See Design Decisions D1-D3 in `docs-md/ai-epics/E-XyfjuZ-agent-monitoring-self-healing.md` for
the full trace/rationale; engine-side pseudocode:
```text
before_ids = {tb.id for tb in state.tripped_breakers}
breaker_action = evaluate_breakers(workflow, state, self._clock, self._store, run_log)  # UNCHANGED call
if breaker_action is not None:
    newly_tripped_ids = {tb.id for tb in state.tripped_breakers if tb.id not in before_ids}
    newly_tripped_specs = [b for b in workflow.circuit_breakers if b.id in newly_tripped_ids]
    consultable = (len(newly_tripped_specs) == len(newly_tripped_ids) and newly_tripped_specs
                   and all(b.mode == "recommend" for b in newly_tripped_specs))
    outcome = self._consult_breaker_trips(newly_tripped_specs, state, run_log) if consultable else "halt"
    if outcome == "halt":
        state.status = "failed"; failed = True; self._runstate.save(state); break
    self._runstate.save(state)  # outcome == "extend": fall through to rest of loop body, unchanged
```

## Schemas / Interface Notes
- Interface / API: `Orchestrator.__init__` new kwargs; `Orchestrator._consult_breaker_trips`
  (private).
- Spec / data schema (JSON/YAML): none new (reuses `CircuitBreakerSpec.mode` from `T-mYMiPK`).
- Triggers / events (cron/event): N/A.
- Artifacts (inputs/outputs by path): `RunState.monitor_decisions`/`monitor_breaker_extensions`/
  `monitor_calls_made` persisted in `state.json` (existing mechanism, new fields only).

## Handoff Boundary
- Upstream: `T-mYMiPK`, `T-h2XLxe`.
- Downstream: `T-QyNnf5` wires CLI construction of the `monitor`/bounds into `Orchestrator(...)`;
  `T-Wx8vUq` covers the full acceptance-criteria test matrix.

## Artifacts
- Docs/comments: `meta/tickets/E-XyfjuZ-agent-monitoring-self-healing/T-TdildW-breaker-consult-engine/`
- Large outputs: none.
