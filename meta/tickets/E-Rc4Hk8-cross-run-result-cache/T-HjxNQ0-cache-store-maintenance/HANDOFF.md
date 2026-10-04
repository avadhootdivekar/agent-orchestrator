# HANDOFF: T-HjxNQ0-cache-store-maintenance

- Task: `T-HjxNQ0-cache-store-maintenance`
- State: `Draft` (handoff not yet available)
- From: `developer` (Dev C)
- To: T-6tRKml, T-gDNjN2

## What will be handed over
- `LocalFsCacheStore`: `iter_entries`, `stats`, `prune`, `clear`, `verify`, and the real
  `maybe_enforce_limits`.

## Frozen names / contracts
- The `CacheAdmin` signatures, and the report dataclasses (HLD §13.4).

## Verification the receiver should run
- `pytest -q tests/cache/test_store_maintenance.py tests/cache/test_store_race.py`

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: Handoff stub created.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2 contents. State `Draft`
  mirrors `TASK.md` and `STATUS.md`.
