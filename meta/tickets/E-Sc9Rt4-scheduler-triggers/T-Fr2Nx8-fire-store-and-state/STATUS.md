# STATUS

- ID: `T-Fr2Nx8-fire-store-and-state`
- Updated At: 2026-11-11
- State: Done
- Owner: developer

## This update
- By: developer · Role: developer · Date: 2026-11-11
- Implemented `service/fire_store.py` (fire_key, FireIntentRecord/Store, ScheduleState/StateStore, ScheduleEventLog) and `tests/service/test_fire_store.py`. All 17 ACs met.

## Evidence
- `pytest -q tests/service/test_fire_store.py`: 55 passed; coverage of `service/fire_store.py` 99 %.
- Crash-simulation (intended + dead pid -> orphaned, zero launches) and clock-rewind no-double-fire tests pass; fsync spy test present.
- `ruff check`, `ruff format --check`, `mypy src` (221 files): clean.
- Full suite: 11213 passed, 12 failed, 32 skipped — failures are in bench/cache/sentinel tests that do not import `fire_store` (sentinel ones sit on other units' uncommitted edits in the shared tree); not caused by this change.

## Risks / Blockers
- `state.json` is single-writer with no lock; `T-Ev3Qm5` must keep the engine the only writer.

## Next actions
1. `T-Ev3Qm5` / `T-Lp4Wt6` integrate against the landed signatures.
