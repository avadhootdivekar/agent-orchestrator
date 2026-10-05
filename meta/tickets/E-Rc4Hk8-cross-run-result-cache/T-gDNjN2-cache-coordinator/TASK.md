# TASK: T-gDNjN2-cache-coordinator

## Metadata
- Task ID: `T-gDNjN2-cache-coordinator`
- Epic ID: `E-Rc4Hk8-cross-run-result-cache`
- Owner: `developer` (Dev A)
- Created: `2026-10-04`
- Last Updated: `2026-10-05` (Rev 3)
- Status: `Done`
- Estimate: `20 focus hours (2.5 days)` · Sprint 2 · first task of the engine set

## Requirements Mapping
- Requirement IDs: FR-5, FR-6, FR-9 (`saved_*`), FR-16, NFR-10 (M-4, M-11, M-16), NFR-11
- HLD: §8.6.1 (contracts), §8.6.2 (`ResultCache`), §8.6.3 (`records.py`), §8.6.4 (lifecycle and
  reasons)
- ADR-0019: D1, D13, D16, D26, D30, D32, D33, D35

## Description
Implement the single engine-facing implementation of `ResultCacheHook`.

1. **`cache/records.py`.** Builders only (they never mutate `RunState`): `make_hit_record`,
   `make_would_hit_record(..., *, store_reason=None)`,
   `make_miss_record(req, reason, key, mode, source, detail=None, *, store_reason=None)`,
   `make_ineligible_record`. Every free-text value is clipped to the model bounds; `hit` and
   `saved_tokens` are derived by the record model itself.
2. **`cache/coordinator.py`.** `ResultCache`, following HLD §8.6.2 **verbatim**:
   - **Constructor** with injectable collaborators; `heads` defaults to
     `RepoHeadReader(workspace_root=workspace_root)`.
   - **`from_settings(*, workspace_root, settings)`**: no I/O besides the `lstat`-only
     `nested_repo_marker` check, which fills `warnings` (printed by the CLI banner); **never
     named `open`**.
   - **Boundary (D32):** `lookup` and `store_success` never raise (strict mode excepted).
   - **`_lookup`:** author policy first (not opted in → no record); eligibility; artifact-store
     type (`artifact_store_unsupported`); `store.check()`; HEADs; `build_cache_key`; then
     `get_entry`:
     - `CacheUnsafePathError` → miss `unsafe_path`, **no pending, never evicted**, `cache.corrupt`
       WARNING (D33);
     - `CacheIntegrityError` → evict + storable miss; `None` → storable `not_found`; expired →
       evict + storable miss;
     - **shadow** → blob presence check, `would_hit` with a pending store;
     - **on** → restore; a `RestoreMiss` is not storable (evict and delete the blob when the
       matrix says so); a hit touches the entry and emits `cache.hit`.
     - There is **no `refresh` branch** (deferred).
   - **Lazy guard 3:** `_storable()` takes the worktree snapshot **only for storable outcomes**
     (misses and would_hits). A hit never calls it. A snapshot failure gives no pending and
     `store_reason: repo_worktree_probe_failed` on the record; it never makes the task
     ineligible.
   - **`_store_success`:** guard 2 (HEADs, always), guard 1 (key recompute with the preseed),
     guard 3 (snapshot compare), capture, entry build (usage clamped; `cli_version` from the
     key; `ao_version` from `agent_orchestrator.__version__`), `put_entry`, bounded
     `maybe_enforce_limits` with the deferred WARNING.
   - Helpers `_ineligible`, `_miss`, `_storable`, `_miss_storable`, `_evict` (never for
     `unsafe_path`), `_skip`, `_control_paths_abs`, `_key_request`.

## File scope (exclusive)
- `src/agent_orchestrator/cache/coordinator.py`, `src/agent_orchestrator/cache/records.py` (new)
- `tests/cache/test_coordinator.py`, `tests/cache/test_records.py` (new)

## Inputs / Outputs
- **Inputs:** T-28J9oR (settings, record model), T-QgQy08, T-uoYW6b, T-8tr1H4, T-U7ckfd,
  T-u3jG8F. T-HjxNQ0 is optional (`maybe_enforce_limits`; the placeholder returns `None`).
- **Outputs:** `ResultCache`, consumed by T-XpF1pF and T-o95l1M.

## Acceptance Criteria
All tests use fakes (`InMemoryCacheStore`, `FakeRepoHeadReader`, `FakeWorktreeProbe`, a fake
CLI version, a fixed clock, a `LoggerAdapter` capture). Test ids follow HLD §18.1.

1. **U-CO1.** Not opted in → `LookupOutcome(False, None, None)`, no store calls, only a DEBUG event.
2. **U-CO2.** Ineligible → record (`outcome=ineligible`, reason, detail) plus INFO `cache.skip`
   (`phase=lookup`); no pending.
3. **U-CO3.** `not_found` → miss record and pending; `cache.miss` carries `key` and the 11
   `components`.
