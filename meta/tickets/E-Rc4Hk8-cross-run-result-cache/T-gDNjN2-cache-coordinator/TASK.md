# TASK: T-gDNjN2-cache-coordinator

## Metadata
- Task ID: `T-gDNjN2-cache-coordinator`
- Epic ID: `E-Rc4Hk8-cross-run-result-cache`
- Owner: `developer` (Dev A)
- Created: `2026-10-04`
- Last Updated: `2026-10-05` (Rev 2)
- Status: `Draft`
- Estimate: `20 focus hours (2.5 days)` · Sprint 2

## Requirements Mapping
- Requirement IDs: FR-5, FR-6, FR-9 (`saved_*`), FR-16, NFR-10 (M-11, M-16), NFR-11
- HLD: §8.6.1 (contracts), §8.6.2 (`ResultCache`), §8.6.3 (`records.py`), §8.6.4 (lifecycle
  and reasons)
- ADR-0019: D1, D13, D16, D26, D30, D32

## Description
Implement the single engine-facing implementation of `ResultCacheHook`.

1. **`cache/records.py`.** Builders only; they never mutate `RunState`:
   - `make_hit_record`;
   - `make_would_hit_record`;
   - `make_miss_record(req, reason, key, mode, source, detail=None)`;
   - `make_ineligible_record`.

   Every free-text value is clipped to the model bounds.
2. **`cache/coordinator.py`.** `ResultCache`, following HLD §8.6.2 **verbatim**:
   - **Constructor.** `__init__(store, settings, *, workspace_root, cache_root, heads=None,
     worktree=None, cli_versions=None, environ=None, strict=False)`. Every collaborator is
     injectable.
   - **Factory.** `from_settings(*, workspace_root, settings)`. It does no I/O and is
     **never named `open`**, because the `engine.py` static audit regex would flag it.
   - **`root` property.**
   - **Engine-facing boundary (D32).** `lookup` and `store_success` never raise:
     - `EXPECTED_ERRORS` becomes a miss or skip with an ERROR log;
     - any other exception sets `self._disabled`, emits `cache.disabled` and returns nothing,
       unless `strict` is set, in which case it re-raises.
   - **`_lookup`:**
     - check the author policy first. A task that is not opted in gets **no record**; at most a
       DEBUG `cache.skip`.
     - then eligibility;
     - then the artifact-store type check;
     - then `store.check()`. A failure gives `store_unavailable`, warned once per run.
     - then the HEAD read;
     - then `build_cache_key`;
     - then the worktree snapshot.
     - **Mode handling:**
       - `refresh` skips the lookup;
       - `shadow` checks blob presence and returns `would_hit` with a pending store;
       - `on` restores. On restore failure the entry is evicted and the blob deleted when
         `m.blob` is set; the result is **not storable**.
     - On a hit, touch the entry and emit `cache.hit` with the outputs audit trail.
   - **`_store_success`:**
     - **guard 2:** HEADs, always checked;
     - **guard 1:** the key recompute with the preseed;
     - **guard 3:** the worktree snapshot;
     - then capture, build the entry (usage clamped to the bounds; `cli_version` from
       `CacheKey.cli_version`; `ao_version` from `agent_orchestrator.__version__`), `put_entry`
       and `maybe_enforce_limits`. A deferred enforcement logs `cache.evict` with
       `reason=deferred` at WARNING.
   - **Helpers:** `_ineligible`, `_miss` (the event carries `components`), `_evict`
     (`corrupt=True` also emits `cache.corrupt`), `_skip`, `_control_paths_abs` and
     `_key_request`.

## File scope (exclusive)
- `src/agent_orchestrator/cache/coordinator.py`, `src/agent_orchestrator/cache/records.py` (new)
- `tests/cache/test_coordinator.py`, `tests/cache/test_records.py` (new)

## Inputs / Outputs
- **Inputs:** T-28J9oR (settings, records model), T-QgQy08, T-uoYW6b, T-8tr1H4, T-U7ckfd and
  T-u3jG8F. T-HjxNQ0 is optional (`maybe_enforce_limits`; the placeholder returns `None`).
- **Outputs:** `ResultCache`, consumed by T-XpF1pF (engine) and T-o95l1M (CLI construction).

## Acceptance Criteria
All tests use fakes: `InMemoryCacheStore`, `FakeRepoHeadReader`, `FakeWorktreeProbe`, a fake CLI
version, a fixed clock and a `LoggerAdapter` capture.

1. **U-CO1.** A task that is not opted in gives `LookupOutcome(False, None, None)`, no store
   calls, and only a DEBUG event.
2. **U-CO2.** An ineligible task gives a record with `outcome=ineligible`, the reason and the
   detail, plus an INFO `cache.skip` with `phase=lookup`. No pending store.
3. **U-CO3.** `not_found` gives a miss record and a pending store. The `cache.miss` event carries
   `key` and the 11 `components`.
4. **U-CO4 (hit).** A hit restores through restore, touches the entry, and emits `cache.hit` with
   `outputs [{path, sha256}]`, `bytes`, `saved_cost_usd`, `source_run_id`,
   `source_created_at` and `source_ao_version`. The record's `saved_*` equals `entry.usage`.
