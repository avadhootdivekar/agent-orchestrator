# TASK: T-U7ckfd-cache-store-core

## Metadata
- Task ID: `T-U7ckfd-cache-store-core`
- Epic ID: `E-Rc4Hk8-cross-run-result-cache`
- Owner: `developer` (Dev C)
- Created: `2026-10-04`
- Last Updated: `2026-10-05` (Rev 2)
- Status: `Draft`
- Estimate: `16 focus hours (2 days)` · Sprint 1, Wave 2

## Requirements Mapping
- Requirement IDs: FR-7, NFR-4, NFR-5, NFR-6, NFR-10 (M-2, M-4, M-6, M-7)
- HLD: §8.4.1 (layout, factory, per-operation checks), §8.4.2 (`parse_entry_bytes` use),
  §8.4.3 (`CacheStore` half of `LocalFsCacheStore`, `is_expired`), §8.4.4
- ADR-0019: D2, D17, D18

## Description
Implement the hot-path half of `LocalFsCacheStore(CacheStore, CacheAdmin)` in `cache/store.py`.

- **Factory.** `for_workspace(workspace_root, *, max_bytes, ttl_days)`. It does no I/O.
- **`check()`.** The coordinator pre-flight. It creates nothing. When the root exists it runs
  `check_root_dir` and reads the layout; an unknown schema raises `CacheLayoutError`.
- **`_checks(target_dir)`.** Root plus component chain, run before **every** operation.
- **`ensure_layout()`.** Lazy, component-wise: `.gitignore`, `CACHEDIR.TAG`, `layout.json`,
  `entries/v1/`, `blobs/` and `tmp/`, with mode `0o700` on creation.
- **Path builders.** `_entry_path` and `_blob_path`. Each validates with
  `SHA256_HEX_RE.fullmatch` **before** building any path.
- **Entry methods.** `get_entry`, which uses `safeio.read_bounded` and then
  `types.parse_entry_bytes`; `put_entry`, which writes canonical bytes; `touch_entry`; and
  `delete_entry`.
- **Blob methods.** `has_blob`; `put_blob`, which is bounded, writes tmp then `os.replace`, and
  refreshes the mtime on dedupe; `read_blob`, which is bounded and FIFO-safe; and `delete_blob`.
- **Write helpers.** `_atomic_write_bytes` and `_new_tmp`. Temp names are
  `<pid>-<thread_ident>-<uuid4hex>.<kind>.tmp`.
- **`is_expired(created_at, now, ttl_days)`.** The single TTL helper, used by both lookup and
  prune.
- **`maybe_enforce_limits(*, now)`.** A **placeholder** in this task that returns `None`, with a
  `TODO(T-HjxNQ0)` comment. T-HjxNQ0 replaces it with the bounded implementation.

## File scope (exclusive)
- `src/agent_orchestrator/cache/store.py`: the `CacheStore` methods, the factory, the checks,
  `ensure_layout`, the write helpers and `is_expired`. T-HjxNQ0 later adds the `CacheAdmin`
  methods and the real `maybe_enforce_limits`.
- `tests/cache/test_store_core.py` (new); it reuses `tests/cache/store_contract.py`.

## Inputs / Outputs
- **Inputs:** T-FJH6LI (`types`, `safeio`, `constants`, contract suite).
- **Outputs:** a working local store for the coordinator, restore and maintenance.

## Acceptance Criteria
1. **U-ST1.** `for_workspace` does no I/O: `os.mkdir`, `os.open` and `os.stat` are spied and
   uncalled. `check()` on a workspace with no cache directory creates nothing.
2. **U-ST2.** The first `put_entry` creates the §8.4.1 layout:
   - the directory is `0o700`;
   - `.gitignore` contains `*`;
   - the `CACHEDIR.TAG` signature is correct;
   - `layout.json` holds `{"schema": "ao.result-cache.layout/v1"}`.

   `git check-ignore` reports the cache files as ignored inside a temp repo.
