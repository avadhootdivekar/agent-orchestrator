# STATUS

- ID: `T-Fr2Nx8-fire-store-and-state`
- Updated At: 2026-11-11
- State: Done
- Owner: developer

## Reconciliation 2026-11-11 (By: developer · Role: developer · unit `w24-07-a1-task-status-sync` · docs only)
- Checked against branch `ad/enhancements-10nov` at HEAD `1d5c6f9`. Any older line below that says "not started", "uncommitted" or "in the working tree" is superseded by this block where they disagree.
- State: **Done**
- All 17 ACs met (55 tests, 99 % coverage); reviewed via the T-Se4Bk5 architecture review (REVIEW.md; R-1 durable write fixed `b93b576`, R-2 view-path writer fixed by `ReadOnlyFireStore`). No unmet ACs.
- Non-AC residue: `FireIntentStore.write` is `exists()` + replace, not an exclusive create (T-lTW88y W-4, Info; safe under `usage.lock` + single engine); `prune` is never called from `src/` (T-lTW88y I-1, Info).

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
