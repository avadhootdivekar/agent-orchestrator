# HANDOFF: T-FJH6LI-cache-contracts

- Task: `T-FJH6LI-cache-contracts`
- State: `Done (handoff available)`
- From: `developer` (Dev A)
- To:
  - T-28J9oR: commit 1 (`constants.py`) = `9b24194`.
  - Every other cache task, T-8tr1H4 included: commit 3 (the whole task) = `d1c1d08`.
- Commits: `9b24194` (constants), `5628556` (safeio), `d1c1d08` (types, fakes, contract suite,
  corpus, AST guard), on branch `worktree-agent-a18ce2c08e42a3a5a`.

## What was delivered
- `agent_orchestrator.cache.constants`: every §8.1.5 constant (incl. `DEFAULT_TASK_CACHE_POLICY`,
  `AGENT_KEY_FIELDS`, `AGENT_NON_KEY_FIELDS`), all `REASON_*` and the eight `EVENT_*`. Imports only `re`.
- `agent_orchestrator.cache.safeio`: `O_SAFE_READ`, `O_SAFE_CREATE`, `SafeIOError`,
  `NotRegularFileError`, `TooLargeError`, `UnsafePathError`, `open_regular_read`, `read_bounded`,
  `create_exclusive`, `check_dir_chain`, `ensure_dir_chain`, `check_root_dir`,
  `is_sensitive_rel_path`, `posix_rel`, `strip_control_chars`.
- `agent_orchestrator.cache.types`:
  - `canonical_json`;
  - `PathStr`, `TextStr`, `HexStr`; `OutputRecord`, `EntryUsage`, `EntrySource`, `KeySummary`,
    `CacheEntry` (`to_canonical_bytes`), `parse_entry_bytes`;
  - dataclasses `Digest`, `BlobRef`, `EntryInfo`, `CacheStats`, `PruneReport`, `ClearReport`,
    `VerifyProblem`, `VerifyReport`, `KeyRequest`, `KeyDeps`, `CacheKey`, `LookupRequest`,
    `PendingStore`, `LookupOutcome`, `StoreResult`;
  - `ResultCacheHook` (Protocol), `CacheStore`, `CacheAdmin` (both PROVISIONAL);
  - errors `CacheError`, `CacheIntegrityError`, `CacheUnsafePathError`, `CacheBlobMissingError`,
    `CacheTooLargeError`, `CacheLayoutError`, `UncacheableError`, `StoreSkip`, `RestoreMiss`.
- Tests and fixtures:
  - `tests/cache/fakes.py`: `InMemoryCacheStore`, `FakeRepoHeadReader`, `FakeWorktreeProbe`,
    `fake_cli_version_of`, `FakeVcsRunner`, plus helpers `make_entry`, `key_of`, `sha_of`,
    `FIXED_CREATED_AT`;
  - `tests/cache/store_contract.py`: `CacheStoreContract` (CacheStore half only);
  - `tests/fixtures/result_cache/corpus/`: 28 hostile files plus `MANIFEST.json`.

## Frozen names / contracts
- Every name above, plus the signatures in HLD §8.2.1, §8.2.2, §8.4.3, §8.6.1 and §14.1.
- A change requires a note in each consumer's `STATUS.md` and an HLD §14.1 update.

### Clarifications where the HLD was not copy-ready (now frozen)
- **Errors.** `CacheError(reason, detail="")` and every subclass except the next one take
  `(reason, detail="")`; `.detail` is clipped to `MAX_TEXT_CHARS`. **`CacheBlobMissingError(sha256)`**
  takes only the blob sha (the HLD §8.4.3 `read_blob` pseudocode form): `.reason ==
  "blob_missing"`, `.detail == sha256`. `UncacheableError(reason, detail="")`,
  `StoreSkip(reason, detail="")`, `RestoreMiss(reason, evict=False, blob=None, detail="")`.
- **Evict-only reasons** are `REASON_EVICT_LRU` (`"lru"`), `REASON_EVICT_INVALID` (`"invalid"`),
  `REASON_EVICT_DEFERRED` (`"deferred"`); the other evict reasons reuse their miss constants.
  `REASON_OUTPUT_NOT_REGULAR = "output_not_regular_file"`. `REASON_NOT_OPTED_IN` exists (DEBUG
  `cache.skip` only, never a record). Ruled-field reasons: `REASON_EMIT_TASKS`,
  `REASON_TASK_MANIFEST_PATH`, `REASON_OUTPUT_MANIFEST`, `REASON_PRE_HOOK`, `REASON_POST_HOOK`.
