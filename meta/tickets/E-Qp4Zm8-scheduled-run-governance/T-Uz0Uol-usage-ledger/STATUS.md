# STATUS

- ID: `T-Uz0Uol-usage-ledger`
- Updated At: 2026-11-11
- State: Implemented (pending review/merge)
- Owner: developer agent

## This update
- By: developer (unit `w17-03-a2-usage-ledger`) · Role: developer · Date: 2026-11-11
- Implemented ACs 1-13. New `service/usage_ledger.py` (`UsageLedger`, `Charge`, `WindowUsage`, `RunProbe`/`probe_run`,
  `run_confidence`, `COST_REPORTING_EXECUTORS`, `STATE_PROBE_GRACE_SECONDS`); `fire_store.py` additive
  `update_governance` (atomic, refuses to lower `cost_observed_usd`), `find_by_run_id`, `iter_schedule`
  (`write_suppressed(governance=)` already existed). Import direction: `usage_ledger -> fire_store`; `FireGovernance` stays
  defined in `fire_store` and is re-exported from `usage_ledger`.
- `governance_gate.py`: interim ledger removed. The gate now types against `LedgerPort` (protocol); `FireRecordLedger` is kept
  as an alias of `UsageLedger` so `ScheduleEngine`/`ScheduledLauncher` default factories (not edited here) use the real ledger.
  `WindowUsage` fields renamed `actual`/`reserved` -> `actual_usd`/`reserved_usd` (+ `confidence`).

## Evidence
- `tests/service/test_usage_ledger.py`: 52 tests, `usage_ledger.py` coverage 97%. `pytest tests/service`: 726 passed.
  ruff check/format clean on own paths; `mypy src` clean.
- Mutation checks (each killed by a test): drop high-water mark, drop blind-run `max`, drop `window_open` gate on partial,
  window start/end boundary (both), persist-always, drop warn-once, drop summary cost.
- Perf (AC8): 50 000 records / 100 schedules, one schedule's month usage after index warm, asserted < 500 ms (spec 50 ms x10 CI
  margin); observed locally well inside 50 ms.

## Deviations / notes
- Index is built lazily per workspace on first `usage()` (the ledger is constructed without knowing workspaces), then kept fresh
  by `note_written` + directory-mtime/per-file signature rescans; `invalidate()` forces a rescan (the gate calls it under flock).
- `charge(rec, *, window_open=True)`: HLD's `window_open` is per-window, so `usage()` evaluates a fire once per open/closed variant.
- Confidence worst-of order: `actual < pending < partial < unknown`.
- A still-running run with no settled task is `pending` (not `unknown`); a terminal one is `unknown`.

## Risks / follow-ups
- Event sink is not wired in production: `ScheduleEngine`/`ScheduledLauncher` construct the ledger with defaults, so
  `schedule.governance.cost_unknown` is only emitted when a sink is injected. Needs a one-line wiring edit in
  `schedule_engine.py`/`schedule_launch.py` (owned by other units this wave).
- `update_governance` is an unlocked read-modify-write (single-writer engine assumption); `T-nI4DBf` writing `cap_state`
  concurrently should go through the same method.
- Depends on `RunState` fields from `T-et7Edf` (committed in 1c0bfeb).
