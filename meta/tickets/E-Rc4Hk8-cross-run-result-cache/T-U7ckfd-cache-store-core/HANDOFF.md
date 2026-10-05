# HANDOFF: T-U7ckfd-cache-store-core

- Task: `T-U7ckfd-cache-store-core`
- State: `Draft` (handoff not yet available)
- From: `developer` (Dev C)
- To: T-HjxNQ0 (same file), T-u3jG8F, T-gDNjN2, T-6tRKml

## What will be handed over
- `agent_orchestrator.cache.store.LocalFsCacheStore(CacheStore)`: the full `CacheStore` surface
  plus `for_workspace`, `ensure_layout` and `root`; `CacheUnsafePathError` on unsafe components.
- `agent_orchestrator.cache.store.is_expired`.
- `maybe_enforce_limits` is a placeholder returning `None` until T-HjxNQ0.

## Frozen names / contracts
- The layout paths (HLD §8.4.1), the canonical entry bytes, and "checks before every operation".

## Verification the receiver should run
- `pytest -q tests/cache/test_store_core.py`

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: Handoff stub created.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2 contents.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 3: `CacheStore`-only class;
  unsafe-path semantics. State `Draft` mirrors `TASK.md` and `STATUS.md`.