5. **U-CO5.** An expired entry is evicted and gives a miss (`expired`) with a pending store.
6. **U-CO6.** A corrupt entry is evicted, emits `cache.corrupt`, and gives a miss
   (`corrupt_entry`) with a pending store.
7. **U-CO7.** A corrupt blob on restore: the entry is evicted, the blob deleted, and the miss
   (`blob_corrupt`) has **no** pending store.
8. **U-CO8.** `restore_failed` and `sensitive_output` on restore give a miss with no pending
   store and no eviction.
9. **U-CO9.** `store_unavailable` gives a miss with no pending store. The WARNING is logged once,
   across 3 lookups.
10. **U-CO10 (shadow).**
    - A valid entry with all blobs present gives a `would_hit` record and `cache.would_hit`, with
      **no** restore call (spy) and a pending store.
    - A missing blob gives eviction plus a miss (`blob_missing`) with a pending store.
11. **U-CO11 (refresh).** `get_entry` is never called. The miss reason is `refresh`, with a
    pending store. A later store overwrites the entry.
12. **U-CO12 (guards).** Each of these makes `store_success` return the reason shown:
    - a HEAD moved between lookup and store gives `repo_head_moved`, also when
      `include_repo_heads=False`;
    - a mutated input gives `key_changed_during_run`;
    - a changed snapshot gives `repo_worktree_changed`;
    - a failing HEAD read gives `repo_head_unavailable`.
13. **U-CO13 (store path).** A successful store writes an entry:
    - `usage` is copied from `ts.cumulative_*` and clamped (a huge cost is capped at
      `MAX_ENTRY_COST_USD`);
    - `source.ao_version == agent_orchestrator.__version__`;
    - `source.cli_version` is taken from the key;
    - the `cache.store` event is emitted.
14. **U-CO14 (boundary, D32).**
    - An `OSError` from the store gives a miss record with `store_error` and an ERROR log.
    - An injected `RuntimeError` gives `cache.disabled`; every later `lookup` and
      `store_success` returns nothing, or `StoreResult(False, "cache_disabled")`.
    - With `strict=True` the `RuntimeError` propagates.
15. **U-CO15 (factory).** `from_settings` performs no filesystem writes (spy) and resolves the
    workspace root. No attribute or method of `ResultCache` is named `open`.
16. **U-CO16 (inline enforcement).** A deferred `maybe_enforce_limits` logs the WARNING
    `cache.evict` with `reason=deferred`. An exception from it is swallowed after a successful
    `put_entry`.
17. **U-RC1..RC3.** The builders set `dispatch_cycle`, `at`, `mode` and `mode_source`. They clip
    texts. A `make_hit_record` built from an entry carrying hostile-but-valid strings produces a
    valid `ResultCacheRecord`.
18. **Hygiene.**
    - Line coverage of `coordinator.py` and `records.py` is at least 90%.
    - The AST guard passes.
    - `ruff` and `mypy` are clean.
    - `pytest -q` has no new failures.

## Test requirements
- `tests/cache/test_coordinator.py`: AC-1..AC-16.
- `tests/cache/test_records.py`: AC-17.

## Risks
- **Integration order.** Restore lands mid-sprint. Mitigation: develop against
  `InMemoryCacheStore` and stub restore functions first. Integrate T-u3jG8F around day 4.
- **Event field drift** between the code and HLD §15. Mitigation: assert on the event field sets.

## Dependencies
- T-uoYW6b, T-QgQy08, T-U7ckfd, T-u3jG8F, T-8tr1H4, T-28J9oR.

## Pseudocode / Algorithm
```text
HLD §8.6.2 ResultCache (lookup boundary, _lookup with modes, store_success boundary, _store_success with
three guards, _control_paths_abs) and §8.6.3 builders verbatim.
```

## Schemas / Interface Notes
- **Interface:** `ResultCacheHook` (HLD §8.6.1), implemented by `ResultCache`.
- **Records:** HLD §8.1.1 and §13.5.
- **Events:** HLD §15.

## Handoff Boundary
- **Upstream:** the key, eligibility, store, restore, hashing and settings tasks.
- **Downstream:** T-XpF1pF, T-o95l1M, T-JCOAsq.

## Artifacts
- **Docs/comments:** `meta/tickets/E-Rc4Hk8-cross-run-result-cache/T-gDNjN2-cache-coordinator/`
- **Large outputs:** N/A

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: Coordinator: lookup, store and
  records.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2, re-estimated from 16 h to
  20 h. Changes:
  - **Author policy first** (double opt-in).
  - **Modes** `shadow` and `refresh`.
  - **Three store guards** (reviewer R1).
  - **Error boundary and strict flag** (D32; reviewer R2, developer #9).
  - **No more `RunState` mutation.** Records are built here, but the engine writes them
    (reviewer R5).
  - **Diagnostics and provenance:** miss components (D30); `__version__` provenance
    (developer #8).
