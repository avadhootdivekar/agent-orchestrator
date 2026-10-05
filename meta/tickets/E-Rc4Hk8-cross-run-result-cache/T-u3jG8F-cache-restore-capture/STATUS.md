# STATUS

- ID: `T-u3jG8F-cache-restore-capture`
- Updated At: `2026-10-05`
- State: `Done`
- Owner: `developer` (Dev B)

## This update
Implemented in commit `87814ea` (branch `worktree-agent-a18ce2c08e42a3a5a`):
`src/agent_orchestrator/cache/restore.py` (`HashingWriter`, `RestoreResult`, `capture_outputs`,
`restore_outputs`) and `tests/cache/test_restore.py` (55 tests, mostly against
`InMemoryCacheStore`, plus one round trip through `LocalFsCacheStore` into a second workspace).
No pre-existing file was touched. Cache stays OFF by default; the engine is unchanged.

## Acceptance criteria
| AC | Result | Evidence |
|----|--------|----------|
| 1 U-R1 | PASS | sha, size and mode (0o640) recorded, blob in the store, records sorted by path; a setuid file is recorded as 0o755 |
| 2 U-R2 | PASS | directory, symlink, FIFO (thread joined at 5 s) -> `StoreSkip(output_not_regular_file)`; missing -> `output_missing` (detail = rel path); simulated EACCES on open -> `store_error` |
| 3 U-R3 | PASS | total above the budget (second file named in `detail`), one file above it, and a file that grows during capture -> `StoreSkip(entry_too_large)`; exactly at the budget is accepted; a zero-byte output with a zero budget is the empty blob |
| 4 U-R4 | PASS | byte-identical files, mode `stored & 0o755`, parents created, an existing (also read-only) destination replaced, result counts files and bytes |
| 5 U-R5 | PASS | `../escape`, nested `..`, absolute, duplicate, missing, extra and renamed paths -> `RestoreMiss(manifest_mismatch, evict=True)`; the parent of the workspace is listed before and after (unchanged), the store's `read_blob` is never called; size sum over the cap -> `corrupt_entry` |
| 6 U-R6 | PASS | wrong-hash same size, shorter and longer blobs, a store `CacheIntegrityError` and a lying manifest size -> `RestoreMiss(blob_corrupt, evict=True, blob=sha)`; contents AND mtimes of every pre-existing destination unchanged, no `.ao-result-cache-*` temp left |
| 7 U-R7 | PASS | missing blob -> `blob_missing`, `evict=True`, nothing created |
| 8 U-R8 | PASS | destination that is a directory -> `restore_failed`, `evict=False`; an earlier-sorted file is untouched (the failure is detected in phase 1) |
| 9 U-R9 | PASS | `.git/hooks/pre-commit`, `CLAUDE.md`, `.github/workflows/x.yml`, `.claude/settings.json` -> `sensitive_output`, `evict=False`, nothing created, no blob read; `docs/claude.md` restores |
| 10 U-R10 | PASS | a symlinked parent, a parent swapped for a link after the chain check, a symlinked destination and a destination outside the workspace -> `restore_failed`; the victim directory stays empty and the link target intact |
| 11 U-R11 | PASS | the second of three `os.replace` calls failing -> `restore_failed`, no temp file left; simulated EACCES on staging and EIO on read -> `restore_failed`; a store `CacheUnsafePathError` propagates unchanged (same object) with the temp removed |
| 12 U-R12 | PASS | modes 0o777 -> 0o755, 0o666 -> 0o644, 0o700, 0o444, 0o000 restored as expected; the staging file is opened with `O_CLOEXEC | O_EXCL | O_NOFOLLOW | O_CREAT` (spy on `os.open`) and is 0o600 until the final chmod |
| 13 edge cases | PASS | zero-byte output round trip; two outputs sharing a blob both restore (blob read twice); restoring twice is idempotent; mode 0o000 |
| 14 hygiene | PASS | FIFO tests carry `skipif(not hasattr(os, "mkfifo"))`, symlink/mode tests `skipif(sys.platform == "win32")`; permission failures are patched, never `chmod`-produced (the `chmod 0o444` setup is a read-only destination that a rename legitimately replaces); AST guard passes (only `os.fdopen`, no bare open); ruff and mypy clean |

## Evidence
- `.venv/bin/python -m pytest -q -p no:cacheprovider tests/cache/test_restore.py` -> 55 passed.
- `.venv/bin/python -m pytest -q -p no:cacheprovider tests/cache tests/test_spawn_provenance.py` -> 800 passed (745 after T-U7ckfd + 55 here).
- `.venv/bin/ruff check src tests` -> All checks passed. `.venv/bin/ruff format --check src tests` -> only the pre-existing generated `src/agent_orchestrator/_build_info.py`.
- `.venv/bin/mypy src tests/cache` -> only the 4 pre-existing `_version.py` errors.
- Full suite: run once at the end of the core-set group; recorded in the T-HjxNQ0 `STATUS.md`.

## Risks / Blockers
- No blockers.
- A crash part-way through the commit loop can leave some destinations new (HLD atomicity statement): never accepted as a success, the task re-dispatches and rewrites every output. A hard crash can leave `.ao-result-cache-*.tmp` litter (documented).
- `os.path.realpath(dest) != dest` (HLD) makes a restore fail closed when the workspace path itself contains a symlink; the engine's `artifact_store.resolve` is expected to hand over resolved paths (T-gDNjN2 should keep passing the resolved `CacheKey.output_abs`).

## Next actions
1. T-gDNjN2 calls `capture_outputs` / `restore_outputs` with `CacheKey.output_abs`-derived maps.
2. T-JCOAsq Part 2 reruns ADV-1/3/5/6/8/10 through the engine.

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: Status initialized (Draft).
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2 re-plan after the Phase-4
  consultation.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 3 re-plan after the
  early-gate review and the manager's scope decisions (HLD §23.5). State stays `Draft`
  (core set); this file, `TASK.md`, `HANDOFF.md` (when present) and the epic `STATUS.md` rollup
  agree.
- By: developer · Role: developer · Date: 2026-10-05 · Comment: State -> Done (commit `87814ea`). All 14 acceptance criteria pass; matches `TASK.md`, `HANDOFF.md` and the epic rollup.
- By: developer · Role: developer · Date: 2026-10-05 · Comment: G1a remediation touched this task's code (SEC-05, SEC-07, SEC-09 (restore/capture)) in commit `763375f`; findings and regression tests are listed in `T-fXWbqg-cache-review-gates/STATUS.md` (G1a remediation) from `output/E-Rc4Hk8-cross-run-result-cache/review-g1a.md` and `review-g1a-security.md`. Task state stays Done; matches `HANDOFF.md`.
