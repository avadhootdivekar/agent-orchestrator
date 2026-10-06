# STATUS

- ID: `T-8tr1H4-cache-hashing`
- Updated At: `2026-10-05`
- State: `Done`
- Owner: `developer` (Dev C)

## This update
Implemented in commit `00b9a3c` (branch `worktree-agent-a18ce2c08e42a3a5a`):
`src/agent_orchestrator/cache/hashing.py` and `repo_state.py`, with `tests/cache/test_hashing.py`
(34 tests) and `tests/cache/test_repo_state.py` (43 tests). No shared file was touched. Cache stays
OFF by default. T-uoYW6b and T-gDNjN2 may start on this dependency. Frozen names and the small
deviations: `HANDOFF.md`.

## Acceptance criteria
| AC | Result | Evidence |
|----|--------|----------|
| 1 U-H1 | PASS | `test_file_digest_equals_sha256_of_content`, empty file, multi-chunk (> `HASH_CHUNK_BYTES`) file; `size == len(content)` |
| 2 U-H2 | PASS | three creation orders give one digest; rename and content change alter it; the manifest equals the documented `ao.result-cache.dir/v1` canonical document |
| 3 U-H3 | PASS | empty dir: equal digests, `size == 0`, equals the canonical empty manifest; non-UTF-8 names deterministic (skips only if the filesystem rejects them) |
| 4 U-H4 | PASS | symlink (also dangling) inside a directory, and given directly -> `input_not_regular` |
| 5 U-H5 | PASS | FIFO inside a directory and given directly (`digest_path` and `hash_regular_file`) -> `input_not_regular`, run in a thread joined with a 5 s timeout; `skipif(not hasattr(os, "mkfifo"))` |
| 6 U-H6 | PASS | `input_too_large` with `bytes=... files=...` in the detail; a spy on `os.read` records zero reads (budget charged before reading); file-count cap; directories count as files; budget cumulative; exactly-at-limit allowed |
| 7 U-H7 | PASS | patched `os.read` that grows the file -> `input_unstable`; the shrinking case too |
| 8 U-H8 | PASS | missing file / path -> `input_missing`; simulated `PermissionError` via patched `os.open` -> `input_unreadable` (also read `EIO`, `scandir`, `lstat`); no `chmod` tests |
| 9 U-H9 | PASS | `.git` (dir and file, any depth) skipped by name; `<ws>/.orchestrator` skipped via `skip_abs` (and a symlink inside a skipped location is never inspected) |
| 10 U-H10 | PASS | `exclude_abs` (declared outputs) skipped by `hash_directory` and forwarded by `digest_path` |
| 11 U-G1 | PASS | `find_git_toplevel`: repo, subdirectory, `.git` file (worktree style), workspace root itself, nearest marker wins, `..`/trailing-separator normalisation, dangling `.git` symlink counts (lstat only); plain directory -> `None` |
| 12 U-G2..G4 | PASS | plain dir omitted; real repos: sha / `"unborn"`; failing `rev-parse` (+ `symbolic-ref`), runner raising `TimeoutExpired` / `OSError`, `GitRepo` construction `RuntimeError` (non-empty hooks dir) and `OSError`, and a stray empty `.git` dir -> `repo_head_unavailable` with the exception type in the detail; `GitRepo` memoized per toplevel (one build, one `rev-parse` per toplevel per read) |
| 13 U-G5/G6 | PASS | real temp repo: tracked edit changes the snapshot; a same-size rewrite is caught by `mtime_ns` (pinned with `os.utime`); not changed by a declared output, a tracked file under `.orchestrator`, or an untracked file; deleted file -> `(None, None)`; failures (timeout, `OSError`, exit 128, non-empty hooks dir, outside workspace) -> `repo_worktree_probe_failed` |
| 14 U-G7 | PASS | `.git` in a parent of the workspace: project dir inside is non-git, the runner spy has zero calls, `nested_repo_marker` reports the outer marker (nearest ancestor; none for a marker at/below the root); repo path outside the workspace (incl. a textual-prefix sibling `ws-evil`) -> `path_rejected` |
| 15 U-G8 | PASS | spy on `GitRepo(...)`: `timeout == 10`, `env["GIT_OPTIONAL_LOCKS"] == "0"`, injected `runner` and `hooks_dir`; the scripted runner also sees env and timeout; AST test: no `_run` / `probe` name or attribute in `repo_state.py` (AST rather than grep because the module docstring names both) |
| 16 hygiene | PASS | see Evidence |

