# STATUS

- ID: `T-Un0Lm6-until-termination`
- Updated At: 2026-11-11
- State: In Review (AC13 + coverage report unfinished)
- Owner: developer agent

## This update
- By: developer · Role: developer · Date: 2026-11-11 (unit `w17-02-a1-until-termination`)
- `service/schedule_engine.py`: `until_condition` (returns the satisfied condition name) +
  `until_satisfied` (bool), `EventSink` protocol, `DEFAULT_UNTIL_MAX_RUNS = 100`; one call site in
  `evaluate` after the status/enabled check and before the queued drain. Satisfaction sets
  `status=completed` (`disabled_reason="until:<condition>"`), emits `schedule.until_satisfied`
  (`runs_count`, `condition`); binding untouched. Order: `max_runs` -> `artifact_exists` -> gate.
- Bad gate reads (missing field/malformed/undecodable/unreadable/symlink escape) return no-terminate;
  malformed/unreadable emit `schedule.until_unreadable` at WARNING; a missing file is "not done".
  `gate_equals`: float/object/null never equal; bool never equals a non-bool.
- `schedules/cli.py`: `add --until-artifact/--until-gate/--until-field/--until-equals/--max-runs`
  (default `max_runs=100` announced on stdout); `list` gains `runs_count` / RUNS column. (`enable`
  restart + `runs_count` reset already existed.) Deviation: `schedules/cli.py` is not in the brief's
  `touches` but the ticket assigns these flags to this task; only these hunks were committed
  (another unit had uncommitted edits in the same file, left unstaged).

## Evidence
- `uv run pytest tests/service/test_until.py -q`: 40 passed. `tests/service tests/schedules`: 992
  passed, 1 failed -- a different test each run in `tests/schedules/test_cli_e2e.py`
  (`test_engine_wiring_matches_service_policy` / `..._fails_closed_on_unreadable_registry`), in
  another unit's uncommitted kill-switch work; not caused by this task.
- ruff check/format clean on own paths; `mypy src` clean (231 files).
- Mutation checks (each made the named test fail, then restored): max_runs `>=`->`>`; bool guard;
  float guard; unreadable terminates; no `completed` status; call site disabled; artifact check
  removed; `enable` not resetting `runs_count`; no default `max_runs`.
- Coverage of `schedule_engine.py` from this test file alone is 61% (module-wide); the new
  `until` code is fully exercised.

## Unfinished
- AC13: LoopSpec-under-`until` characterization test (needs a running engine with a loop workflow).
- AC14: whole-tree pytest/mypy-tests delta and a coverage figure scoped to the new lines.
- Brief asked to record skip/termination "in fire store": termination is recorded in
  `ScheduleState` (status/reason) and the event log; no suppressed fire record is written since no
  instant is being burned.

## Next actions
1. Add AC13 test; hand `completed`/`runs_count` display contract to `T-Ap1Xs3`; docs to `T-Dc6Zr2`.
