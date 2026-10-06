# STATUS

- ID: `T-HjxNQ0-cache-store-maintenance`
- Updated At: `2026-10-05`
- State: `Done`
- Owner: `developer` (Dev C)

## This update
Implemented in commit `6753c71` (branch `worktree-agent-a18ce2c08e42a3a5a`):
`src/agent_orchestrator/cache/store.py` now declares `class LocalFsCacheStore(CacheStore, CacheAdmin)`
with `iter_entries`, `_referenced_blobs`, `stats`, `prune`, `clear`, `verify` and the bounded
`maybe_enforce_limits`; tests in `tests/cache/test_store_maintenance.py` (83) and
`tests/cache/test_store_race.py` (1). T-U7ckfd's class-shape test in `tests/cache/test_store_core.py`
was updated on purpose (it asserted "no `CacheAdmin` base"; the base is exactly what this task adds).
Cache stays OFF by default; the engine is unchanged.

## Acceptance criteria
| AC | Result | Evidence |
|----|--------|----------|
| 1 U-SM1 | PASS | stepping `StepClock` (0, +10, +25 days): nothing, then one, then one `expired`; `ttl_days=None` removes none of a 10 000-day-old entry; boundary (exactly the TTL kept, a future `created_at` kept) |
| 2 U-SM2 | PASS | ten equal-size entries touched at distinct times: exactly the k oldest go (k computed from the 90% target), `after.total_bytes <= int(0.9 * max_bytes)`, `bytes_before`/`bytes_after` equal the stats totals; equal mtimes break by key (smallest keys first); `max_bytes=0` removes everything and sweeps the freed blobs; invalid entries are removed first |
| 3 U-SM3 | PASS | a blob shared by two entries survives the removal of one (and is counted once in the total); it is removed with the last reference |
| 4 U-SM4 | PASS | orphan blob 60 s inside the grace survives, 60 s past it is removed; same for temp files; referenced blobs are never swept |
| 5 U-SM5 | PASS | an `entries/v2/` entry and its blob survive `prune(max_bytes=0, ttl_days=0)` twice and inline enforcement; `verify` reports `foreign_version` and stays `ok`; `stats.foreign_version_dirs == ("v2",)`; a blob named only by kept junk files in `entries/v1` (non-entry name, hex name in the wrong shard) is protected by the hex-token mark; an invalid v1 entry that IS removed does not protect its blob |
| 6 U-SM6 | PASS | a stale `trash-*` directory is counted by a dry run and removed by the next prune |
| 7 U-SM7 | PASS | the trash directory exists before the first `os.replace` (spy); afterwards `stats().entries == 0`, the three directories are gone, layout files stay, the cache is usable again; `shutil.rmtree` raising or silently doing nothing -> `CacheError(store_error)`; a symlinked `blobs/` is unlinked, its target untouched; a missing cache is a no-op and stale trash goes too |
| 8 U-SM8 | PASS | `corrupt_entry`, `key_mismatch`, `missing_blob`, `corrupt_blob`, `orphan_blob`, `foreign_version`, `symlink` (shard and blob file), `unexpected_file` (junk in `entries/` and in a shard) each from its trigger; `ok` is False only for the failing kinds; `verify()` leaves the whole tree (types, sizes, mtimes, link targets) identical |
| 9 U-SM9 | PASS | `iter_entries()` is a generator (`inspect.isgenerator`); 10 000 entries consumed with a `tracemalloc` peak below 20 MiB |
| 10 U-SM10a | PASS | `INLINE_PRUNE_MAX_ENTRIES` patched to 10: exactly 10 entries is not deferred (and prunes when over budget), 11 entries -> `PruneReport(deferred=True)`, nothing removed, `parse_entry_bytes` and `read_bounded` never called (spies) |
| 11 U-SM10b | PASS | `INLINE_PRUNE_MAX_ENTRY_FILE_BYTES` patched to 1 KiB with 3 entry files above it: deferred, no entry file read or parsed, nothing removed |
| 12 U-SM11 | PASS | `prune(dry_run=True)` leaves the whole tree identical and reports the same `removed_entries`, `removed_blobs`, `removed_tmp`, `removed_trash`, `bytes_before`, `bytes_after` as the real prune that follows (mixed: invalid, expired, LRU, orphan, trash) |
| 13 U-SM12 | PASS | `stats`, `prune`, `clear` and `verify` reports mapped to the HLD 13.4 JSON shapes validate against those schemas (`jsonschema`); `max_entry_bytes` is supplied by the CLI (the store does not know it) |
| 14 U-SM13 | PASS | 8 writers + 2 readers (spawned processes) behind one `Barrier`: at least two writers (in practice all, none errored) completed all 40 same-key puts (return values), at least one pair of put windows overlapped (shared monotonic clock), readers validated every observed entry (blob present, bytes hash to the sha), the final entry is one writer's whole entry, `verify().ok`, empty `tmp/`; 15 repeated runs all passed; skipped with a reason below 2 CPUs |
| 15 class shape | PASS | `isinstance` of both ABCs; no abstract methods left; `CacheStoreContract` still passes (`TestContractStillPasses`) |
| 16 hygiene | PASS | see Evidence |

