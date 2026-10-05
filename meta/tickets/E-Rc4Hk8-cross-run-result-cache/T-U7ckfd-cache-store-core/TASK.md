# TASK: T-U7ckfd-cache-store-core

## Metadata
- Task ID: `T-U7ckfd-cache-store-core`
- Epic ID: `E-Rc4Hk8-cross-run-result-cache`
- Owner: `developer` (Dev C)
- Created: `2026-10-04`
- Last Updated: `2026-10-05` (Rev 3)
- Status: `Draft`
- Estimate: `17 focus hours (2.1 days)` · Sprint 1

## Requirements Mapping
- Requirement IDs: FR-7, NFR-4, NFR-5, NFR-6, NFR-10 (M-2, M-4, M-6, M-7)
- HLD: §8.4.1 (layout, factory, per-operation checks), §8.4.2 (`parse_entry_bytes` use),
  §8.4.3 (`CacheStore` half of `LocalFsCacheStore`, `is_expired`), §8.4.4
- ADR-0019: D2, D17, D18, D33

## Description
Implement the hot-path half of the store in `cache/store.py`. **The class derives from
`CacheStore` only**: `class LocalFsCacheStore(CacheStore)`. T-HjxNQ0 later adds `CacheAdmin` to
its bases together with the admin methods, so the class is importable and type-correct after
this task alone (early-gate A1a).

- **Factory.** `for_workspace(workspace_root, *, max_bytes, ttl_days)`; no I/O.
- **`check()`.** Pre-flight; creates nothing; when the root exists, `check_root_dir` and a layout
  read (unknown schema → `CacheLayoutError`).
- **`_checks(target_dir)`.** Root check (→ `CacheLayoutError`) plus `check_dir_chain`; an
  `UnsafePathError` becomes **`CacheUnsafePathError(REASON_UNSAFE_PATH)`**. It runs before
  **every** operation: `get_entry`, `put_entry`, **`touch_entry`, `delete_entry`**, `has_blob`,
  `put_blob`, `read_blob`, `delete_blob`. Nothing is read, written, `utime`d or unlinked through
  a symlinked directory (D33).
- **`ensure_layout()`.** Lazy, component-wise: `.gitignore`, `CACHEDIR.TAG`, `layout.json`,
  `entries/v1/`, `blobs/`, `tmp/`, mode `0o700`.
- **Path builders.** `_entry_path` / `_blob_path` validate with `SHA256_HEX_RE.fullmatch`
  **before** building any path.
- **Entries and blobs.** Exactly HLD §8.4.3: a final-component symlink or FIFO at an entry path is
  `CacheIntegrityError(corrupt_entry)` (evicting it unlinks the link itself); a symlinked
  directory component is `CacheUnsafePathError`.
- **`_atomic_write_bytes`, `_new_tmp`, `is_expired`.**
- **`maybe_enforce_limits(*, now)`.** A placeholder returning `None` with a `TODO(T-HjxNQ0)`
  comment.

## File scope (exclusive)
- `src/agent_orchestrator/cache/store.py`: the class with its `CacheStore` methods, the factory,
  the checks, `ensure_layout`, the write helpers and `is_expired`. T-HjxNQ0 later adds the
  `CacheAdmin` base and methods.
- `tests/cache/test_store_core.py` (new); it reuses `tests/cache/store_contract.py`.

## Inputs / Outputs
- **Inputs:** T-FJH6LI (`types`, `safeio`, `constants`, contract suite).
- **Outputs:** a working local hot-path store for the coordinator and restore.

## Acceptance Criteria
1. **U-ST1.** `for_workspace` does no I/O (spies on `os.mkdir`, `os.open`, `os.stat` unused);
   `check()` on a workspace without a cache directory creates nothing.
2. **U-ST2.** The first `put_entry` creates the §8.4.1 layout (`0o700`, `.gitignore` with `*`,
   the `CACHEDIR.TAG` signature, `layout.json`); `git check-ignore` reports the cache files as
   ignored inside a temp repo.