## Evidence
- `.venv/bin/python -m pytest -q tests/cache/test_hashing.py tests/cache/test_repo_state.py` ->
  77 passed (34 + 43).
- `.venv/bin/python -m pytest -q tests/cache tests/test_spawn_provenance.py` -> 517 passed
  (`tests/cache` alone: 503, up from 426).
- `.venv/bin/ruff check src tests` -> All checks passed.
- `.venv/bin/ruff format --check src tests` -> only the pre-existing generated
  `src/agent_orchestrator/_build_info.py` would be reformatted.
- `.venv/bin/mypy src tests/cache` -> only the 4 pre-existing `_version.py` errors.
- AST guard (`tests/cache/test_ast_guard.py`): passes (hashing opens files only through `safeio`).
- Full suite: run once for the whole group (T-8tr1H4 + T-uoYW6b); result recorded in
  `T-uoYW6b-cache-key-builder/STATUS.md`.

## Risks / Blockers
- No blockers.
- Residual (documented, A-12 / R-19): a workspace nested inside a parent repository is treated as
  non-git; the banner warns through `nested_repo_marker`.
- Heads-up for T-gDNjN2: `RepoHeadReader.read` caches only the `GitRepo` objects; every call reads
  HEAD afresh (the settle-time HEAD guard needs a new value). Construct one reader and one probe
  per run and pass the same `hooks_dir` / `runner` conventions.

## Next actions
1. T-uoYW6b builds `fingerprint` and `keys` on `hashing.digest_path` / `HashBudget`.
2. T-gDNjN2 wires `RepoHeadReader`, `WorktreeProbe` and `nested_repo_marker`.

## Comments
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Status initialized (Draft, Rev 2).
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 3 re-plan after the
  early-gate review and the manager's scope decisions (HLD §23.5).
- By: developer · Role: developer · Date: 2026-10-05 · Comment: State -> Done (commit `00b9a3c`).
  All 16 acceptance criteria pass; two deviations from the HLD code blocks (child environment
  layering, per-call HEAD memo) are documented in `HANDOFF.md`. Matches `TASK.md`, `HANDOFF.md`
  and the epic rollup.
- By: developer · Role: developer · Date: 2026-10-05 · Comment: G1a remediation touched this task's code (SEC-03, SEC-04 (repo state)) in commit `763375f`; findings and regression tests are listed in `T-fXWbqg-cache-review-gates/STATUS.md` (G1a remediation) from `output/E-Rc4Hk8-cross-run-result-cache/review-g1a.md` and `review-g1a-security.md`. Task state stays Done; matches `HANDOFF.md`.
- By: developer · Role: developer · Date: 2026-10-05 · Comment: G1b remediation touched this task's code (sec S-1 (directory hashing ignores .ao-result-cache-*.tmp[.bak] regular files)) in commit `6ba90ba`; findings and regression tests are listed in `T-fXWbqg-cache-review-gates/STATUS.md` (G1b remediation). Task state stays Done.
By: developer · Role: developer · Date: 2026-10-05 · Comment: G2 remediation: the restore-temp-name exemption in `hash_directory` (G1b S-1) was removed (sec G2-S1: any writer could hide a file from the key; demonstrated poisoned hit). Directory hashing now skips only the git dir, `skip_abs` and `exclude_abs`; a staging-named file is hashed like any other (a leftover is a safe false miss; `ao cache prune` sweeps it). Tests flipped in `tests/cache/test_hashing.py`; GV-1 unchanged. Task state unchanged. See `T-fXWbqg-cache-review-gates/STATUS.md` (G2 remediation).
