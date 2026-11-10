# STATUS

- ID: `T-Ev3Qm5-schedule-engine-core`
- Updated At: 2026-11-11
- State: Done
- Owner: developer agent

## This update
By: developer · Role: developer · Date: 2026-11-11
- Implemented `service/schedule_engine.py` (ScheduleEngine, FireDecision, FireReason, ScheduledLauncher + TriggerSource
  Protocols, Candidate, RunNowRequest, probe_engine_lock, EngineLockHeldError) per the corrected HLD §6.2/§6.3
  (F2/F6/F7/F8/F9/F13), with both load-bearing orderings (already_fired after catch-up; concurrency last).
- 59 tests in `tests/service/test_schedule_engine.py` (fixed clocks, recording-stub launcher; one real-child test for F2 reaping).

## Evidence
- `pytest tests/service tests/schedules`: 624 passed. Engine module coverage 93 %. ruff check + format clean; `mypy src` and
  the new test file: 0 errors (whole-tree `mypy .` reports pre-existing errors in other files, none in these two).

## Deviations / notes for downstream
- `__init__` adds one extra keyword, `registry_factory` (test seam; default `ServiceRegistry`). `state_dir` defaults to `default_state_dir()`.
- Backlog collapse reports ONE `schedule.missed` (`reason=backlog_collapsed`, `skipped_instants=N-1`) instead of burning N-1 records
  (one fsync each, inside a 0.5 s budget). Only the chosen instant's key is ever burned/fired.
- Opt-in (F5) is read as `getattr(entry, "schedules", False)`; no registry entry opts in until `T-Sv5Hb3` adds the field.
- **`T-Lp4Wt6`/`T-Sv5Hb3` MUST build the launcher with the engine's `state_store`, `fire_store`, `event_log` and
  `process_supervisor(root)`**: `ScheduleStateStore` caches state.json in memory and is single-writer; private instances clobber each other.
- Queued fire's `scheduled_for` is held in memory (state stores only the key); after a restart a queued fire is dropped loudly
  (`schedule.missed`, `reason=queue_lost_on_restart`). Follow-up: add `queued_scheduled_for` to `ScheduleState` (fire_store.py).
- Drain respects the concurrency caps; run-now skips jitter/catch-up and never advances the natural anchor.
- `until` hook point is marked in `evaluate` for `T-Un0Lm6`. Full-suite run stopped at an unrelated
  `tests/bench/test_blackbox_grader.py` failure (2291 passed before it).