3. **U-ST3 (M-2).** `get_entry("../x")`, `get_entry("A"*64)` and a 63-character key raise
   `ValueError` **before** any filesystem call (spy on `os.open` and `os.lstat`). The same holds
   for blob shas.
4. **U-ST4.** An entry is written to `entries/v1/<k[:2]>/<k>.json` as canonical bytes: equal to
   `entry.to_canonical_bytes()`, with sorted keys and compact separators.
5. **U-ST5.** The contract suite from T-FJH6LI passes against `LocalFsCacheStore`.
6. **U-ST6 (hostile entry files).** Each corpus file placed at an entry path makes `get_entry`
   raise `CacheIntegrityError`, and nothing else:
   - a FIFO, which returns within 5 s;
   - a symlink;
   - a 2 MiB file;
   - invalid JSON;
   - a file with a wrong key.
7. **U-ST7 (M-4).** A symlinked root, a symlinked `.orchestrator`, a symlinked shard directory or
   a symlinked blob is refused. The error is `CacheLayoutError` for root-level cases and
   `CacheIntegrityError` for shard and blob cases.
8. **U-ST8.** A root owned by another uid, simulated, raises `CacheLayoutError`. A group-writable
   root that we own is chmodded to `0o700`.
9. **U-ST9 (atomicity).** If `os.replace` raises during `put_entry` or `put_blob`, no destination
   file exists and no temp file remains.
10. **U-ST10 (dedupe).** Putting identical bytes twice gives one blob file, `BlobRef.new` is False
    the second time, and the mtime is refreshed.
11. **U-ST11 (bounds).**
    - `put_blob` beyond `max_bytes` raises `CacheTooLargeError` and leaves no temp file.
    - `read_blob` never writes more than `max_bytes + 1` bytes into `dest`.
12. **U-ST12.** `has_blob` returns False for a missing blob and for a blob path that is a
    directory.
13. **U-ST13.** A `layout.json` with an unknown schema makes `check()` raise `CacheLayoutError`.
14. **U-ST14 (`is_expired`).**
    - True when the age exceeds `ttl_days`.
    - False for a future `created_at`.
    - A naive datetime cannot reach it: entry models reject naive values.
15. **Hygiene.**
    - The AST guard passes: every file open goes through `safeio`.
    - `ruff` and `mypy` are clean.
    - `pytest -q` has no new failures.

## Test requirements
- `tests/cache/test_store_core.py`: AC-1..AC-14.

## Risks
- **TOCTOU between the check and the open.** This is a residual risk in MVP (HLD §7.7). Persistent
  plants are still caught.
- **Windows.** POSIX flags fall back to 0. Best-effort only.

## Dependencies
- T-FJH6LI.

## Pseudocode / Algorithm
```text
HLD §8.4.3 LocalFsCacheStore (paths, check, entries, blobs, _atomic_write_bytes) and is_expired verbatim.
maybe_enforce_limits: return None   # TODO(T-HjxNQ0)
```

## Schemas / Interface Notes
- **Interface:** `CacheStore` (HLD §8.4.3).
- **Layout:** HLD §8.4.1.
- **Entry schema:** HLD §13.3.

## Handoff Boundary
- **Upstream:** T-FJH6LI.
- **Downstream:** T-HjxNQ0 (same file, later), T-gDNjN2 and T-u3jG8F (through the ABC).

## Artifacts
- **Docs/comments:** `meta/tickets/E-Rc4Hk8-cross-run-result-cache/T-U7ckfd-cache-store-core/`
- **Large outputs:** N/A

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: Store core: layout, entries,
  blobs, atomic writes.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2 changes:
  - per-operation root, ownership and component checks (security S4);
  - `entries/v1/` versioned layout and canonical bytes (critic #5/#6);
  - total parsing through `parse_entry_bytes` (security S1);
  - `check()` and `has_blob` added;
  - `is_expired` lives here, so the coordinator does not depend on maintenance.
