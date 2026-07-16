# STATUS

- ID: `T-yjtdAq-self-heal-engine`
- Updated At: 2026-07-15
- State: Done
- Owner: dev-epic agent

## This update
- By: dev-epic agent
- Role: developer
- Date: 2026-07-15
- Comment: Implemented per D9 (no new RunState field — `monitor_heal_retries` derived via
  `count_monitor_heal_retries`, reusing the same `monitor_decisions` list T-TdildW added).
  `Orchestrator.__init__` gained `self_heal_enabled`/`max_heal_retries_per_task`.
  `_consult_task_failure_heal` wired immediately after the existing budget-reconcile/429
  block, before `ts.attempts`/`ts.ended_at` are ever touched (Design Decision D4) — a
  healed-and-retried failure never sets `ts.status="failed"` and never reaches
  `evaluate_breakers`. 10 new integration tests in `tests/test_monitoring_self_heal.py`
  using a local `_ScriptedExecutor` double (FakeExecutor's fixed `"fake failure"` error
  string can't exercise transient-pattern classification). Covers: disabled-by-default
  byte-identical behavior, transient-heal-then-succeed, transient-heal-then-fail-again
  (bound exhausted), non-transient immediate accept_failure, `timed_out` never healed
  (D4 boundary), resume honouring a persisted heal-retry count, `max_monitor_calls_per_run`
  cap + `monitor.cap_exceeded` event, a raising monitor falling back safely, heal retries
  not consuming `RetryPolicy` attempts, and `ts.started_at` preserved across the heal-retry
  redispatch (same `_StepDatetime` clock-monkeypatch technique as the E-3JTmVu
  `run_active_seconds`/quota-wait regression test).

## Evidence
- `uv run pytest tests/test_monitoring_self_heal.py -q` → 10 passed.
- `uv run pytest tests/test_engine.py tests/test_engine_budget.py tests/test_engine_routing.py
  tests/test_executor.py tests/test_run_active_seconds_breaker.py -q` → 147 passed, zero
  regressions.
- `uv run pytest -q` (full suite) → 762 passed, 3 skipped (752 prior + 10 new).
- `uv run ruff check .` / `ruff format --check .` clean; `uv run mypy src` → 4 pre-existing
  `_version.py` errors only.

## Risks / Blockers
- None. The hook-placement risk (before vs. after budget reconcile) resolved cleanly —
  placed after reconcile (so a healed retry's actual/estimated tokens are still correctly
  reconciled) but before any `TaskRunState` settle-time mutation.

## Next actions
1. Done. `T-QyNnf5` wires `self_heal_enabled`/`max_heal_retries_per_task`/`monitor` from
   config + CLI into `ao run`/`ao resume`.
