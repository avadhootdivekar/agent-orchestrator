# TASK: T-u3jG8F-cache-restore-capture

## Metadata
- Task ID: `T-u3jG8F-cache-restore-capture`
- Epic ID: `E-Rc4Hk8-cross-run-result-cache`
- Owner: `developer` (Dev B)
- Created: `2026-10-04`
- Last Updated: `2026-10-05` (Rev 3)
- Status: `Draft`
- Estimate: `16 focus hours (2 days)` · Sprint 2 (first item for Dev B)

## Requirements Mapping
- Requirement IDs: FR-8, NFR-4, NFR-10 (M-1, M-3, M-5, M-6, M-8, M-14)
- HLD: §8.5 (capture, restore, atomicity statement, failure matrix)
- ADR-0019: D20, D29

## Description
Implement all workspace-side byte I/O of the cache in `cache/restore.py`, following HLD §8.5
**verbatim**:

- `HashingWriter`; `RestoreResult(files, bytes)`.
- **`capture_outputs(output_abs, store, *, max_entry_bytes) -> list[OutputRecord]`**: opens each
  output with `safeio.open_regular_read` (an irregular output → `StoreSkip`); bounded by a
  running byte budget; records `mode & STORED_MODE_MASK`.
- **`restore_outputs(entry, expected, store, *, workspace_root, max_entry_bytes)`**: the manifest
  must equal the spec set (no duplicates); the size sum must fit the cap; **phase 1** for every
  output (sensitive re-check, `ensure_dir_chain`, `realpath(dest) == dest`, stage to a temp file
  next to the destination with `create_exclusive`, verify size and sha); **phase 2**
  `chmod(mode & 0o755)` and `os.replace`; a `finally` removes every remaining temp file.

Every `RestoreMiss` is non-storable; raise it with the §8.5 failure-matrix values of `reason`,
`evict`, `blob` and `detail`. A store-side `CacheUnsafePathError` from `read_blob` propagates
unchanged (the coordinator handles it as `unsafe_path`).

## File scope (exclusive)
- `src/agent_orchestrator/cache/restore.py` (new)
- `tests/cache/test_restore.py` (new)

## Inputs / Outputs
- **Inputs:** T-FJH6LI (`types`, `safeio`, `InMemoryCacheStore`).
- **Outputs:** `capture_outputs` and `restore_outputs`, consumed by T-gDNjN2.

## Acceptance Criteria
1. **U-R1.** Capturing a regular file records the right sha, size and mode; the blob is in the
   store.
2. **U-R2.** Capture of a directory, a symlink or a FIFO → `StoreSkip(output_not_regular_file)`
   (the FIFO case within 5 s); a missing output → `StoreSkip(output_missing)`.
3. **U-R3.** A total above `max_entry_bytes`, or a file that grows during capture →
   `StoreSkip(entry_too_large)`.
4. **U-R4.** A successful restore writes byte-identical files with mode `stored & 0o755`, replaces
   an existing destination atomically and creates missing parents.
5. **U-R5 (ADV-1, M-1).** A manifest path `../escape`, an absolute path, a duplicate, a missing or
   an extra path → `RestoreMiss(manifest_mismatch, evict=True)`; no file is created outside the
   workspace (list `tmp_path.parent`).
6. **U-R6 (ADV-3).** A corrupt blob → `RestoreMiss(blob_corrupt, evict=True, blob=sha)`; **no
   destination is touched** (mtimes and contents compared); no temp file remains.
7. **U-R7.** A missing blob → `RestoreMiss(blob_missing, evict=True)`.
8. **U-R8.** A destination that is a directory → `RestoreMiss(restore_failed, evict=False)`.
9. **U-R9 (ADV-10, M-14).** `.git/hooks/pre-commit`, `CLAUDE.md` and `.github/workflows/x.yml`
   as destinations → `RestoreMiss(sensitive_output, evict=False)`.
10. **U-R10 (M-5).** A parent that becomes a symlink before staging →
    `RestoreMiss(restore_failed)`; nothing is written through the link.
11. **U-R11.** An `os.replace` failure part-way through the commit → `RestoreMiss(restore_failed)`;
    every temp file is removed.
12. **U-R12 (ADV-8a/b, M-8).** Mode `0o777` is restored as `0o755`; the temp file is opened with
    `O_CLOEXEC` (spy on the `os.open` flags).
13. **Edge cases.** A zero-byte output round-trips; two outputs sharing a blob both restore; a
    stored mode of `0o000` is restored as `0o000`.
14. **Hygiene.** FIFO tests carry `skipif(not hasattr(os, "mkfifo"))`; symlink tests carry
    `skipif(sys.platform == "win32")`; permission failures are simulated, never produced with
    `chmod`; the AST guard passes; ruff (≤ 100 columns) and mypy are clean; `pytest -q` has no
    new failures.

## Test requirements
- `tests/cache/test_restore.py`: AC-1..AC-13, against `InMemoryCacheStore`, plus one smoke test
  against `LocalFsCacheStore` once T-U7ckfd has landed.

## Risks
- **Partial commit on a crash.** Documented in the HLD atomicity statement; never accepted as
  success.

## Dependencies
- T-FJH6LI.

## Pseudocode / Algorithm
```text
HLD §8.5 capture_outputs / restore_outputs verbatim (phase 1 validate+stage+verify ALL, phase 2 commit).
```

## Schemas / Interface Notes
- **Interface:** `capture_outputs(...) -> list[OutputRecord]` (raises `StoreSkip`);
  `restore_outputs(...) -> RestoreResult` (raises `RestoreMiss`).

## Handoff Boundary
- **Upstream:** T-FJH6LI.
- **Downstream:** T-gDNjN2.

## Artifacts
- **Docs/comments:** `meta/tickets/E-Rc4Hk8-cross-run-result-cache/T-u3jG8F-cache-restore-capture/`
- **Large outputs:** N/A

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: Staged, verified restore and safe
  capture.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2: sensitive re-check,
  realpath re-validation, `O_CLOEXEC`, non-storable misses.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 3 (early-gate C; staffing):
  explicit skip markers and simulated permission errors; `CacheUnsafePathError` passes through;
  owner moves to Dev B so T-gDNjN2 is not blocked behind Dev C's queue (HLD §22.1).
