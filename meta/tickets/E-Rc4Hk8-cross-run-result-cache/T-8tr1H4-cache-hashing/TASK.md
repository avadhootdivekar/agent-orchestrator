# TASK: T-8tr1H4-cache-hashing

## Metadata
- Task ID: `T-8tr1H4-cache-hashing`
- Epic ID: `E-Rc4Hk8-cross-run-result-cache`
- Owner: `developer` (Dev C)
- Created: `2026-10-05`
- Last Updated: `2026-10-05`
- Status: `Draft`
- Estimate: `14 focus hours (1.75 days)` · Sprint 1, Wave 2

## Requirements Mapping
- Requirement IDs: FR-3, FR-6 (guards 2 and 3), NFR-4, NFR-10 (M-5, M-6, M-7)
- HLD: §8.2.3 (bounded hashing), §8.2.4 (repo state), N-6
- ADR-0019: D5, D8, D13

## Description
Implement the two I/O-bearing building blocks of key construction and the store guards.

1. **`cache/hashing.py`** (HLD §8.2.3):
   - `HashBudget(max_bytes, max_files)`, with `charge()` raising
     `UncacheableError(input_too_large)`;
   - `hash_regular_file(abs_path, budget) -> Digest`. It is bounded and FIFO-safe via
     `safeio.open_regular_read`. A size change mid-read raises `input_unstable`.
   - `hash_directory(abs_dir, budget, *, exclude_abs, skip_abs) -> Digest`. The canonical manifest
     `[["D", rel] | ["F", rel, size, sha]]` is sorted by `os.fsencode(rel)` and hashed through
     `types.canonical_json` with `DIR_DIGEST_SCHEMA`. It skips `.git`, `skip_abs` and
     `exclude_abs`. A symlink or special file inside the directory raises `input_not_regular`.
   - `digest_path(abs_path, budget, *, exclude_abs=frozenset(), skip_abs=frozenset())`.
