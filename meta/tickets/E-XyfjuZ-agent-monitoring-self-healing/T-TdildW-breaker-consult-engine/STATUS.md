# STATUS

- ID: `T-TdildW-breaker-consult-engine`
- Updated At: 2026-07-15
- State: Done
- Owner: dev-epic agent

## This update
- By: dev-epic agent
- Role: developer
- Date: 2026-07-15
- Comment: Implemented per D9 (revised design): `RunState.monitor_decisions` (ONE new field,
  not four) + derivation helpers in `models.py`; `Orchestrator.__init__` gained
  `monitor`/`max_extensions_per_breaker`/`max_monitor_calls_per_run`; `_consult_breaker_trips`
  wired at the existing `evaluate_breakers` call site via before/after id-diffing —
  `evaluate_breakers` itself is byte-for-byte unchanged. 12 new integration tests in
  `tests/test_monitoring_breaker_consult.py` covering all 8 acceptance criteria (hard-mode
  no-op, recommend extend-then-halt via both the engine bound and RuleBasedMonitor's own
  policy, mixed hard+recommend never consults, max_extensions_per_breaker bound, cap
  exhaustion + `monitor.cap_exceeded` event, resume honouring persisted state, zero
  `state.tasks`/`injected_tasks` pollution via a real `AgentMonitor` call, and a raising
  monitor falling back safely). Two test-design bugs found and fixed during my own
  verification (not engine bugs): a manufactured `RunState` doesn't pre-seed pending entries
  for undispatched tasks the way `new_run()` does, and `consecutive_failures` is a
  recency-ordered streak that a real dispatched task's later timestamp always breaks — fixed
  by using `task_failures` (a pure count) for the mixed-mode test instead.

## Evidence
- `uv run pytest tests/test_monitoring_breaker_consult.py -q` → 12 passed.
- Targeted regression suites (`test_stop_reframe_parity.py`, `test_resume_replay.py`,
  `test_engine_budget.py`, `test_breakers.py`, `test_engine_breakers.py`,
  `test_mvp_breaker_conditions.py`, `test_breaker_extension.py`,
  `test_resume_extend_breaker_cli.py`, `test_run_active_seconds_breaker.py`,
  `test_routing_breaker_models.py`, `test_validate_run_control.py`) → 212 passed, zero
  regressions.
- `uv run pytest -q` (full suite) → 752 passed, 3 skipped (740 prior + 12 new).
- `uv run ruff check .` / `ruff format --check .` clean on all touched files;
  `uv run mypy src` → 4 errors, all pre-existing in `_version.py` (unchanged from baseline).

## Risks / Blockers
- None. The highest-risk item (byte-identical preservation of `evaluate_breakers`) is proven
  by the unmodified parity/regression suites staying green.

## Next actions
1. Done. `T-yjtdAq` implements Consult Point B next, sharing this same
   `Orchestrator.__init__` (sequenced immediately after to avoid a constructor merge
   conflict).
