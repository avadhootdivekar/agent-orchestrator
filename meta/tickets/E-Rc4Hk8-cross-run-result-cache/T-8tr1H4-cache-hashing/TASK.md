# TASK: T-8tr1H4-cache-hashing

## Metadata
- Task ID: `T-8tr1H4-cache-hashing`
- Epic ID: `E-Rc4Hk8-cross-run-result-cache`
- Owner: `developer` (Dev C)
- Created: `2026-10-05`
- Last Updated: `2026-10-05` (Rev 3; implemented)
- Status: `Done`
- Estimate: `14 focus hours (1.75 days)` · Sprint 1 (after T-FJH6LI)

## Requirements Mapping
- Requirement IDs: FR-3, FR-6 (guards 2 and 3), NFR-4, NFR-10 (M-5, M-6, M-7)
- HLD: §8.2.3 (bounded hashing, key-hygiene note), §8.2.4 (repo state), N-6
- ADR-0019: D5, D8, D13

## Description
Implement the two I/O-bearing building blocks of key construction and the store guards.

1. **`cache/hashing.py`** (HLD §8.2.3): `HashBudget`; `hash_regular_file` (bounded, FIFO-safe,
   `input_unstable` on a size change); `hash_directory` (canonical sorted manifest through
   `types.canonical_json`; skips `.git`, `skip_abs` and `exclude_abs`; a symlink or special file
   inside raises `input_not_regular`); `digest_path`.
2. **`cache/repo_state.py`** (HLD §8.2.4):
   - **`find_git_toplevel(path, *, workspace_root) -> str | None`**: the directory holding the
     nearest `.git` entry, walking up from `path` to `workspace_root` **inclusive and never
     above it**. A path outside the workspace raises `UncacheableError(path_rejected)`. A `.git`
     in `$HOME` or any other ancestor of the workspace is never consulted (early-gate A2).
   - **`nested_repo_marker(workspace_root) -> str | None`**: the first ancestor above the
     workspace root holding a `.git` entry (lstat only); used only for the banner warning.
   - **`RepoHeadReader(*, workspace_root, runner=None, hooks_dir=None,
     timeout=CACHE_GIT_TIMEOUT_SECONDS)`** with `.read(repo_paths)`: non-git repos omitted;
     unborn HEAD → `"unborn"`; memoized per toplevel. The toplevel is the marker directory. It
     uses **public `GitRepo` methods only** (`rev_parse`, `current_branch`) on
     `GitRepo(top, timeout=10, runner=..., hooks_dir=..., env={GIT_OPTIONAL_LOCKS_VAR:
     GIT_OPTIONAL_LOCKS_OFF})`; **never** `GitRepo._run` and never `GitRepo.probe()`.
     `GitError`, `OSError`, `RuntimeError` and `subprocess.TimeoutExpired` (including from the
     `GitRepo` constructor) → `UncacheableError(repo_head_unavailable)`.
   - **`WorktreeProbe.snapshot(repo_paths, workspace_root, exclude_abs)`**: tracked changes only,
     via `status_porcelain(top, untracked=False)` with the same `GitRepo` settings, plus
     `(mtime_ns, size)` per path, excluding `<ws>/.orchestrator/**` and the declared outputs. Any
     failure → `UncacheableError(repo_worktree_probe_failed)`. The coordinator calls it lazily and
     maps a failure to "not storable", never to "ineligible".

## File scope (exclusive)
- `src/agent_orchestrator/cache/hashing.py`, `src/agent_orchestrator/cache/repo_state.py` (new)
- `tests/cache/test_hashing.py`, `tests/cache/test_repo_state.py` (new)

## Inputs / Outputs
- **Inputs:** T-FJH6LI commit 3 (`safeio`, `types`: `Digest`, `UncacheableError`,
  `canonical_json`; `constants`); `isolation.git.GitRepo` (public API).
- **Outputs:** bounded digests; repo HEAD maps; tracked-worktree snapshots; the nested-repo
  marker.

## Acceptance Criteria
1. **U-H1.** A file's digest equals `hashlib.sha256(content)`, with `size` equal to its length.
2. **U-H2.** A directory's digest is independent of creation order; renaming one file changes it.
3. **U-H3.** An empty directory has a stable digest with `size == 0`; non-UTF-8 names hash
   deterministically.
4. **U-H4.** A symlink inside a directory → `input_not_regular`.
5. **U-H5.** A FIFO inside a directory and a FIFO given directly → `input_not_regular`, within
   5 s (thread + join timeout; `skipif(not hasattr(os, "mkfifo"))`).
