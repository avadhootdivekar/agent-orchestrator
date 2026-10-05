# HANDOFF: T-8tr1H4-cache-hashing

- Task: `T-8tr1H4-cache-hashing`
- State: `Done (handoff available)`
- From: `developer` (Dev C)
- To: T-uoYW6b, T-gDNjN2
- Commit: `00b9a3c` on branch `worktree-agent-a18ce2c08e42a3a5a`.

## What was delivered
- `agent_orchestrator.cache.hashing`:
  - `HashBudget(max_bytes, max_files)` with `.charge(nbytes, nfiles)` (cumulative; raises
    `input_too_large` with `bytes=<n> files=<n>`);
  - `hash_regular_file(abs_path, budget) -> Digest`;
  - `hash_directory(abs_dir, budget, *, exclude_abs, skip_abs) -> Digest`;
  - `digest_path(abs_path, budget, *, exclude_abs=frozenset(), skip_abs=frozenset()) -> Digest`.
- `agent_orchestrator.cache.repo_state`:
  - `find_git_toplevel(path, *, workspace_root) -> str | None`;
  - `nested_repo_marker(workspace_root) -> str | None`;
  - `RepoHeadReader(*, workspace_root, runner=None, hooks_dir=None, timeout=10)` with
    `.read(repo_paths) -> dict[str, str]`;
  - `WorktreeProbe(*, runner=None, hooks_dir=None, timeout=10)` with
    `.snapshot(repo_paths, workspace_root, exclude_abs) -> frozenset[tuple]`.
- Tests: `tests/cache/test_hashing.py` (34), `tests/cache/test_repo_state.py` (43).

## Frozen names / contracts
- The signatures above (HLD §8.2.3 and §8.2.4) and the reason strings `input_missing`,
  `input_not_regular`, `input_unstable`, `input_too_large`, `input_unreadable`,
  `repo_head_unavailable`, `repo_worktree_probe_failed`, `path_rejected`.
- Snapshot tuple shape: `(toplevel, path, index_code, worktree_code, mtime_ns | None, size | None)`.
- A directory counts as one file against `max_files`. `hash_directory` raises
  `input_unreadable` for a `scandir` / stat `OSError` on any directory it walks.

## Deviations from the HLD code blocks (reason)
1. **Child environment is layered.** The HLD passes `env={GIT_OPTIONAL_LOCKS: "0"}`. But
   `GitRepo.env` REPLACES the child environment (no `PATH`, `HOME`, ...), which breaks any
   non-default git install. The module passes `{**os.environ, "GIT_OPTIONAL_LOCKS": "0"}`
   instead; the lock switch is still always present (U-G8).
2. **HEAD memo scope.** The `GitRepo` instance is memoized per toplevel across calls (as the HLD
   says), and within one `read()` call a HEAD is read once per toplevel (several repo ids in one
   toplevel). A HEAD value is never cached across calls, because the settle-time guard needs a
   fresh read.
3. **Shared private base.** `RepoHeadReader` and `WorktreeProbe` share `_GitReaders` (the one
   place that builds `GitRepo`), so the two cannot drift apart on settings. `WorktreeProbe` takes
   the same `runner` / `hooks_dir` / `timeout` keyword arguments as the reader (the HLD says only
   "same GitRepo settings").
4. **U-G8 "never references `_run` or `probe`"** is an AST test (no such name or attribute),
   because the module docstring legitimately mentions both.

## Verification the receiver should run
- `pytest -q tests/cache/test_hashing.py tests/cache/test_repo_state.py` (77 tests)

## Comments
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Handoff stub created (Rev 2).
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 3: workspace-bounded repo
  detection and `nested_repo_marker`.
- By: developer · Role: developer · Date: 2026-10-05 · Comment: State -> Done; handoff available
  (commit `00b9a3c`). Deviations 1-4 above are small and documented; the frozen names are
  unchanged.
