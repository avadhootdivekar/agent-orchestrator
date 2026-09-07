# TASK: T-Fw8Gp4-file-watch-trigger

## Metadata
- Task ID: `T-Fw8Gp4-file-watch-trigger`
- Epic ID: `E-Sc9Rt4-scheduler-triggers`
- Owner: developer agent
- Created: 2026-09-06
- Last Updated: 2026-09-06
- Status: Draft
- Estimate: 2 days

## Requirements Mapping
- Requirement IDs: FR-11 (see `../EPIC.md`)

## Description
The first event trigger: fire a workflow when a watched path or glob appears or changes — the
"re-run the epic-runner when `prompt.md` changes" case. Implemented as **polling behind the
existing `Scheduler` ABC**, not inotify (ADR-0014 D7).

The design property that makes this correct rather than merely working: `scheduled_for` is the
**newest changed mtime**, not the observation time. That makes a fire content-derived, so the
same file state derives the same `fire_key`, and `T-Fr2Nx8`'s at-most-once store makes the fire
replay-safe across a crash for free. Do not "simplify" it to `now`.

Read HLD §6.7 and §7.1, and ADR-0014 D7.

Files you own (create/edit freely):
- `src/agent_orchestrator/service/watch.py` (new — the digest store and scanner)
- `src/agent_orchestrator/scheduler.py` (edit: add `FileWatchScheduler`; keep `EventScheduler`
  as a deprecated alias whose existing behavior and tests are untouched)
- `tests/service/test_watch.py`, `tests/schedules/test_file_watch_scheduler.py` (new)
- additive: register the `file_watch` kind in the engine's scheduler map (a one-line edit to
  `service/schedule_engine.py`'s `SCHEDULERS` — coordinate with `T-Ev3Qm5`'s owner if concurrent)
- additive: `--watch GLOB`, `--watch-on`, `--debounce`, `--poll-seconds`, `--max-files` on
  `ao schedule add`

Do NOT touch: `service/fire_store.py`, `service/schedule_launch.py`, `schedules/models.py`
(the `WatchSpec` model already landed in `T-Sd1Kq7`), `ui/`.

## Acceptance Criteria
1. `WatchDigestStore(state_dir)` persists `{rel_path: [mtime_ns, size]}` per
   `(workspace_slug, schedule_id)` at `<state_dir>/schedules/watch/<slug>/<id>.json`, via
   `service/statefile.py`'s atomic helpers.
2. `scan(workspace_root, watch_spec, *, max_files, budget_seconds, monotonic)` expands each glob
   **after** resolving the workspace root through `LocalFsArtifactStore`'s guard, skips symlinks
   that resolve outside the root (with a warning), applies `DEFAULT_WATCH_IGNORES` =
   `{".git", "node_modules", ".venv", "__pycache__", ".orchestrator", ".ao/…state"}`, and returns
   `(digest, truncated: bool)`. It stops and sets `truncated` on exceeding either `max_files`
   (default 5000) or `budget_seconds` (default `WATCH_SCAN_BUDGET_SECONDS = 0.25`).
3. **The ignore list must exclude the run output tree (`.orchestrator/`).** Without it a watch on
   a broad glob is triggered by the runs it itself produced — a self-sustaining fire loop that
   spends money. A dedicated test writes into `.orchestrator/` and asserts no fire.
4. `FileWatchScheduler(Scheduler)` implements `next_fire(trigger, now)` per HLD §6.7:
   - a truncated scan returns `None` and emits `schedule.watch_budget_exceeded` — **a partial
     scan never fires**;
   - no changed paths ⇒ save the digest, return `None`;
   - changed paths whose newest mtime is within `debounce_seconds` of `now` ⇒ return `None` and
     **do not save the digest** (the change is still settling; saving it would swallow the event);
   - otherwise save the digest and return the newest changed mtime, truncated to whole seconds.
5. `watch.on` supports `created` and `modified` in MVP: `created` = a path absent from the prior
   digest; `modified` = present with a different `(mtime_ns, size)`. `deleted` is rejected at
   load time by `T-Sd1Kq7` and is a documented non-goal.
