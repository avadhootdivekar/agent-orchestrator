# HANDOFF: T-XpF1pF-cache-engine-integration

- Task: `T-XpF1pF-cache-engine-integration`
- State: `Done (handoff available)`
- From: `developer` (Dev A)
- To: T-o95l1M, T-JCOAsq (Part 2), T-fXWbqg (gate G1b)
- Commit: `b7ca9c7` on branch `worktree-agent-a18ce2c08e42a3a5a`.

## What was delivered
- `Orchestrator(..., result_cache: ResultCacheHook | None = None)` (last keyword parameter);
  `_RunContext.result_cache_pending`; call sites (c) (before the budget estimate) and (d) (top of
  the `succeeded` settle branch); private methods `_reverse_stale_charge`,
  `_result_cache_lookup` and `_result_cache_store`.
- `tests/cache/test_engine_result_cache.py` (25 tests): I-3..I-8, I-18, I-21, I-22, I-27, U-AST-E,
  seam wiring. Reusable helpers there: `TickingClock`, `CostlyFakeExecutor`, `SpyCache`,
  `SpyBudget`, `World`.

## Frozen names / contracts
- Seam positions (HLD 8.7.2) and the success-side-effect rule (HLD 8.7.4).
- The engine passes its own `self._store` as `LookupRequest.artifact_store`; `injected` is derived
  from `RunState.spawned_by` (no `.origin` comparison anywhere under `src/`).
- A hit settles in `_result_cache_lookup` (status `succeeded`, `ended_at` bound on the record,
  `task.end` with `cached: true`, stale charge reversed through `_reverse_stale_charge`).

## For T-o95l1M
- Construct `ResultCache.from_settings(...)` only for mode `on` or `shadow` and pass it as
  `result_cache=`; pass nothing (None) otherwise. The engine never imports a cache module at
  runtime in the cache-off path.

## For the approval-gate merge (E-Ag7Pw3)
- Any approval or human-gate check must sit BEFORE call site (c) in
  `_prepare_and_maybe_dispatch`; G2 verifies a hit never satisfies or bypasses one.

## Verification the receiver should run
- `pytest -q tests/cache/test_engine_result_cache.py tests/cache/test_noop_proof.py`
- `git diff --numstat ed8b8c3 -- src/agent_orchestrator/engine.py` (133 added, 30 removed)

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: Handoff stub created.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2 contents.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 3: shared
  `_reverse_stale_charge`, honest budget. State `Draft` mirrors `TASK.md` and `STATUS.md`.
- By: developer · Role: developer · Date: 2026-10-05 · Comment: State -> Done; handoff available
  (commit `b7ca9c7`).