3. **U-ST3 (M-2).** `get_entry("../x")`, `get_entry("A"*64)` and a 63-character key raise
   `ValueError` before any filesystem call (spy); the same for blob shas.
4. **U-ST4.** An entry is written to `entries/v1/<k[:2]>/<k>.json` as exactly
   `entry.to_canonical_bytes()`.
5. **U-ST5.** The T-FJH6LI contract suite passes against `LocalFsCacheStore`.
6. **U-ST6.** Each hostile entry file makes `get_entry` raise `CacheIntegrityError` and nothing
   else: a FIFO (returns within 5 s), a final-component symlink, a 2 MiB file, invalid JSON, a
   wrong key.
7. **U-ST7.** A symlinked root or `.orchestrator` → `CacheLayoutError`.
8. **U-ST8.** A root owned by another uid (simulated) → `CacheLayoutError`; a group-writable root
   we own is chmodded to `0o700`.
9. **U-ST9.** If `os.replace` raises during `put_entry` or `put_blob`, no destination file exists
   and no temp file remains.
10. **U-ST10.** Identical bytes put twice give one blob file; `BlobRef.new` is False the second
    time; the mtime is refreshed.
11. **U-ST11.** `put_blob` beyond `max_bytes` raises `CacheTooLargeError` and leaves no temp file;
    `read_blob` never writes more than `max_bytes + 1` bytes.
12. **U-ST12.** `has_blob` is False for a missing blob and for a blob path that is a directory.
13. **U-ST13.** A `layout.json` with an unknown schema makes `check()` raise `CacheLayoutError`.
14. **U-ST14.** `is_expired` is True past `ttl_days` and False for a future `created_at`.
15. **U-ST15 (unsafe paths, D33).** With `entries/v1/<shard>` (and, separately, `entries/v1`
    itself) replaced by a symlink to a victim directory that holds `<key>.json`:
    `get_entry`, `touch_entry` and `delete_entry` raise `CacheUnsafePathError`, and the victim
    file still exists with an unchanged mtime. With a symlinked blob shard, `has_blob`,
    `read_blob` and `delete_blob` raise `CacheUnsafePathError` and the victim is untouched.
16. **Class shape.** `LocalFsCacheStore.__mro__` contains `CacheStore` and not `CacheAdmin`; the
    class instantiates (all `CacheStore` abstract methods are implemented).
17. **Hygiene.** The AST guard passes; symlink/FIFO/ownership tests carry skip markers; ruff
    (≤ 100 columns) and mypy are clean; `pytest -q` has no new failures.

## Test requirements
- `tests/cache/test_store_core.py`: AC-1..AC-16.

## Risks
- **TOCTOU between check and use.** A residual in MVP (HLD §7.7); persistent plants are caught.

## Dependencies
- T-FJH6LI.

## Pseudocode / Algorithm
```text
HLD §8.4.3 LocalFsCacheStore (the part before "added by T-HjxNQ0") and is_expired verbatim.
maybe_enforce_limits: return None   # TODO(T-HjxNQ0)
```

## Schemas / Interface Notes
- **Interface:** `CacheStore` (HLD §8.4.3). **Layout:** HLD §8.4.1. **Entry schema:** HLD §13.3.

## Handoff Boundary
- **Upstream:** T-FJH6LI.
- **Downstream:** T-HjxNQ0 (same file, later), T-u3jG8F and T-gDNjN2 (through the ABC),
  T-6tRKml.

## Artifacts
- **Docs/comments:** `meta/tickets/E-Rc4Hk8-cross-run-result-cache/T-U7ckfd-cache-store-core/`
- **Large outputs:** N/A

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: Store core.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2: per-operation checks,
  `entries/v1`, canonical bytes, `check()`, `has_blob`, `is_expired`.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 3 (early-gate A1a, A4): the
  class derives from `CacheStore` only; component checks run before `touch_entry` and
  `delete_entry` too, and raise `CacheUnsafePathError` (never followed, never evicted); U-ST15;
  re-estimated from 16 h to 17 h.
