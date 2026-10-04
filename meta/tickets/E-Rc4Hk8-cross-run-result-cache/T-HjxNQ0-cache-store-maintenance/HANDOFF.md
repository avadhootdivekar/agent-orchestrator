# HANDOFF: T-HjxNQ0-cache-store-maintenance

- Task: `T-HjxNQ0-cache-store-maintenance`
- State: `Draft` (handoff not yet available)
- From: `developer` (Dev C)
- To: T-6tRKml, T-gDNjN2

## What will be handed over
- `LocalFsCacheStore(CacheStore, CacheAdmin)`: `iter_entries`, `stats`, `prune`, `clear`,
  read-only `verify`, and the bounded `maybe_enforce_limits`.

## Frozen names / contracts
- The `CacheAdmin` signatures and the report dataclasses (HLD §13.4).

## Verification the receiver should run
- `pytest -q tests/cache/test_store_maintenance.py tests/cache/test_store_race.py`

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: Handoff stub created.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2 contents.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 3: `CacheAdmin` base added
  here; byte bound; read-only `verify`. State `Draft` mirrors `TASK.md` and `STATUS.md`.