## Evidence
- `.venv/bin/python -m pytest -q -p no:cacheprovider tests/cache/test_store_maintenance.py tests/cache/test_store_race.py` -> 84 passed (83 + 1).
- `.venv/bin/python -m pytest -q -p no:cacheprovider tests/cache tests/test_spawn_provenance.py` -> 884 passed (800 after T-u3jG8F + 84 here).
- `.venv/bin/ruff check src tests` -> All checks passed. `.venv/bin/ruff format --check src tests` -> clean (the generated `_build_info.py` is only flagged right after a test run regenerates it; pre-existing).
- `.venv/bin/mypy src tests/cache` -> only the 4 pre-existing `_version.py` errors.
- AST guard passes (no bare `open` / `os.open`; the only reads go through `safeio.read_bounded` / `open_regular_read`).
- **Full suite** (core-set group: T-U7ckfd + T-u3jG8F + T-HjxNQ0, run once after the last commit, foreground then background-polled):
  `.venv/bin/python -m pytest -q -rs -p no:cacheprovider` -> **6027 passed, 10 skipped, 0 failed**
  (1 pre-existing starlette/httpx deprecation warning) in 651 s. Baselines: 5583 passed / 10 skipped
  before group T-8tr1H4 + T-uoYW6b; 5787 passed / 10 skipped after it. The delta from 5787 is
  exactly +240 = 101 (T-U7ckfd) + 55 (T-u3jG8F) + 84 (this task). The 10 skips are the same
  environment gates as before (`real_llm`, `swebench`, `playwright` not installed, one isolation
  layout skip). This single run also covers the previous group's tree.

## Risks / Blockers
- No blockers.
- Destructive code (prune, clear): gate G1a must review it. Safety properties tested: foreign-version and junk files are never deleted, symlinks are never followed or removed through, deletions go through `delete_entry` / `delete_blob` (component checks first), a symlinked `entries/` aborts the whole pass before any deletion.
- `maybe_enforce_limits` scans blobs with an unbounded lstat walk (only entry files are bounded by count and bytes, as specified); a planted store with millions of blob files would make that walk slow. Not in the HLD bound; flagged for the G1a reviewer.
- `clear()` concurrent with a writer: the writer's `os.replace` fails with ENOENT (a skipped store) or its entry lands pointing at trashed blobs (a later miss + evict): both benign, as in the HLD failure matrix.

## Next actions
1. Gate G1a (T-fXWbqg) can start: the core set (T-FJH6LI, T-28J9oR, T-OeRYSO, T-QgQy08, T-8tr1H4, T-uoYW6b, T-U7ckfd, T-u3jG8F, T-HjxNQ0) is Done.
2. T-gDNjN2 consumes `CacheStore` (+ `maybe_enforce_limits`); T-6tRKml consumes `CacheAdmin`.

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: Status initialized (Draft).
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2 re-plan after the Phase-4
  consultation.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 3 re-plan after the
  early-gate review and the manager's scope decisions (HLD §23.5). State stays `Draft`
  (core set); this file, `TASK.md`, `HANDOFF.md` (when present) and the epic `STATUS.md` rollup
  agree.
- By: developer · Role: developer · Date: 2026-10-05 · Comment: State -> Done (commit `6753c71`). All 16 acceptance criteria pass; matches `TASK.md`, `HANDOFF.md` and the epic rollup. Full suite 6027 passed, 10 skipped, 0 failed.
- By: developer · Role: developer · Date: 2026-10-05 · Comment: G1a remediation touched this task's code (SEC-01, SEC-02, S-2, S-3, S-4, S-5 (maintenance)) in commit `763375f`; findings and regression tests are listed in `T-fXWbqg-cache-review-gates/STATUS.md` (G1a remediation) from `output/E-Rc4Hk8-cross-run-result-cache/review-g1a.md` and `review-g1a-security.md`. Task state stays Done; matches `HANDOFF.md`.