6. `EventScheduler` keeps its exact current behavior and its existing tests pass unmodified; its
   docstring is updated to name `FileWatchScheduler` as the successor and to explain that a
   sentinel path is a degenerate one-file watch.
7. The engine's per-tick budget is respected: a file-watch scan that would exceed it yields
   rather than blocking, and `T-Ev3Qm5` AC20's budget test still passes with a watch schedule
   present.

### Tests
8. Digest/scan: created vs modified detection; a file touched twice inside the debounce window
   fires once, after the window; `max_files` truncation returns `truncated=True` and no fire;
   `budget_seconds` truncation likewise (inject a `monotonic` that jumps).
9. Idempotency: after a fire, a `--once` tick with no further change produces no fire; simulate
   a crash between the fire and the digest save and assert the re-derived `fire_key` is already
   burned (no double fire) — this is the payoff of the mtime-derived `scheduled_for` and must be
   asserted, not assumed.
10. Security: a glob resolving outside the workspace root, and a symlink pointing outside it,
    are both skipped with a warning and never scanned.
11. AC3's self-trigger test.
12. E2E via `ao schedule daemon --once`: add a `file_watch` binding, touch the watched file,
    wait past the debounce with a fixed clock, assert exactly one run directory appears; a second
    `--once` with no change asserts none.
13. `uv run pytest -q` green with the delta reported; ruff + format clean; mypy whole-tree count
    reported; coverage ≥80 % on `service/watch.py`.

## Risks
- **Self-triggering loops** are the failure mode that costs real money here. AC3 is not optional
  and its test must be explicit.
- Polling a broad glob on a large workspace is the other cost. `max_files`, the scan budget, and
  the ignore list bound it; a truncated scan deliberately fires nothing rather than firing on
  partial information.
- `mtime_ns` granularity varies by filesystem. Use `(mtime_ns, size)` together, as specified —
  size alone catches same-second overwrites that mtime misses on coarse-granularity mounts.
- The one-line edit to `T-Ev3Qm5`'s `SCHEDULERS` map is the only place these two tasks touch the
  same file. Coordinate if they overlap in time.

## Dependencies
- `T-Sd1Kq7` (`WatchSpec`), `T-Fr2Nx8` (fire store, for the idempotency assertion), `T-Ev3Qm5`
  (the engine and its scheduler map), `T-Cl6Jn9` (the `add` command this extends).

## Pseudocode / Algorithm
```text
See HLD §6.7 for the normative algorithm. The three orderings that must not be rearranged:
  1. truncated  -> return None  (never fire on a partial scan)
  2. debounce   -> return None WITHOUT saving the digest (saving swallows the event)
  3. save digest THEN return newest-changed-mtime (so a crash after the save cannot re-fire,
     and a crash before it re-derives the SAME fire_key, which the store has burned)
```

## Schemas / Interface Notes
- Interface / API: `service.watch.{WatchDigestStore, scan, DEFAULT_WATCH_IGNORES,
  WATCH_SCAN_BUDGET_SECONDS}`; `scheduler.FileWatchScheduler`.
- Spec / data schema: consumes `WatchSpec` (`paths`, `on`, `debounce_seconds`, `poll_seconds`,
  `max_files`); persists `<state_dir>/schedules/watch/<slug>/<id>.json`.
- Triggers / events: implements the `file_watch` kind; emits `watch.changed` and
  `schedule.watch_budget_exceeded`.
- Artifacts: reads workspace files matching the declared globs (read-only, never writes into the
  workspace).

## Handoff Boundary
- Upstream: `T-Sd1Kq7`, `T-Fr2Nx8`, `T-Ev3Qm5`, `T-Cl6Jn9`; HLD §6.7/§7.1; ADR-0014 D7.
- Downstream: `T-Te3Qw8` (e2e tier), `T-Se4Bk5` (reviews the glob/symlink path handling),
  `T-Ap1Xs3` (renders the `file_watch` kind in the panel).

## Artifacts
- Docs/comments: `meta/tickets/E-Sc9Rt4-scheduler-triggers/T-Fw8Gp4-file-watch-trigger/`
- Large outputs: N/A