6. **U-H6.** Exceeding `max_bytes` or `max_files` → `input_too_large` with the counts in the
   detail, checked **before** reading the bytes (spy on `os.read`).
7. **U-H7.** A file that grows while being read (patched `os.read`) → `input_unstable`.
8. **U-H8.** A missing path → `input_missing`; a **simulated** `PermissionError` (monkeypatched
   `os.open`) → `input_unreadable`. No `chmod`-based test (CI may run as root).
9. **U-H9.** `.git` and `<ws>/.orchestrator` are skipped by directory walks.
10. **U-H10.** Paths in `exclude_abs` (the task's own outputs) are skipped.
11. **U-G1.** `find_git_toplevel` returns the marker directory for a repo, a subdirectory of it,
    and a `.git` *file* (worktree-style); None for a plain directory.
12. **U-G2..G4.** The head reader omits plain directories, returns `"unborn"` and real shas, and
    maps a failing `rev_parse`, a runner raising `TimeoutExpired`, a runner raising `OSError`,
    and a `GitRepo` construction raising `RuntimeError` (non-empty hooks dir) to
    `repo_head_unavailable` with the exception type in the detail. It is memoized per toplevel.
13. **U-G5/G6.** The worktree snapshot changes on an undeclared tracked edit (a same-size rewrite
    is caught by `mtime_ns`), not on a declared output, a file under `.orchestrator` or a new
    untracked file. A probe failure → `repo_worktree_probe_failed`.
14. **U-G7.** With a `.git` directory in a parent of the workspace (simulated `$HOME`), a plain
    project dir inside the workspace is non-git, no VCS command runs against the outer
    repository (runner spy), and `nested_repo_marker` reports the outer marker. A repo path
    outside the workspace → `path_rejected`.
15. **U-G8.** Every `GitRepo` built by this module receives `env` with `GIT_OPTIONAL_LOCKS=0`
    and `timeout=10` (spy); the module never references `_run` or `probe` (grep test).
16. **Hygiene.** VCS tests use temp repos, an injected `hooks_dir` and `AO_STATE_DIR` pointed at
    `tmp_path`; symlink tests carry `skipif(sys.platform == "win32")`; the AST guard passes;
    ruff (≤ 100 columns) and mypy are clean; `pytest -q` has no new failures.

## Test requirements
- `tests/cache/test_hashing.py`: AC-1..AC-10.
- `tests/cache/test_repo_state.py`: AC-11..AC-15.

## Risks
- **The nested-workspace residual** (A-12, R-19): documented; the banner warns.
- **Slow VCS calls in CI.** The runner is injectable; few tests use real repositories.

## Dependencies
- T-FJH6LI (commit 3, i.e. the whole task).

## Pseudocode / Algorithm
```text
HLD §8.2.3 and §8.2.4 verbatim.
```

## Schemas / Interface Notes
- **Interface:** `digest_path(...) -> Digest`; `find_git_toplevel(path, *, workspace_root)`;
  `nested_repo_marker(workspace_root)`; `RepoHeadReader.read(...)`; `WorktreeProbe.snapshot(...)`.
- **Spec / data schema:** directory manifest `ao.result-cache.dir/v1` (D8).

## Handoff Boundary
- **Upstream:** T-FJH6LI.
- **Downstream:** T-uoYW6b (hashing), T-gDNjN2 (`RepoHeadReader`, `WorktreeProbe`,
  `nested_repo_marker`).

## Artifacts
- **Docs/comments:** `meta/tickets/E-Rc4Hk8-cross-run-result-cache/T-8tr1H4-cache-hashing/`
- **Large outputs:** N/A

## Comments
- By: architect · Role: architect · Date: 2026-10-05 · Comment: New in Rev 2 (split out of
  T-uoYW6b; developer #1, reviewer R8).
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 3 (early-gate A1c, A2, C,
  D; manager B): depends on T-FJH6LI commit 3; repository detection stops at the workspace root;
  public `GitRepo` API only with `GIT_OPTIONAL_LOCKS=0`; `nested_repo_marker`; explicit
  U-H1..U-H10 and U-G7/U-G8; simulated `EACCES`.
- By: developer · Role: developer · Date: 2026-10-05 · Comment: State -> Done. Delivered as commit
  `00b9a3c`. All 16 acceptance criteria pass; evidence in `STATUS.md`, frozen names and the four
  small deviations in `HANDOFF.md`.
