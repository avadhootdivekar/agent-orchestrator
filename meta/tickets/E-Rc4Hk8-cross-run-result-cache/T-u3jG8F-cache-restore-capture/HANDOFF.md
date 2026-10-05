# HANDOFF: T-u3jG8F-cache-restore-capture

- Task: `T-u3jG8F-cache-restore-capture`
- State: `Done (handoff available)`
- From: `developer` (Dev B)
- To: T-gDNjN2 (and T-JCOAsq for ADV integration)

## What was delivered
- `agent_orchestrator.cache.restore` (commit `87814ea`, branch `worktree-agent-a18ce2c08e42a3a5a`):
  - `HashingWriter(sink)`: `.write(data) -> int`, `.hexdigest()`, `.size`;
  - `RestoreResult(files: int, bytes: int)` (frozen dataclass);
  - `capture_outputs(output_abs, store, *, max_entry_bytes) -> list[OutputRecord]`, raises `StoreSkip`;
  - `restore_outputs(entry, expected, store, *, workspace_root, max_entry_bytes) -> RestoreResult`,
    raises `RestoreMiss` (always non-storable) and lets a store `CacheUnsafePathError` through.

## Frozen names / contracts
- The signatures above and the `RestoreMiss` values of the HLD 8.5 failure matrix (reason, evict,
  blob, detail): `manifest_mismatch` (evict), `corrupt_entry` (evict; size sum), `blob_missing`
  (evict), `blob_corrupt` (evict, `blob=sha`), `sensitive_output` (no evict), `restore_failed`
  (no evict; `detail` is the exception class name).
- `StoreSkip` reasons: `output_missing`, `output_not_regular_file`, `entry_too_large`,
  `store_error` (detail = the relative path). Any other store exception (`CacheUnsafePathError`,
  `OSError` from `put_blob`) propagates; the coordinator decides.

## Deviations from the HLD code block (reason)
1. Phase 1 also refuses a destination that is a directory (`os.path.isdir` -> `restore_failed`). The
   HLD would hit it at `os.replace` in phase 2, after earlier outputs were committed; the matrix
   row and the "validate everything before touching any destination" statement are better served.
2. The `0o777` parent-directory mode is a named module constant (`_RESTORE_DIR_MODE`).
3. `HashingWriter` is handed to `read_blob` through `typing.cast(BinaryIO, ...)` (it is the minimal
   `.write()` sink, not a full `BinaryIO`); its `write` returns the sink's count (or `len(data)`).
4. `capture_outputs` records the actual stored size (`ref.size`), which can differ from the `fstat`
   size only if the file changed while being read.

## Verification the receiver should run
- `pytest -q tests/cache/test_restore.py`

## G1a remediation deviations (commit `763375f`)
- `capture_outputs` refuses (`StoreSkip(output_not_regular)`) any path whose `realpath` differs from itself (a swapped parent directory); signature unchanged.
- `restore_outputs` applies the mode with `os.fchmod` on the open staging descriptor after hash verification (no path chmod, so U-R11's 'private until commit' test now spies `fchmod`); each existing destination is hard-linked to a `<staging>.bak` before phase 2 and a failing rename rolls the earlier renames back. Residuals: created parent directories stay; where hard links are unavailable that one file is not rolled back.
- A non-integrity `CacheError` from `read_blob` (transient I/O) is `RestoreMiss(store_error, evict=False)`; `CacheUnsafePathError` still propagates.
- Sensitive-path matching is case-insensitive (see T-uoYW6b); the old 'lower-case lookalike is not sensitive' test was inverted.

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: Handoff stub created.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2 contents.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 3: owner Dev B; test
  hygiene. State `Draft` mirrors `TASK.md` and `STATUS.md`.
- By: developer · Role: developer · Date: 2026-10-05 · Comment: State -> Done; handoff available (commit `87814ea`). Deviations 1-4 above are small.
- By: developer · Role: developer · Date: 2026-10-05 · Comment: G1a remediation (SEC-05, SEC-07, SEC-09 (restore/capture)) in commit `763375f`; deviations listed above; frozen names unchanged.
