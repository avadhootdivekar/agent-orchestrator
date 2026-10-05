# HANDOFF: T-U7ckfd-cache-store-core

- Task: `T-U7ckfd-cache-store-core`
- State: `Done (handoff available)`
- From: `developer` (Dev C)
- To: T-HjxNQ0 (same file), T-u3jG8F, T-gDNjN2, T-6tRKml

## What was delivered
- `agent_orchestrator.cache.store.LocalFsCacheStore(CacheStore)`: the full `CacheStore` surface plus
  `for_workspace`, public `__init__(workspace_root, *, max_bytes, ttl_days)`, `ensure_layout`,
  `root`, `max_bytes`, `ttl_days`; `CacheUnsafePathError` on unsafe components, `CacheLayoutError`
  on root problems.
- `agent_orchestrator.cache.store.is_expired(created_at, now, ttl_days)`.
- `maybe_enforce_limits` is a placeholder returning `None` (`TODO(T-HjxNQ0)`).
- Commit `181bbbb` on branch `worktree-agent-a18ce2c08e42a3a5a`.

## Frozen names / contracts
- The layout paths (HLD 8.4.1), the canonical entry bytes, and "checks before every operation".
- Private seams T-HjxNQ0 reuses: `_checks(target_dir)`, `_check_root()`, `_entry_path`, `_blob_path`,
  `_new_tmp(kind)`, `_atomic_write_bytes(dst, body, *, kind)`, `_unlink_quietly`, `_add_approx(n)`,
  `_approx_total` (None = unknown), `_ws`, `_orchestrator_dir`.

## Deviations from the HLD code block (reason)
1. `put_entry` checks the 1 MiB file bound BEFORE `ensure_layout` (the HLD does it after): a refused
   entry creates no directories. Behaviour otherwise identical.
2. `ensure_layout` also maps `safeio.UnsafePathError` (symlinked `entries/`, `entries/v1`, `blobs/`,
   `tmp/` ...) to `CacheUnsafePathError`, and `_atomic_write_bytes` / `put_blob` do the same for their
   `ensure_dir_chain` calls, so a link planted in the check-to-use window is also
   `CacheUnsafePathError` and never a bare `SafeIOError`.
3. `ensure_layout` creates `.orchestrator` itself when missing, with mode `0o777` under the umask (it
   is shared with run state); only the cache root and below are forced to `0o700`. It reads
   `layout.json` right after creating the root, so an unknown layout is refused before anything else
   is created. Layout files are written through `_atomic_write_bytes(kind="entry")` only if absent
   (`lexists`).
4. `_ws` is `os.path.abspath` (not `realpath`) so the factory does no I/O; `check_root_dir` itself
   resolves real paths for the containment check.
5. `os.utime(..., follow_symlinks=False)` is called directly (Linux/macOS); there is no Windows
   fallback (the cache is POSIX-only in practice, tests carry skip markers).

## Verification the receiver should run
- `pytest -q tests/cache/test_store_core.py`

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: Handoff stub created.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2 contents.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 3: `CacheStore`-only class;
  unsafe-path semantics. State `Draft` mirrors `TASK.md` and `STATUS.md`.
- By: developer · Role: developer · Date: 2026-10-05 · Comment: State -> Done; handoff available (commit `181bbbb`). Deviations 1-5 above are small and behaviour-preserving.
