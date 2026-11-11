# STATUS

- ID: `T-Uz0Uol-usage-ledger`
- Updated At: 2026-11-11
- State: Partial — ACs 1-13 implemented; event-sink wiring open
- Owner: developer agent

## Reconciliation 2026-11-11 (By: developer · Role: developer · unit `w24-07-a1-task-status-sync` · docs only)
- Checked against branch `ad/enhancements-10nov` at HEAD `1d5c6f9`. Any older line below that says "not started", "uncommitted" or "in the working tree" is superseded by this block where they disagree.
- State: **Partial — ACs 1-13 implemented; event-sink wiring open**
- Committed `61ddea2`; 52 tests, 97 % coverage, mutation-checked; reviewed in the T-lTW88y gate (F-1/F-2/N-1 fixes).
- **Unmet / open**: the production event sink is not wired - nothing outside `usage_ledger.py` passes an `event_sink`, so `schedule.governance.cost_unknown` is never emitted by the service; AC8 perf asserted at 500 ms (10x margin), the exact 50 ms figure never measured.

## This update
- 2026-11-11 — By: developer (unit `w20-08-a2-docs-adr-sync`) · Role: developer · Date: 2026-11-11 · Comment: Docs reconciled (HLD §26). Still open: production event-sink wiring (`schedule.governance.cost_unknown`), `ScheduleEngine` default factory still `FireRecordLedger` alias.
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
  margin); the exact 50 ms figure was not separately measured.

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

## Update 2026-11-11 — REVIEW G-2 fixed (w31-02)
By: developer · Role: developer · Date: 2026-11-11
Comment: `UsageLedger.charge` treats `cap_state=="resumed" and not final` as live: the resume top-up stays reserved
(`mark + max(0, reserved - mark)`) until the resumed child is finalised, even while `state.json` still says `failed`.
Tests: 3 ledger tests (probe shape through `charge` and `usage`, final releases, running unchanged) + 1 gate test (fire
refused with `cost_cap_reached` while a resume is pending). Both pending-resume tests were verified to fail without the fix.
`pytest tests/service tests/schedules tests/ui/test_schedule_usage_api.py` 1421 passed; ruff + `mypy src` clean.
Note: a child that dies before writing state leaves the reservation held until the record is finalised (over-, not under-reserved).
