# STATUS

- ID: `T-x8v4d3-breaker-framework-registry`
- Updated At: 2026-07-09
- State: Done
- Owner: developer

## This update
Ticket created from LLD §6. Breaker ABC + registry + eval loop (trip→record→act) + fail/stop/pause actions.

By: architect · Role: architect · Date: 2026-07-09 · Comment: Wave-2. Framework only; the six conditions land in T-q5n7k2. stop/pause map to resumable failed per ADR-RC-004.

## Evidence
- Design: `docs-md/lld-run-control-routing-breakers.md` §6, ADR-RC-004.

## Risks / Blockers
- Depends on T-b7q2m4 (`CircuitBreakerSpec`, `TrippedBreaker`).

## Next actions
1. Implement `Breaker` ABC + `BREAKER_REGISTRY` + `evaluate_breakers` at task boundaries.
2. Hand trip/record path to T-q5n7k2 and T-r3j9b6.

---

## Completion (2026-07-09)

Implemented in `src/agent_orchestrator/breakers.py` (new module, mirrors the
`Executor`/`BudgetManager` DI style): `BreakerContext` (pydantic, paths/ids/counters
only — NFR-1), `TripResult`, `Breaker` ABC, `BREAKER_REGISTRY` (empty — no MVP
conditions registered, per scope; raises `SpecValidationError` for any declared
condition not yet registered, never a silent no-op), `record_trip()` (standalone,
reusable recording primitive — `state.tripped_breakers.append(...)` + `breaker.trip`
emit, deliberately callable outside the eval loop so T-r3j9b6's `trip_builtin` re-frame
can call it directly), `map_action()`, and `evaluate_breakers()` (trip → record → act,
declared-spec order then `builtin_specs` extension point, latch-by-id, first NEW trip
this boundary owns the action).

Wired into `engine.py`'s main loop immediately after the existing
`self._runstate.save(state)` call following the succeeded/non-succeeded logging block
(task boundary, before the next dispatch and before the existing
`ts.status not in (...)` check). `fail`/`stop`/`pause` all set
`state.status="failed"` (resumable) and break — the action is preserved only in
`tripped_breakers[].action` (ADR-RC-004); no new `RunState.status` literal added; exit
code mapping (`typer.Exit(0 if state.status == "succeeded" else 1)`) untouched.

Found and fixed one real regression during implementation: an early draft called
`clock()` unconditionally on every boundary (even with zero `circuit_breakers`
declared), which silently consumed a step from stepping-clock fixtures used elsewhere
in the suite and desynced a budget-wait sleep-duration assertion
(`test_budget_integration.py::TestWaitThenContinueWindowRoll`). Fixed by short-circuiting
`evaluate_breakers` before touching the clock when there is nothing to evaluate — the
common case for every pre-existing workflow/test. Re-ran full suite after the fix: no
regressions.

Tests added: `tests/test_breakers.py` (13 cases — map_action, no-op when nothing
declared, trip/record/latch, two-breakers-same-boundary first-in-declared-order-wins,
unregistered-condition error, clock purity, `record_trip` as a standalone primitive)
and `tests/test_engine_breakers.py` (2 cases — a synthetic `circuit_breakers` entry
pointing at a test-only stub condition proves the hook fires at a real task boundary
inside `Orchestrator.run()` and halts before the next dispatch; a byte-identical no-op
regression guard for workflows without `circuit_breakers`). All stub conditions are
registered/unregistered via a fixture so `BREAKER_REGISTRY` never leaks test-only state.

Verification: `uv run ruff check .` / `uv run ruff format --check .` / `uv run mypy .`
clean on every file this ticket touched (`src/agent_orchestrator/breakers.py`,
`src/agent_orchestrator/engine.py`, `tests/test_breakers.py`,
`tests/test_engine_breakers.py`); pre-existing findings in untouched files
(`test_e2e_cli.py`, `test_engine_budget.py`, `test_project_config.py`) predate this
change and are out of scope. `uv run pytest -q` → 478 passed, 3 skipped (451 baseline +
27 new: 15 from this ticket, 12 from T-c4w6p1 landing in parallel) — no regressions.

All 6 TASK.md acceptance criteria verified: (1) hook fires at the task boundary after
save, before next dispatch; (2) exactly one `TrippedBreaker` per id, latched across
re-evaluation; (3) `breaker.trip` emitted once per NEW trip with the full field set;
(4) two-breakers-same-boundary test proves first-in-declared-order ownership; (5)
fail/stop/pause all terminate on resumable `status="failed"`, action preserved
distinctly per record; (6) clock purity proven (mocked `time.time()` raises if called
by breaker code; a mocked `run_log` avoids a false positive from stdlib logging's own
internal `time.time()` call).

By: developer · Role: developer · Date: 2026-07-09 · Comment: T-x8v4d3
breaker-framework-registry DONE. Framework + registry + engine wiring landed exactly to
scope (no MVP conditions implemented — that's T-q5n7k2; no built-in re-frame — that's
T-r3j9b6; zero file overlap with the parallel T-c4w6p1 route-cone ticket). Unblocks
T-q5n7k2, T-r3j9b6, T-t4m8x1, T-n9k3r5.
