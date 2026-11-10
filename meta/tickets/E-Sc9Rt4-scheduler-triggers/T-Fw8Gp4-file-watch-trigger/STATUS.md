# STATUS

- ID: `T-Fw8Gp4-file-watch-trigger`
- Updated At: 2026-11-11
- State: Partial — core landed; CLI flags (`ao schedule add --watch ...`) and the `ao schedule daemon --once` e2e (AC12) are NOT done
- Owner: developer agent (overseer unit w12-03-a1-file-watch)

## This update
- `src/agent_orchestrator/service/watch.py` added: `WatchDigestStore`, `scan`, `changed_paths`, `FileWatchSource` (observe pure/read-only, commit saves digest), `DEFAULT_WATCH_IGNORES` (incl. `.orchestrator`), `WATCH_SCAN_BUDGET_SECONDS`.
- `scheduler.py`: only the `EventScheduler` docstring changed (deprecated; successor named). No `FileWatchScheduler` class was added (the ticket allows omitting it).
- `tests/service/test_watch.py`: 22 tests (store, scan globs/ignores/max_files/budget, path-escape, symlink file/dir/dangling/into-ignored, debounce, F6 read-only, crash-replay `already_fired`, concurrency skip keeps change, `.orchestrator` self-trigger).

## Evidence
- ruff check/format clean on touched files; `mypy src`: 0 errors (227 files); `pytest tests/service tests/schedules tests/test_scheduler.py tests/test_wave_scheduler.py`: all green; coverage `service/watch.py` 93%.

## Risks / Blockers
- No `ao schedule` CLI exists in this tree yet (`T-Cl6Jn9` not landed): AC12 (CLI e2e) and the `--watch/--debounce/--poll-seconds/--max-files` flags are pending. Engine-level equivalents are covered in `test_watch.py`.
- `WatchSpec` (frozen, not touched) has no `max_files`; `FileWatchSource` takes it as a constructor arg (default 5000). Wiring a per-binding value needs a model change.
- Engine hook: `register_trigger_source("file_watch", factory)` already exists; a production factory (`FileWatchSource(root, binding.watch, WatchDigestStore(state_dir))`) is not yet registered in `service/schedule_engine.py` / service startup (engine file owned by another unit).
- First observation with no stored digest treats every matching file as `created` (HLD §6.7 verbatim) — a new watch fires once on existing files.
- Directory symlinks are never followed (even in-root); in-root file symlinks are scanned via their target.

## Next actions
1. Register the production factory in the service wiring; add `max_files` to `WatchSpec` if per-binding control is wanted.
2. Land `--watch*` flags + AC12 e2e once `ao schedule` exists.
3. `T-Se4Bk5` to review `scan` path handling.
