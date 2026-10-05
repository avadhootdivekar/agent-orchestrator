# TASK: T-HjxNQ0-cache-store-maintenance

## Metadata
- Task ID: `T-HjxNQ0-cache-store-maintenance`
- Epic ID: `E-Rc4Hk8-cross-run-result-cache`
- Owner: `developer` (Dev C)
- Created: `2026-10-04`
- Last Updated: `2026-10-05` (Rev 3)
- Status: `Draft`
- Estimate: `15 focus hours (1.9 days)` · Sprint 2

## Requirements Mapping
- Requirement IDs: FR-7, FR-12 (backend), NFR-4, NFR-5, NFR-6
- HLD: §8.4.3 (the `CacheAdmin` half, `_referenced_blobs`, `prune`, `maybe_enforce_limits`,
  `clear`, `verify`), §8.4.4
- ADR-0019: D17, D18, D19, D33

## Description
Add the maintenance half of the store, following HLD §8.4.3 **verbatim**:

- **Change the class to `class LocalFsCacheStore(CacheStore, CacheAdmin)`** and implement:
  - **`iter_entries()`**: a streaming generator over `entries/v1/*/*.json`; never builds a list;
    never follows a symlink; junk and symlinks are yielded as anomaly records;
  - **`_referenced_blobs(*, exclude_v1_keys)`**: the hex-token mark phase over **every** entry
    file of every version (skipping only v1 entries being removed);
  - **`stats(*, now)`** (with `foreign_version_dirs`, `anomalies`, `expired_entries`,
    `orphan_blobs`);
  - **`prune(*, now, max_bytes, ttl_days, dry_run=False)`**: invalid, expired, then LRU down to
    90% of `max_bytes`; refcounted shared blobs; orphan sweep with a 1 h grace; stale tmp and
    trash; deletions go through `delete_entry`/`delete_blob`, so the component checks apply;
  - **`clear()`**: create `trash-<uuid>` first, move `entries/`, `blobs/` and `tmp/` into it,
    `rmtree` it without `ignore_errors`, verify the three directories are gone (else
    `CacheError(store_error)`), remove stale trash;
  - **`verify()`**: **read-only** (Rev 3: `--repair` is deferred); problem kinds per HLD §13.4;
    `ok` ignores `orphan_blob` and `foreign_version`.
- **Replace the placeholder `maybe_enforce_limits(*, now)`** with the bounded version: an
  lstat-only scan that **defers** (`PruneReport(deferred=True)`) as soon as the store holds more
  than `INLINE_PRUNE_MAX_ENTRIES` entries **or** more than `INLINE_PRUNE_MAX_ENTRY_FILE_BYTES` of
  entry files; otherwise it prunes only when over `max_bytes`.

## File scope (exclusive)
- `src/agent_orchestrator/cache/store.py`: the `CacheAdmin` base, the admin methods and the real
  `maybe_enforce_limits`. It shares the file with T-U7ckfd and runs strictly after it.
- `tests/cache/test_store_maintenance.py`, `tests/cache/test_store_race.py` (new)

## Inputs / Outputs
- **Inputs:** T-U7ckfd.
- **Outputs:** the admin backend for T-6tRKml; bounded inline enforcement for T-gDNjN2.

## Acceptance Criteria
1. **U-SM1 (TTL).** With a stepping clock, entries older than `ttl_days` are removed with reason
   `expired`; `ttl_days=None` removes none.
2. **U-SM2 (LRU).** Over `max_bytes`, the least-recently-touched entries go until the total is at
   most 90% of `max_bytes`; equal mtimes break by key; `max_bytes=0` removes everything.
3. **U-SM3.** A blob shared by two entries survives removing one of them.
4. **U-SM4.** Unreferenced blobs and temp files younger than 1 h survive; older ones are removed.
5. **U-SM5 (version safety).** An `entries/v2/` entry and the blob it references survive `prune`
   and inline enforcement; `verify` reports `foreign_version` and stays `ok`; a blob referenced
   only by an unparseable kept v1 entry is protected by the hex-token mark.
6. **U-SM6.** A stale `trash-*` directory is removed by the next `prune`.
7. **U-SM7 (`clear`).** Creates its trash directory first; afterwards `stats().entries == 0`; with
   `shutil.rmtree` patched to fail it raises `CacheError(store_error)`.
8. **U-SM8 (`verify`).** Each problem kind is produced by its trigger (`corrupt_entry`,
   `key_mismatch`, `missing_blob`, `corrupt_blob`, `orphan_blob`, `foreign_version`, `symlink`,
   `unexpected_file`); `verify()` deletes nothing (snapshot of the tree before and after).
9. **U-SM9 (streaming).** `iter_entries` is a generator; with 10 000 entries the `tracemalloc`
   peak stays below 20 MiB.
10. **U-SM10a (count bound).** With `INLINE_PRUNE_MAX_ENTRIES` patched to 10 and 11 entries,
    `maybe_enforce_limits` returns `deferred=True` without parsing any entry (spy on
    `parse_entry_bytes`).
11. **U-SM10b (byte bound).** With `INLINE_PRUNE_MAX_ENTRY_FILE_BYTES` patched to 1 KiB and entry
    files totalling more, it defers without reading any entry file (spy on `read_bounded`).
12. **U-SM11.** `prune(dry_run=True)` reports the same counts as a real prune and deletes nothing.
13. **U-SM12.** `stats` and `prune` reports match the §13.4 shapes.
14. **U-SM13 (race, non-vacuous).** 8 writer and 2 reader processes start behind a
    `multiprocessing.Barrier`; at least two writers complete a `put_entry` for the **same** key
    (their return values prove it); readers observe at least one complete entry; every observed
    entry is valid with its blobs present; `verify().ok` afterwards. Bounded iterations and join
    timeouts; skipped with a reason on fewer than 2 CPUs.
15. **Class shape.** `LocalFsCacheStore` is an instance of both ABCs; the contract suite still
    passes.
16. **Hygiene.** The AST guard passes; ruff (≤ 100 columns) and mypy are clean; `pytest -q` has
    no new failures.

## Test requirements
- `tests/cache/test_store_maintenance.py`: AC-1..AC-13, AC-15.
- `tests/cache/test_store_race.py`: AC-14.

## Risks
- **A destructive bug deletes foreign data.** Mitigation: AC-5; gate G1a reviews this code.
- **Race-test flakiness.** Mitigation: barrier, bounded iterations, timeouts, fixed seeds.

## Dependencies
- T-U7ckfd.

## Pseudocode / Algorithm
```text
HLD §8.4.3 (the part "added by T-HjxNQ0") verbatim.
```

## Schemas / Interface Notes
- **Interface:** `CacheAdmin` (HLD §8.4.3). **Report shapes:** HLD §13.4.

## Handoff Boundary
- **Upstream:** T-U7ckfd.
- **Downstream:** T-6tRKml (CLI), T-gDNjN2 (`maybe_enforce_limits`).

## Artifacts
- **Docs/comments:** `meta/tickets/E-Rc4Hk8-cross-run-result-cache/T-HjxNQ0-cache-store-maintenance/`
- **Large outputs:** N/A

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: Maintenance.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2: streaming iteration,
  bounded inline enforcement, hex-token mark phase, fixed `clear()`.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 3 (early-gate A1a, C;
  manager B): this task adds the `CacheAdmin` base; inline enforcement is also bounded by
  entry-file bytes; `verify` is read-only (`--repair` deferred); U-SM13 is non-vacuous;
  re-estimated from 16 h to 15 h.