- **Admin dataclasses** (shape derived from §13.4 and the §8.4.3 pseudocode; all frozen, all
  fields after the required ones have defaults, so adding a field is non-breaking):
  - `EntryInfo(key, size, mtime, entry=None, error=None, anomaly=False)`: `anomaly=True` marks a
    non-entry (symlinked shard, junk, non-hex name) that is counted and never parsed or deleted.
  - `PruneReport(removed_entries: Mapping[str, int] = {}, removed_blobs=0, removed_tmp=0,
    removed_trash=0, bytes_before=0, bytes_after=0, dry_run=False, deferred=False)` and
    `total_removed` (sum of `removed_entries`). `PruneReport(deferred=True)` is valid.
  - `ClearReport(removed_entries=0, removed_blobs=0, bytes_freed=0)`.
  - `VerifyProblem(kind, key=None, blob=None, detail=None)`;
    `VerifyReport(ok, problems=(), entries_checked=0, blobs_checked=0)` (no `repaired`).
  - `CacheStats`: flat form of `ao.result-cache.stats/v1` (`root`, `exists`, counts,
    `entries_bytes`, `blobs_bytes`, `referenced_blobs_bytes`, `orphan_blobs_bytes`, `total_bytes`,
    `max_bytes`, `max_entry_bytes`, `ttl_days`, `oldest_created_at`, `newest_created_at`).
- **safeio semantics.**
  - `check_dir_chain(root, path)` checks root (inclusive) and every existing component below it,
    stops at the first missing one, raises `UnsafePathError` for a symlink, a non-directory, or a
    path lexically outside root.
  - `ensure_dir_chain(root, path, mode)` creates root too if missing (its parent must exist) and
    never creates through a symlink.
  - `check_root_dir(root, *, workspace_root)` returns silently when the root does not exist
    (a write creates it lazily); it checks every existing component between the workspace and the
    root (`.orchestrator`), the root itself (symlink, directory, owner, group/other write ->
    `fchmod 0o700` when owned else `UnsafePathError`) and that the realpath stays in the
    workspace. All failures are `UnsafePathError` (the store maps them to `CacheLayoutError`).
  - `is_sensitive_rel_path` is exact-case (D29): `docs/claude.md` is not sensitive.
- **Fakes.**
  - `fake_cli_version_of(version=..., *, versions=None, error=None)` is a FACTORY returning a
    callable `binary -> version`; pass the result to `KeyDeps(cli_version_of=...)`.
  - `FakeRepoHeadReader.read` omits repo ids it has no head for (non-git).
  - `FakeWorktreeProbe(*snapshots)` returns them in order (the last repeats).
  - `.fail(exc)` / `.then(...)` / `InMemoryCacheStore.fail_with(method, exc)` make any fake raise.
  - `FakeVcsRunner` is an `isolation.git.Runner`: `.on(*argv_tokens, stdout=, returncode=, raises=)`.
- **Contract suite.** Subclass `tests.cache.store_contract.CacheStoreContract` and override the
  `store` fixture (for `LocalFsCacheStore` use a tmp_path workspace).

### Deviations from the HLD code blocks (reason)
- `OutputRecord.kind: Literal["file"] = "file"` instead of `= KIND_FILE`: mypy cannot narrow a
  plain `str` constant to a `Literal`; `constants.py` may import only `re`, so it cannot be a
  `Final`. A test pins `KIND_FILE == "file"`.
- The `TYPE_CHECKING` import of `AgentSpec`, `ResultCacheRecord`, ... from `models` carries
  `# type: ignore[attr-defined,unused-ignore]` because `ResultCacheRecord` does not exist until
  T-28J9oR. **T-28J9oR: remove the ignore once `ResultCacheRecord` is in `models.py`.**
- `types.py` does not import `safeio` (the HLD layering table allows it; nothing in `types` uses it).

## Verification the receiver should run
- `pytest -q tests/cache` (246 tests)
- `python -c "import agent_orchestrator.cache.types, agent_orchestrator.cache.safeio"`

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: Handoff stub created.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2 contents.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 3: type-only model reference,
  fixed commit numbering, new constants and `CacheUnsafePathError`. State `Draft` mirrors
  `TASK.md` and `STATUS.md`.
- By: developer · Role: developer · Date: 2026-10-05 · Comment: State -> Done; handoff available.
  Delivered as `9b24194`, `5628556`, `d1c1d08`. Clarifications and deviations listed above are the
  frozen contract.
