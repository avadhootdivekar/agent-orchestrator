# TASK: T-HjxNQ0-cache-store-maintenance

## Metadata
- Task ID: `T-HjxNQ0-cache-store-maintenance`
- Epic ID: `E-Rc4Hk8-cross-run-result-cache`
- Owner: `developer` (Dev C)
- Created: `2026-10-04`
- Last Updated: `2026-10-05` (Rev 2)
- Status: `Draft`
- Estimate: `16 focus hours (2 days)` · Sprint 2

## Requirements Mapping
- Requirement IDs: FR-7, FR-12 (backend), NFR-4, NFR-5, NFR-6
- HLD: §8.4.3 (the `CacheAdmin` half, `_referenced_blobs`, `prune`, `maybe_enforce_limits`,
  `clear`, `verify`), §8.4.4
- ADR-0019: D18, D19

## Description
Add the maintenance half of `LocalFsCacheStore`, following HLD §8.4.3 **verbatim**:

- **`iter_entries()`.** A **streaming** generator over `entries/v1/*/*.json`. It never builds a
  list and never follows symlinks. Junk files and symlinks are yielded as anomaly records.
- **`_referenced_blobs(*, exclude_v1_keys)`.** The mark phase. It collects every
  `HEX64_TOKEN_RE_BYTES` token from **every** entry file under `entries/**`, of any version,
  whether parseable or not. The only skips are v1 entries being removed by this prune.
- **`stats(*, now)`.** Includes `foreign_version_dirs`, `anomalies`, `expired_entries` and
  `orphan_blobs`.
- **`prune(*, now, max_bytes, ttl_days, dry_run=False)`.**
  - Removal order: invalid entries, then expired (TTL from `created_at`), then LRU by entry
    mtime down to 90% of `max_bytes`, with a key tie-break.
  - Shared blobs are refcounted.
  - Orphan blobs older than the 1 h grace period are swept.
  - Temp files older than 1 h and stale `trash-*` directories are removed.
  - `max_bytes is not None` / `ttl_days is not None` are explicit tests, because `0` is valid.
- **`maybe_enforce_limits(*, now)`.** Bounded inline enforcement. It replaces T-U7ckfd's
  placeholder:
  - an lstat-only size scan, capped at `INLINE_PRUNE_MAX_ENTRIES`;
  - returns `PruneReport(deferred=True)` when there are more entries than that;
  - otherwise prunes only when over `max_bytes`.
- **`clear()`.** Create the `trash-<uuid>` directory **first**. Move `entries/`, `blobs/` and
  `tmp/` into it. `rmtree` it **without** `ignore_errors`. Verify that the three directories are
  gone, else raise `CacheError(store_error)`. Remove any other stale trash directories.
- **`verify(*, repair)`.** Uses the problem kinds from HLD §13.4. `ok` ignores `orphan_blob` and
  `foreign_version`. `repair` deletes:
  - corrupt or key-mismatched entries;
  - entries whose blobs are missing or corrupt;
  - corrupt blobs;
  - junk files.

  It never touches foreign versions.

## File scope (exclusive)
- `src/agent_orchestrator/cache/store.py`: the `CacheAdmin` methods and `maybe_enforce_limits`.
  It shares the file with T-U7ckfd, but runs strictly after it.
- `tests/cache/test_store_maintenance.py`, `tests/cache/test_store_race.py` (new)

## Inputs / Outputs
- **Inputs:** T-U7ckfd (store core).
- **Outputs:** the admin backend for T-6tRKml, and bounded inline enforcement for T-gDNjN2.

## Acceptance Criteria
1. **U-SM1 (TTL).** With a stepping clock, entries older than `ttl_days` are removed with reason
   `expired`. `ttl_days=None` removes none.
2. **U-SM2 (LRU).**
   - Over `max_bytes`, the least-recently-touched entries are removed until the total is at most
     90% of `max_bytes`.
   - Equal mtimes are broken by key order.
   - `max_bytes=0` removes everything.
