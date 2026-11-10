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

## Review fixes (w10-03 Opus review) — By: developer · Role: developer · Date: 2026-11-11
Fixed in `service/schedule_engine.py` (fire_store.py needed no change), each with a regression test in
`tests/service/test_schedule_engine.py` that fails when the fix is reverted (mutation-checked):
- **M1** same-tick fires of one schedule now count against overlap (`_pending_schedules`): run-now + natural instant fire once under `skip`.
- **M2** backlog collapse no longer skips past an instant still in its jitter window (only stale instants are collapsed, onto one whose own window is open).
- **M3** a run-now blocked by concurrency is written back (same `requested_at`, TTL-bounded) + `schedule.run_now_deferred`; dropped only on expiry.
- **M4** AC18 no-burn asserted on all three branches (main/global cap, cross-schedule slot, queue drain); the three skip sites share `_concurrency_skip()` (L5).
- **L1** collapse `schedule.missed` reported once per distinct collapse. **L3** `catch_up` only emitted for an instant that can still fire. **L4** eval-error bookkeeping failure no longer discards the tick's decisions. **L5** `STATUS_*` constants + shared helper. **L7** `status_snapshot()` error keeps the full HLD §12 shape.
- **Not fixed (recorded):** **L2** (`MAX_CATCHUP_STEPS` walk cost/in-walk deadline/closed form for interval) — perf-only, needs its own measured change; **L6** (HLD §14 DST wording is wrong: spring-forward gap shifts forward, not skipped) — doc outside this unit's paths.
- **Residual / follow-up:** jitter > period is still accepted by the model (`schedules/models.py`, T-Sd1Kq7): instants head-of-line-blocked behind a long jitter can go stale and be burned `missed` (reported, never silent). Recommend clamping `jitter_seconds` below the minimum period at load. Also no cron bindings in the engine tests (review gap) — not added here. HLD §6.5 should note that collapsed instants are not burned (L1 note).
- Evidence: `pytest tests/service tests/schedules` 845 passed (engine file 60 → 72); `ruff check`/`format`, `mypy src` clean.