2. **`cache/repo_state.py`** (HLD §8.2.4):
   - `has_git_marker(path) -> bool`: a filesystem walk up to `/` looking for a `.git` entry,
     using `lstat`. **Do not use `GitRepo.probe()`**: it hides failures (reviewer R8).
   - `RepoHeadReader(*, runner=None, hooks_dir=None, timeout=CACHE_GIT_TIMEOUT_SECONDS)` with
     `.read(repo_paths) -> dict[str, str]`:
     - non-git repos are omitted;
     - an unborn HEAD gives `"unborn"`;
     - results are memoized per toplevel;
     - `GitError`, `OSError`, `RuntimeError` and `subprocess.TimeoutExpired` all map to
       `UncacheableError(repo_head_unavailable)`.

     Construct `GitRepo(path, timeout=CACHE_GIT_TIMEOUT_SECONDS, runner=..., hooks_dir=...)`.
     Never use the 300 s default. `GitRepo.__init__` can raise `OSError`/`RuntimeError` when the
     empty-hooks dir cannot be created (developer #1).
   - `WorktreeProbe.snapshot(repo_paths, workspace_root, exclude_abs) -> frozenset[tuple]`:
     - tracked changes only, via `status_porcelain(top, untracked=False)`;
     - plus `(mtime_ns, size)` per path;
     - excluding `<ws>/.orchestrator/**` and the declared outputs;
     - failures map to `UncacheableError(repo_worktree_probe_failed)`.

## File scope (exclusive)
- `src/agent_orchestrator/cache/hashing.py`, `src/agent_orchestrator/cache/repo_state.py` (new)
- `tests/cache/test_hashing.py`, `tests/cache/test_repo_state.py` (new)

## Inputs / Outputs
- **Inputs:** T-FJH6LI commit 2 (`safeio`) and commit 3 (`types`: `Digest`, `UncacheableError`,
  `canonical_json`); `isolation.git.GitRepo`.
- **Outputs:** bounded digests; repo HEAD maps; tracked-worktree snapshots.

## Acceptance Criteria
1. **U-H1 (file digest).** A file's digest equals `hashlib.sha256(content)`, with `size` equal to
   its byte length.
2. **U-H2 (directory determinism).** A directory's digest is identical when the same tree is
   created in a different order. Renaming one file changes it.
3. **U-H3 (empty and non-UTF-8 names).** An empty directory gives a stable digest with
   `size == 0`. Non-UTF-8 file names hash deterministically (surrogate escapes).
4. **U-H4 (links and special files).**
   - A symlink inside a directory raises `UncacheableError(input_not_regular)`.
   - A FIFO inside a directory raises the same reason within 5 s (thread + join timeout).
   - A FIFO given directly as an input raises the same reason.
5. **U-H5 (budget).**
   - Exceeding `max_bytes` or `max_files` raises `input_too_large`, with the detail carrying the
     counts.
   - The check runs **before** reading the file's bytes: spy on `os.read`.
6. **U-H6 (unstable input).** A file that grows during hashing raises `input_unstable`. Simulate
   it with a patched `os.read` that returns extra bytes.
7. **U-H7 (skips).**
   - `.git` directories and `<ws>/.orchestrator` are skipped.
   - Paths in `exclude_abs` (the task's own outputs) are skipped.
8. **U-H8 (missing and unreadable).**
   - A missing path raises `input_missing`.
   - An `EACCES` on open raises `input_unreadable`.
9. **U-G1 (`has_git_marker`).**
   - True inside a temp repo, including a subdirectory of it, and for a `.git` *file*
     (worktree-style).
   - False in a plain temp directory.
10. **U-G2..G4 (head reader).**
    - A plain directory is omitted from the result.
    - An unborn repo gives `"unborn"`.
    - A repo with a commit gives its sha.
    - Each of these gives `repo_head_unavailable` with the exception type name in the detail: a
      failing `rev_parse`, a runner raising `TimeoutExpired`, a runner raising `OSError`, and a
      `GitRepo` construction raising `RuntimeError` (non-empty hooks dir).
    - Results are memoized: the runner is called once per toplevel per reader instance.
    - The timeout passed to `GitRepo` is 10 s.
11. **U-G5 (worktree snapshot).**
    - Editing an undeclared tracked file changes the snapshot. A same-size rewrite is caught by
      `mtime_ns`.
    - Editing a declared output, or a file under `.orchestrator`, does not change it.
    - A new **untracked** file does not change it. This is the documented residual risk.
12. **U-G6.** A probe failure raises `repo_worktree_probe_failed`.
13. **Hygiene.**
    - VCS tests use temp repos, an injected `hooks_dir`, and `AO_STATE_DIR` pointed at
      `tmp_path`.
    - The AST guard (U-AST) passes: no bare `open(` outside `safeio`.
    - `ruff` and `mypy` are clean.
    - `pytest -q` has no new failures.

## Test requirements
- `tests/cache/test_hashing.py`: AC-1..AC-8.
- `tests/cache/test_repo_state.py`: AC-9..AC-12.

## Risks
- **Platform differences in FIFO and permission behaviour.** Skip the `EACCES` case when running
  as root (`os.geteuid() == 0`).
- **Slow VCS calls in CI.** The runner is injectable; only a few tests use real repositories.

## Dependencies
- T-FJH6LI commit 2 (`safeio`) and commit 3 (`types`).

## Pseudocode / Algorithm
```text
HLD §8.2.3 (HashBudget, hash_regular_file, hash_directory, digest_path) and §8.2.4
(has_git_marker, RepoHeadReader.read, WorktreeProbe.snapshot) verbatim.
```

## Schemas / Interface Notes
- **Interface:**
  - `digest_path(...) -> Digest`;
  - `RepoHeadReader.read(Mapping[str, str]) -> dict[str, str]`;
  - `WorktreeProbe.snapshot(...) -> frozenset[tuple]`;
  - `has_git_marker(path) -> bool`.
- **Spec / data schema:** directory manifest `ao.result-cache.dir/v1` (HLD D8).

## Handoff Boundary
- **Upstream:** T-FJH6LI.
- **Downstream:** T-uoYW6b (hashing) and T-gDNjN2 (`RepoHeadReader`, `WorktreeProbe`).

## Artifacts
- **Docs/comments:** `meta/tickets/E-Rc4Hk8-cross-run-result-cache/T-8tr1H4-cache-hashing/`
- **Large outputs:** N/A

## Comments
- By: architect · Role: architect · Date: 2026-10-05 · Comment: New in Rev 2. Split out of
  T-uoYW6b so that one task stays ≤ 3 days (developer #10). Incorporates developer #1 (exception
  set, timeout, injectable runner and hooks dir), reviewer R8 (filesystem repo detection) and the
  tracked-worktree guard (reviewer R1).
