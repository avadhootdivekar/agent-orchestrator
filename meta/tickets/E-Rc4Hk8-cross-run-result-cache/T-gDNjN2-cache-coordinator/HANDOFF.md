# HANDOFF: T-gDNjN2-cache-coordinator

- Task: `T-gDNjN2-cache-coordinator`
- State: `Done (handoff available)`
- From: `developer` (Dev A)
- To: T-XpF1pF, T-o95l1M, T-JCOAsq
- Commit: `d14f07d` on branch `worktree-agent-a18ce2c08e42a3a5a`.

## What was delivered
- `agent_orchestrator.cache.coordinator`:
  - `ResultCache(store, settings, *, workspace_root, cache_root, heads=None, worktree=None,
    cli_versions=None, environ=None, strict=False, warnings=())` implementing `ResultCacheHook`;
  - `ResultCache.from_settings(*, workspace_root, settings)` (no store I/O; fills `warnings`);
  - `.root`, `.warnings` (tuple of banner strings), `.lookup(request, log)`,
    `.store_success(pending, *, ts, now, log)`;
  - `EXPECTED_ERRORS`, and the `HeadReader` / `Worktree` protocols for injected collaborators.
- `agent_orchestrator.cache.records`: `make_hit_record`, `make_would_hit_record(..., *,
  store_reason=None)`, `make_miss_record(req, reason, key, mode, source, detail=None, *,
  store_reason=None)`, `make_ineligible_record`.

## Frozen names / contracts
- The engine-facing calls never raise (except `strict=True` for an unexpected exception).
- `LookupOutcome.record is None` means the engine writes no record (not opted in, or the cache is
  disabled). `pending` is set only for a storable outcome; a hit never takes the guard-3 snapshot.
- The engine fills `record.ended_at` for a hit and sets `stored` / `store_reason` after
  `store_success`; the builders leave them `None` / `False` (a probe failure at lookup puts
  `store_reason` on the record already).
- `store_success` results: `StoreResult(True)`, or `StoreResult(False, <reason>)` with one of the
  store-skip reasons of HLD 8.6.4 (`repo_head_moved`, `key_changed_during_run`,
  `repo_worktree_changed`, `repo_worktree_probe_failed`, `repo_head_unavailable`, any key-build
  reason, `output_missing`, `output_not_regular_file`, `entry_too_large`, `store_error`,
  `cache_disabled`).
- Events (`extra={"event": ...}` on the adapter handed in): `cache.hit`, `cache.would_hit`,
  `cache.miss`, `cache.skip`, `cache.store`, `cache.evict`, `cache.corrupt`, `cache.disabled`, with
  exactly the HLD 15 field sets (asserted by the tests). The `log` argument must merge call-site
  `extra` with its own (`logging_setup._MergingAdapter` does).
- For T-o95l1M: build with `ResultCache.from_settings(workspace_root=..., settings=...)`, echo
  `rc.warnings` through the banner, and only construct it when `settings.mode` is `on` or `shadow`
  (a coordinator built for any other mode disables itself).
- For T-XpF1pF: `req.now` and `store_success(now=...)` are the engine clock; the coordinator
  never reads the wall clock. `LookupRequest.artifact_store` must be the engine's own
  `LocalFsArtifactStore` (anything else is recorded as `artifact_store_unsupported`).

## Deviations from the HLD code blocks (reason)
See `STATUS.md` "Deviations" 1-9 (uniform D33 handling in `_serve`, `entry_too_large` for an
oversized entry file, fail-closed mode-off coordinator, `cli_versions` callable, protocols for the
fakes, `ttl_days` cast, `detail`/`blob` extras, duration computation, local `_try_resolve`).

## Verification the receiver should run
- `pytest -q tests/cache/test_coordinator.py tests/cache/test_records.py` (107 tests)

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: Handoff stub created.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2 contents.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 3: lazy guard 3, unsafe-path
  handling, `warnings`. State `Draft` mirrors `TASK.md` and `STATUS.md`.
- By: developer · Role: developer · Date: 2026-10-05 · Comment: State -> Done; handoff available
  (commit `d14f07d`). Deviations are small and documented in `STATUS.md`.