3. **U-SM3 (shared blobs).** A blob shared by two entries survives the removal of one of them.
4. **U-SM4 (grace).**
   - An unreferenced blob younger than 1 h survives. One older than 1 h is removed.
   - Temp files follow the same rule.
5. **U-SM5 (version safety).**
   - An entry under `entries/v2/` and the blob it references both survive `prune`, `verify
     --repair` and inline enforcement.
   - `verify` reports `foreign_version` and stays `ok`.
   - A blob referenced only by an **unparseable** v1 entry being kept is protected by the
     hex-token mark.
6. **U-SM6.** A stale `trash-*` directory is removed by the next `prune`.
7. **U-SM7 (`clear`).**
   - Creates the trash directory first.
   - Afterwards the store is empty and `stats().entries == 0`.
   - When `shutil.rmtree` is patched to fail, `clear` raises `CacheError(store_error)`. This is
     the exit-1 path in T-6tRKml.
8. **U-SM8 (`verify`).** Each problem kind is produced by its trigger:
   - `corrupt_entry`
   - `key_mismatch`
   - `missing_blob`
   - `corrupt_blob`
   - `orphan_blob`
   - `foreign_version`
   - `symlink`
   - `unexpected_file`

   `repair` removes exactly the documented set. A second `verify` is `ok`.
9. **U-SM9 (streaming).** `iter_entries` is a generator. With 10 000 entries,
   `tracemalloc` peak stays below a fixed bound (for example 20 MiB), and no `list(` of all
   entries is created (code review).
10. **U-SM10 (bounded inline).**
    - With `INLINE_PRUNE_MAX_ENTRIES` monkeypatched to 10 and 11 entries present,
      `maybe_enforce_limits` returns `deferred=True` and parses **no** entry: spy on
      `parse_entry_bytes`.
    - Under the cap and over `max_bytes`, it prunes.
11. **U-SM11 (dry run).** `prune(dry_run=True)` reports the same counts as a real prune and
    deletes nothing.
12. **U-SM12.** `stats` and `prune` reports validate against the §13.4 shapes, through their
    dataclasses.
13. **U-SM13 (race, multiprocess).**
    - 8 writer processes and 2 reader processes use the same key and different keys for a
      bounded number of iterations, joined with timeouts.
    - Every entry a reader observes is valid, its blobs exist, and the shas match.
    - `verify().ok` holds afterwards.
    - The test documents its assumption of 2 or more CPUs.
14. **Hygiene.** The AST guard passes. `ruff` and `mypy` are clean. `pytest -q` has no new
    failures.

## Test requirements
- `tests/cache/test_store_maintenance.py`: AC-1..AC-12.
- `tests/cache/test_store_race.py`: AC-13.

## Risks
- **A destructive bug deletes foreign data.** Mitigation: AC-5. Gate G1b reviews this code.
- **Race-test flakiness.** Mitigation: bounded iterations, timeouts, and a fixed seed for any
  randomness.

## Dependencies
- T-U7ckfd.

## Pseudocode / Algorithm
```text
HLD §8.4.3 iter_entries / _referenced_blobs / prune / maybe_enforce_limits / clear / verify verbatim.
```

## Schemas / Interface Notes
- **Interface:** `CacheAdmin` (HLD §8.4.3).
- **Report shapes:** HLD §13.4.

## Handoff Boundary
- **Upstream:** T-U7ckfd.
- **Downstream:** T-6tRKml (CLI) and T-gDNjN2 (`maybe_enforce_limits`).

## Artifacts
- **Docs/comments:** `meta/tickets/E-Rc4Hk8-cross-run-result-cache/T-HjxNQ0-cache-store-maintenance/`
- **Large outputs:** N/A

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: Maintenance: iterate, stats,
  prune, clear, verify, race test.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2 changes:
  - streaming iteration and bounded inline enforcement (security S7);
  - hex-token mark phase protecting foreign versions (critic #5);
  - `clear()` now creates its trash directory and verifies the removal (security S5);
  - the "is not None" fixes;
  - `verify` kinds `foreign_version` and `symlink`.