4. **U-CO4 (hit).** Restore, touch, `cache.hit` with `outputs [{path, sha256}]`, `bytes`,
   `saved_cost_usd`, `source_run_id`, `source_created_at`, `source_ao_version`; the record's
   `saved_*` equals `entry.usage` and `saved_tokens` equals input + output.
5. **U-CO5.** Expired → evict, storable miss `expired`.
6. **U-CO6.** Corrupt entry → evict, `cache.corrupt`, storable miss `corrupt_entry`.
7. **U-CO7.** Corrupt blob on restore → evict, delete the blob, miss `blob_corrupt`, **no**
   pending.
8. **U-CO8.** `restore_failed` and `sensitive_output` on restore → miss, no pending, no eviction.
9. **U-CO9.** `store_unavailable` → miss, no pending; the WARNING is logged once over 3 lookups.
10. **U-CO10 (shadow).** A valid entry with all blobs present → `would_hit` and `cache.would_hit`,
    **no** restore call (spy), a pending store; a missing blob → evict plus storable miss.
11. **U-CO12 (guards).** `repo_head_moved` (also with `include_repo_heads=False`),
    `key_changed_during_run`, `repo_worktree_changed`, and `repo_head_unavailable` from a failing
    HEAD read at settle.
12. **U-CO13 (store path).** The entry's `usage` comes from `ts.cumulative_*`, clamped;
    `source.ao_version == agent_orchestrator.__version__`; `source.cli_version` comes from the
    key; `cache.store` is emitted. Cost values are set on the `TaskRunState` fixture directly
    (no executor involved).
13. **U-CO14 (boundary, D32).** An `OSError` from the store → miss `store_error` with an ERROR
    log; an injected `RuntimeError` → `cache.disabled`, and every later call returns nothing (or
    `StoreResult(False, "cache_disabled")`); with `strict=True` the `RuntimeError` propagates.
14. **U-CO15 (factory).** `from_settings` performs no filesystem writes (spy) and resolves the
    workspace root; nothing on `ResultCache` is named `open`.
15. **U-CO16.** A deferred `maybe_enforce_limits` logs the WARNING `cache.evict`
    (`reason=deferred`); an exception from it is swallowed after a successful `put_entry`.
16. **U-CO17.** A non-`LocalFsArtifactStore` engine store → ineligible
    `artifact_store_unsupported`.
17. **U-CO18 (lazy guard 3).** A hit never calls `WorktreeProbe.snapshot` (spy); a storable miss
    or would_hit calls it exactly once; a probe failure gives no pending, `stored: false`,
    `store_reason: repo_worktree_probe_failed`, and the outcome is unchanged (never ineligible).
18. **U-CO19 (unsafe path).** `get_entry` raising `CacheUnsafePathError` → miss `unsafe_path`, no
    pending, `delete_entry` **never called** (spy), `cache.corrupt` WARNING.
19. **U-CO20 (warnings).** A `.git` above the workspace root yields exactly one entry in
    `ResultCache.warnings`; none otherwise.
20. **U-RC1..RC3.** The builders set `dispatch_cycle`, `at`, `mode`, `mode_source` and
    `store_reason`; they clip texts; a hit record built from hostile-but-valid strings validates.
21. **Hygiene.** Line coverage of `coordinator.py` ≥ 90%; the AST guard passes; ruff
    (≤ 100 columns) and mypy are clean; `pytest -q` has no new failures.

## Test requirements
- `tests/cache/test_coordinator.py`: AC-1..AC-19.
- `tests/cache/test_records.py`: AC-20.

## Risks
- **Event field drift** between code and HLD §15. Mitigation: assert on the event field sets.

## Dependencies
- T-uoYW6b, T-QgQy08, T-U7ckfd, T-u3jG8F, T-8tr1H4, T-28J9oR.

## Pseudocode / Algorithm
```text
HLD §8.6.2 and §8.6.3 verbatim.
```

## Schemas / Interface Notes
- **Interface:** `ResultCacheHook` (HLD §8.6.1), implemented by `ResultCache`.
- **Records:** HLD §8.1.1 and §13.5. **Events:** HLD §15.

## Handoff Boundary
- **Upstream:** the key, eligibility, store, restore, hashing and settings tasks.
- **Downstream:** T-XpF1pF, T-o95l1M, T-JCOAsq.

## Artifacts
- **Docs/comments:** `meta/tickets/E-Rc4Hk8-cross-run-result-cache/T-gDNjN2-cache-coordinator/`
- **Large outputs:** N/A

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: Coordinator.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2: author policy first,
  modes, three guards, error boundary, builders-only records.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 3 (early-gate A4, C; manager
  B): `refresh` removed (U-CO11 retired); lazy guard 3 with not-storable semantics (U-CO18);
  unsafe paths never evicted (U-CO19); `artifact_store_unsupported` test (U-CO17); nested-repo
  warnings from the factory (U-CO20).
- By: developer · Role: developer · Date: 2026-10-05 · Comment: State -> Done. Delivered as commit
  `d14f07d` (`coordinator.py`, `records.py`, `test_coordinator.py`, `test_records.py`); every acceptance
  criterion passes (see `STATUS.md`).
