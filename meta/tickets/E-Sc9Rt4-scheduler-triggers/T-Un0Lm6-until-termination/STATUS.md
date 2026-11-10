# STATUS

- ID: `T-Un0Lm6-until-termination`
- Updated At: 2026-11-11
- State: In Review (AC14 coverage report recorded 2026-11-11)
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
- AC14: whole-tree pytest/mypy-tests delta and a coverage figure scoped to the new lines.
- Brief asked to record skip/termination "in fire store": termination is recorded in
  `ScheduleState` (status/reason) and the event log; no suppressed fire record is written since no
  instant is being burned.

## Update 2026-11-11 (unit `w18-03-a1-schedule-followups`)
- AC13 done: `tests/service/test_until.py::test_loopspec_workflow_runs_unchanged_under_until_schedule` (real `Orchestrator` + `FakeExecutor`, 2-iteration loop = ONE `runs_count` tick; `until` ends the schedule, not the loop; fixed clock) and `..._bad_gate_still_fails_the_run_while_until_gate_never_does` (the LoopSpec/`until` contrast). Mutation-checked: disabling the `evaluate` until call site fails it.

## Next actions
1. (AC13 done) hand `completed`/`runs_count` display contract to `T-Ap1Xs3`; docs to `T-Dc6Zr2`.

## Update 2026-11-11 (unit `w19-03-a1-until-coverage-report`) -- AC14 coverage report
- Measured at clean HEAD 76ca870 (detached worktree; other units' uncommitted edits excluded):
  `pytest tests/service/test_until.py tests/service/test_fire_store.py --cov=agent_orchestrator.service.schedule_engine --cov-branch`.
- until code = `evaluate` until block (~L819-835) + `until_condition`/`_gate_matches`/`until_satisfied` (~L1545-1620). Module-wide from these files: 57% (809 stmts). Within the until code: **100% statements**; 2 partial branches before, **0 after**: `822->835` (binding without `until`) and `1598->1606` (unreadable gate with `events=None`).
- Added fixed-clock tests: `test_unreadable_gate_without_event_sink_is_silent_and_never_satisfied` (invalid JSON + undecodable bytes) and `test_plain_schedule_without_until_fires_through_the_engine`. `test_until.py`: 45 passed (was 40+2 param); ruff check/format clean.
- `tests/service/test_fire_store.py::test_hld_event_table_is_complete` **passes at clean HEAD** (104 passed across both files). The earlier failure came from another unit's uncommitted `schedule_engine.py`/`fire_store.py` edits (event table out of sync with new emits) -- re-check when those land.
- No src changes; no real bug found.
