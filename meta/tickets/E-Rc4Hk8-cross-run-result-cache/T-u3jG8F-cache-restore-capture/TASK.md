# TASK: T-u3jG8F-cache-restore-capture

## Metadata
- Task ID: `T-u3jG8F-cache-restore-capture`
- Epic ID: `E-Rc4Hk8-cross-run-result-cache`
- Owner: `developer` (Dev C)
- Created: `2026-10-04`
- Last Updated: `2026-10-05` (Rev 2)
- Status: `Draft`
- Estimate: `16 focus hours (2 days)` · Sprint 2 (first item for Dev C)

## Requirements Mapping
- Requirement IDs: FR-8, NFR-4, NFR-10 (M-1, M-3, M-5, M-6, M-8, M-14)
- HLD: §8.5 (capture, restore, atomicity statement, failure matrix)
- ADR-0019: D20, D29

## Description
Implement all workspace-side byte I/O of the cache in `cache/restore.py`, following HLD §8.5
**verbatim**.

- **`HashingWriter`.** Wraps a binary file object and computes sha256 while writing.
- **`RestoreResult(files, bytes)`.** A dataclass.
- **`capture_outputs(output_abs, store, *, max_entry_bytes) -> list[OutputRecord]`.**
  - Opens each output with `safeio.open_regular_read`; an irregular output raises `StoreSkip`.
  - Bounded by a running byte budget.
  - Records `mode & STORED_MODE_MASK`.
- **`restore_outputs(entry, expected, store, *, workspace_root, max_entry_bytes) -> RestoreResult`.**
  - **Manifest check.** The manifest must equal the spec set, with no duplicates.
  - **Size check.** The sum of sizes must not exceed the cap.
  - **Phase 1, for every output:**
    - re-check that the output is not sensitive;
    - `ensure_dir_chain`;
    - check `realpath(dest) == dest`;
    - stage to a temp file in the destination's directory with `create_exclusive`, with
      `O_CLOEXEC` and mode `0o600`;
    - verify the size and sha.
  - **Phase 2:** `chmod(mode & 0o755)`, then `os.replace` each file.
  - **Cleanup.** A `finally` block removes every remaining temp file.

**Every `RestoreMiss` is non-storable.** The coordinator handles that; this module raises with
the correct `reason`, `evict`, `blob` and `detail` values, following the §8.5 failure matrix.

## File scope (exclusive)
- `src/agent_orchestrator/cache/restore.py` (new)
- `tests/cache/test_restore.py` (new)

## Inputs / Outputs
- **Inputs:** T-FJH6LI (`types`, `safeio`, `InMemoryCacheStore`). The real store is optional.
- **Outputs:** `capture_outputs` and `restore_outputs`, consumed by T-gDNjN2.

## Acceptance Criteria
1. **U-R1.** Capturing a regular file records the right sha, size and mode, and the blob is
   present in the store.
2. **U-R2.** Capture of a directory, a symlink or a FIFO raises
   `StoreSkip(output_not_regular_file)`. The FIFO case returns within 5 s. A missing output raises
   `StoreSkip(output_missing)`.
3. **U-R3.** A total size above `max_entry_bytes` raises `StoreSkip(entry_too_large)`. A file
   that grows during capture also raises it.
4. **U-R4.** A successful restore writes byte-identical files with mode `stored & 0o755`. An
   existing destination is replaced atomically. Missing parent directories are created.
5. **U-R5 (ADV-1, M-1).** Each of these raises `RestoreMiss(manifest_mismatch, evict=True)`:
   - a manifest path `../escape`;
   - an absolute path;
   - a duplicate path;
   - a missing path;
   - an extra path.

   No file is created outside the workspace. Assert by listing `tmp_path.parent`.
6. **U-R6 (ADV-3).** A corrupt blob, with wrong bytes or a wrong size, raises
   `RestoreMiss(blob_corrupt, evict=True, blob=sha)`. **No destination is touched**: compare
   destination mtimes and contents before and after. No temp file remains.
7. **U-R7.** A missing blob raises `RestoreMiss(blob_missing, evict=True)`.
8. **U-R8.** A destination that is a directory raises `RestoreMiss(restore_failed, evict=False)`.
9. **U-R9 (ADV-10, M-14).** A sensitive destination raises
   `RestoreMiss(sensitive_output, evict=False)`. Cover `.git/hooks/pre-commit`, `CLAUDE.md` and
   `.github/workflows/x.yml`.
10. **U-R10 (M-5).** A destination whose parent becomes a symlink before staging raises
    `RestoreMiss(restore_failed)`. Nothing is written through the link.
11. **U-R11.** An `os.replace` failure part-way through the commit raises
    `RestoreMiss(restore_failed)`, and every temp file is removed.
12. **U-R12 (ADV-8a/b, M-8).** Mode `0o777` is restored as `0o755`. An entry with mode `0o4777`
    never reaches restore, because the entry model rejects it (covered with T-FJH6LI). The temp
    file is opened with `O_CLOEXEC`: assert on the flags passed to `os.open` with a spy.
13. **Edge cases.**
    - A zero-byte output round-trips.
    - Two outputs sharing one blob both restore.
    - A stored mode of `0o000` is restored as `0o000`.
14. **Hygiene.** The AST guard passes. `ruff` and `mypy` are clean. `pytest -q` has no new
    failures.

## Test requirements
- `tests/cache/test_restore.py`: AC-1..AC-13. Use `InMemoryCacheStore`; one smoke test runs
  against `LocalFsCacheStore` once T-U7ckfd has landed.

## Risks
- **Partial commit on a crash.** Documented in the HLD atomicity statement: never accepted as
  success.
- **umask affects created parent directories.** That is intended.

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
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2 changes:
  - sensitive-destination re-check (security S3);
  - `realpath` re-validation and component-wise directory creation (security S4);
  - `O_CLOEXEC` (developer #16);
  - all restore misses are non-storable (developer #11);
  - moved to Sprint 2 to balance Dev C's queue.
