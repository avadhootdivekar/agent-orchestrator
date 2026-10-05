# STATUS

- ID: `T-U7ckfd-cache-store-core`
- Updated At: `2026-10-05`
- State: `Done`
- Owner: `developer` (Dev C)

## This update
Implemented in commit `181bbbb` (branch `worktree-agent-a18ce2c08e42a3a5a`):
`src/agent_orchestrator/cache/store.py` (`LocalFsCacheStore(CacheStore)`, `is_expired`) and
`tests/cache/test_store_core.py` (101 tests). No pre-existing file was touched. Cache stays OFF by
default; the engine is unchanged. `maybe_enforce_limits` is the `TODO(T-HjxNQ0)` placeholder.

## Acceptance criteria
| AC | Result | Evidence |
|----|--------|----------|
| 1 U-ST1 | PASS | `for_workspace` under an `os.mkdir/open/stat/lstat/unlink/utime/replace/scandir/rename` spy: zero calls, nothing created; `check()` (and reads) on a workspace without a cache directory create nothing |
| 2 U-ST2 | PASS | first `put_entry` / `put_blob` create the 0o700 layout, `.gitignore` (`*`), `CACHEDIR.TAG` signature, `layout.json`; `git check-ignore` is 0 for entry, blob and layout files in a temp repo and `git status --porcelain` is empty |
| 3 U-ST3 | PASS | `../x`, 64 x `A`, 63 chars: `ValueError` from all six key/sha operations with zero os calls (spy) |
| 4 U-ST4 | PASS | file at `entries/v1/<k[:2]>/<k>.json` equals `entry.to_canonical_bytes()`; returned size equals the file size |
| 5 U-ST5 | PASS | `TestLocalFsStoreContract(CacheStoreContract)`: all contract tests pass on a real tmp_path workspace |
| 6 U-ST6 | PASS | FIFO (thread joined at 5 s), final-component symlink (eviction unlinks only the link; target survives), 2 MiB file, five invalid-JSON shapes (incl. 100 000 nested brackets, invalid UTF-8), wrong key (`key_mismatch`): each `CacheIntegrityError`, nothing else |
| 7 U-ST7 | PASS | symlinked root, symlinked `.orchestrator`, dangling `.orchestrator` link, root that is a file: `CacheLayoutError` from check/get/put/has; the link targets stay empty |
| 8 U-ST8 | PASS | `os.geteuid` patched to look foreign: `CacheLayoutError`, nothing written; a group/other-writable root we own is chmodded to 0o700 (checked via `check()` and `get_entry`) |
| 9 U-ST9 | PASS | `os.replace` raising: no destination and an empty `tmp/` for `put_entry` and `put_blob`; also a failing `os.write` and a short-write continuation test |
| 10 U-ST10 | PASS | identical bytes twice: one blob file, `new` False the second time, mtime refreshed from 1000 |
| 11 U-ST11 | PASS | beyond `max_bytes`: `CacheTooLargeError`, no temp, no blob; source reads are always `<= max_bytes + 1`; `read_blob` writes exactly `max_bytes + 1` to the sink for a bigger blob |
| 12 U-ST12 | PASS | `has_blob` False for missing, a directory and a symlink at the blob path; `read_blob` of those is `CacheIntegrityError(blob_corrupt)`; a FIFO blob does not block |
| 13 U-ST13 | PASS | six layout variants (unknown schema, bad JSON, list, no schema, bad UTF-8, oversize) and a symlinked `layout.json`: `check()` and writes raise `CacheLayoutError`; a missing `layout.json` is tolerated and rewritten |
| 14 U-ST14 | PASS | past the TTL True, exactly the TTL False, future `created_at` False |
| 15 U-ST15 | PASS | symlinked shard, symlinked `entries/v1`, symlinked `entries/` and `blobs/`, symlinked blob shard and `tmp/`: `get/touch/delete/put_entry` and `has/read/delete/put_blob` raise `CacheUnsafePathError(unsafe_path)`; victim files exist with unchanged mtime and content; a link planted after `ensure_layout` is also refused |
| 16 class shape | PASS | `__mro__` has `CacheStore`, not `CacheAdmin`; no abstract methods left; instantiates |
| 17 hygiene | PASS | see Evidence |

## Evidence
- `.venv/bin/python -m pytest -q -p no:cacheprovider tests/cache/test_store_core.py` -> 101 passed.
- `.venv/bin/python -m pytest -q -p no:cacheprovider tests/cache tests/test_spawn_provenance.py` -> 745 passed (`tests/cache` alone: 731 = 630 before this task + 101 here; plus 14 in `test_spawn_provenance.py`).
- `.venv/bin/ruff check src tests` -> All checks passed. `.venv/bin/ruff format --check src tests` -> clean (the pre-existing generated `_build_info.py` was not flagged on this tree).
- `.venv/bin/mypy src tests/cache` -> only the 4 pre-existing `_version.py` errors.
- AST guard passes (store.py has no bare `open` / `os.open`; every open goes through `safeio`).
- Full suite: run once at the end of the core-set group (T-U7ckfd + T-u3jG8F + T-HjxNQ0); the result is recorded in the T-HjxNQ0 `STATUS.md`.

## Risks / Blockers
- No blockers.
- Residual TOCTOU between a check and its use (HLD 7.7): persistent plants are caught, a swap inside the window is refused only when it hits `ensure_dir_chain` / `os.replace`.

## Next actions
1. T-HjxNQ0 adds `CacheAdmin` and the real `maybe_enforce_limits` in the same file.
2. T-gDNjN2 and T-u3jG8F use the store through the `CacheStore` ABC.

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: Status initialized (Draft).
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2 re-plan after the Phase-4
  consultation.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 3 re-plan after the
  early-gate review and the manager's scope decisions (HLD §23.5). State stays `Draft`
  (core set); this file, `TASK.md`, `HANDOFF.md` (when present) and the epic `STATUS.md` rollup
  agree.
- By: developer · Role: developer · Date: 2026-10-05 · Comment: State -> Done (commit `181bbbb`). All 17 acceptance criteria pass; matches `TASK.md`, `HANDOFF.md` and the epic rollup.
- By: developer · Role: developer · Date: 2026-10-05 · Comment: G1a remediation touched this task's code (SEC-08, SEC-09 (store core)) in commit `763375f`; findings and regression tests are listed in `T-fXWbqg-cache-review-gates/STATUS.md` (G1a remediation) from `output/E-Rc4Hk8-cross-run-result-cache/review-g1a.md` and `review-g1a-security.md`. Task state stays Done; matches `HANDOFF.md`.
