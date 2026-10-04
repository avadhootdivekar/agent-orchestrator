# HANDOFF: T-FJH6LI-cache-contracts

- Task: `T-FJH6LI-cache-contracts`
- State: `Draft` (handoff not yet available)
- From: `developer` (Dev A)
- To:
  - T-28J9oR: commit 1 (`constants.py`).
  - T-8tr1H4: commit 2 (`safeio.py`).
  - T-uoYW6b, T-U7ckfd, T-u3jG8F, T-gDNjN2, T-HjxNQ0, T-eyn5UG, T-6tRKml and T-JCOAsq: commit 3.

## What will be handed over
- `agent_orchestrator.cache.constants`: every §8.1.5 constant, `REASON_*` and `EVENT_*`.
- `agent_orchestrator.cache.safeio`:
  - the flags `O_SAFE_READ` and `O_SAFE_CREATE`;
  - the functions `open_regular_read`, `read_bounded`, `create_exclusive`, `check_dir_chain`,
    `ensure_dir_chain`, `check_root_dir`, `is_sensitive_rel_path`, `posix_rel` and
    `strip_control_chars`;
  - the errors `SafeIOError`, `NotRegularFileError`, `TooLargeError` and `UnsafePathError`.
- `agent_orchestrator.cache.types`:
  - `canonical_json`;
  - the models `OutputRecord`, `EntryUsage`, `EntrySource`, `KeySummary` and `CacheEntry`, and
    `parse_entry_bytes`;
  - the dataclasses `Digest`, `BlobRef`, `EntryInfo`, `CacheStats`, `PruneReport`,
    `ClearReport`, `VerifyProblem`, `VerifyReport`, `KeyRequest`, `KeyDeps`, `CacheKey`,
    `LookupRequest`, `PendingStore`, `LookupOutcome` and `StoreResult`;
  - `ResultCacheHook`, `CacheStore` and `CacheAdmin`;
  - the errors.
- `tests/cache/fakes.py`: `InMemoryCacheStore`, `FakeRepoHeadReader`, `FakeWorktreeProbe`,
  `fake_cli_version_of` and a fake VCS runner.
- `tests/cache/store_contract.py`: the reusable store contract suite.
- `tests/fixtures/result_cache/corpus/`: the hostile-entry corpus (AC-5).

## Frozen names / contracts
- Every name above, plus the signatures in HLD §8.2.1, §8.2.2, §8.4.3, §8.6.1 and §14.1.
- A change requires a note in each consumer's `STATUS.md` and an HLD §14.1 update.

## Verification the receiver should run
- `pytest -q tests/cache/test_types.py tests/cache/test_safeio.py tests/cache/test_ast_guard.py`
- `python -c "import agent_orchestrator.cache.types, agent_orchestrator.cache.safeio"` (no
  import cycle)

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: Handoff stub created.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2 contents (safeio, parse
  boundary, split ABCs, hook protocol, canonical_json, corpus). State `Draft` mirrors `TASK.md`
  and `STATUS.md`.
