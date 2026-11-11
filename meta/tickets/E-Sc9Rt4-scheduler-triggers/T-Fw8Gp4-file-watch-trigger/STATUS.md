# STATUS

- ID: `T-Fw8Gp4-file-watch-trigger`
- Updated At: 2026-11-11
- State: Partial — core landed, w13-03 review H1/H2/M1-M4 fixed (see 'Review fixes'); CLI flags (`ao schedule add --watch ...`) and the `ao schedule daemon --once` e2e (AC12) are NOT done
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

## Review fixes (w13-03 Opus review) — By: developer · Role: developer · Date: 2026-11-11
Unit w14-01. `service/watch.py`, plus an engine-contract change in `service/schedule_engine.py` / `service/fire_store.py`. Each fix has a regression test in `tests/service/test_watch.py` that was mutation-checked (reverting the fix fails a test).
- **H1** `observe` seeds/refreshes the baseline digest when nothing *wanted* changed (the only write it makes; nothing can be swallowed), so `on: [modified]` fires on the first edit, including for files created later.
- **H2** `workflows/*/runs/**` (template instance trees) is ignored by shape, so a template-launched schedule cannot re-trigger on its own scaffolded `prompt.md`.
- **M1** `Candidate.skip_catch_up` (engine): an observed change is never "late" because its mtime is old. `cp -p`/`tar x` files and `catch_up: skip` + `debounce >= grace` now fire.
- **M2** `Candidate.discriminator` is mixed into `fire_key(..., discriminator=)` (empty = legacy key, other kinds unchanged). It is a hash of `(path, prev sig, new sig)`, so same-second states and `cp -p` reverts get distinct keys, while crash replay is still deterministic.
- **M3** The scan is an explicit `scandir` walk: budget and visit cap are checked per entry, and it is pruned to each pattern's static prefix and to directories that can still match.
- **M4** `poll_seconds` is honoured through an in-memory gate (monotonic clock). It is skipped while a change is pending, so a concurrency skip retries on the next tick.
- **Lows fixed** L1 (`WatchSpec.max_files`, schema regenerated), L2 (integer whole-second truncation, no round-up), L3 (`watch.changed` emitted once on commit, budget event once per episode, no double logging), L4 (`lstat` + `S_ISREG`), L5 (negated classes never match `/`, a leading `^` is negation).
- **Production wiring** `ScheduleEngine` registers `file_watch_factory(state_dir)` by default (caller can override via `register_trigger_source`).
- Pinned: whole-second truncation, visit cap, debounce boundary (exactly `debounce` fires).
- **Not fixed (reasons):** **L6** orphan digest files: safe by design, cleanup belongs with schedule removal (`ao schedule rm`, T-Cl6Jn9). **L7** overlap `skip` drops edits: engine-wide cron semantics, document/recommend `overlap: queue`. **H2 generalisation:** custom template `instance_dir`s outside `workflows/*/runs` are not auto-ignored (only that shape is). **Queued fire across restart:** a file_watch fire buffered by `overlap: queue` re-derives its key without the discriminator after a restart, so it is dropped loudly (`queue_lost`); persisting the discriminator in `ScheduleState` is a follow-up. AC12 CLI e2e is still pending the `ao schedule` CLI.
- Evidence: `pytest tests/service tests/schedules tests/test_scheduler.py` 873 passed, 1 failed (`test_governance_gate::test_real_renderer_does_not_yet_register_cost_cap...`, caused by another unit's uncommitted `engine.py`/`run_options.py` cost-cap work, not these paths). `ruff check`/`format`, `mypy src` clean. `test_watch.py` 22 -> 43 tests.

## Re-review fixes (w15-01 Opus re-review) — By: developer · Role: developer · Date: 2026-11-11
Unit w16-01. Each fix has a regression test in `tests/service/test_watch.py`, mutation-checked (reverting the fix fails it).
- **N1** `scan` skips any start dir with a symlink component (lstat walk from root) with a warning: symlink to outside root, into `.orchestrator`, and mid-prefix are all empty.
- **N2** `ScheduleState.queued_discriminator` (expand-only, default `""`) is persisted by `set_queued`, cleared by `clear_queued`, and used by `_drain_queued` to re-derive the key, so an `overlap: queue` file-watch fire survives a restart (`queue_drain`, no `queue_lost_on_restart`).
- **N3** Changed paths with a future mtime are excluded from the settle time (`newest`), so they no longer starve other changes; a once-per-episode `schedule.watch_future_mtime` event is emitted. Deliberately NOT the review's "clamp to now": that would make `scheduled_for` (hence the fire key) depend on observation time and break crash-replay determinism, and `now - newest` would never reach the debounce. If only future-dated paths changed, the watch waits until their mtime passes.
- **Lows fixed** L-b (observe-purity docstrings in `watch.py` and the `TriggerSource` protocol reworded; HLD §6.7/§7.1 text in `docs-md/scheduler-triggers-hld.md` left for T-Hq3vXd), L-c (`file_watch_factory(monotonic=)`, engine passes its own), L-d (`pattern_under_ignored_tree` warns at source construction), L-e (dead `^` branch removed), L-f (`already_fired` decision key pinned in the crash-replay test).
- **Not fixed:** L-a (A→B→A→B preserved-mtime cycle swallowed) — rare, needs a persisted commit counter; accepted and documented here. Unpinned L4 and static-prefix-start mutations unchanged (harmless).
- Evidence: `test_watch.py` + `test_schedule_engine.py` + `tests/test_scheduler.py` + `tests/schedules`: 424 passed, 1 failed (`test_cli_e2e::test_run_now_without_daemon_fires_in_process`, another unit's in-flight cli/cost-cap edits). `tests/service`: other units' uncommitted `webhook.py`/`cli.py`/`schedule_launch.py`/cost-cap work causes 15 unrelated failures. ruff clean on own paths; `mypy src` errors only in other units' `webhook.py`.

## Update 2026-11-11 — `add --watch` flags (unit `w21-08-a1-schedule-add-watch`)
`--watch/--watch-on/--debounce/--poll-seconds/--max-files` shipped in `schedules/cli.py` with CliRunner tests incl. a `daemon --once` e2e (baseline, one fire, then none). See `T-Cl6Jn9-schedule-cli/STATUS.md`.
