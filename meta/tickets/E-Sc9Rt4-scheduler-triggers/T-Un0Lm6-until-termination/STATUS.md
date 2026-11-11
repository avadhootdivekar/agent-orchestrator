# STATUS

- ID: `T-Un0Lm6-until-termination`
- Updated At: 2026-11-11
- State: **Done** (re-review w28-02, 2026-11-11: M1/W1/W2/m1/m4 fixed and verified; m2/m3 open, non-blocking; see REVIEW.md "Re-review")
- Owner: developer agent

## Re-review 2026-11-11 (By: reviewer (Opus) · Role: reviewer · unit `w28-02-a1-until-rereview` · review only, no src changes)
- Independent re-review of `ed84cb4` from a clean worktree (HEAD `44cdf92`): `pytest tests/service tests/schedules tests/ui` **2224 passed, 2 skipped**; ruff/format clean; `mypy src` clean (233 files); 4 mutations caught; in-process probe confirms the dashboard Enable path queues the restart and the next engine tick applies it.
- Verdict: **Done.** AC4 met with the service running. m2 (AC6 wording) and m3 (hardened gate reader) are **not blocking**.
- Follow-ups (minor, not gating): n1 dashboard response does not say restart is queued; n2 restart request lost/expired on corrupt-registry ticks or a save `OSError`; n3 three duplicated request-drain loops; n4 scheduler HLD §6.2/§6.8/§6.9 do not mention `restart_completed`; m2, m3, s1.

## Fixes 2026-11-11 (By: developer · Role: developer · unit `w27-01-a1-until-restart-fix`)
- **M1 fixed:** new `restart_completed` request kind. `ao schedule enable` / dashboard Enable on a `completed` schedule call `schedule_engine.restart_completed_schedule`: if it can take `engine.lock` (no engine) it resets state directly; otherwise it drops `requests/<uuid>.json` and the running engine applies it at the start of its next tick (`drain_restart_requests` -> `apply_restart`: `runs_count=0`, `queued_*` cleared (m4), status `active`). TTL/invalid handling as run-now. The CLI says "restart queued"; the dashboard no longer returns 409 "Retry in a moment". The duplicated locked reset in CLI/UI is folded into the one helper.
- **W1 fixed:** `until_condition(..., unreadable_seen=)`; the engine keeps last error per schedule, so `schedule.until_unreadable` emits once per distinct error (a clean/absent-gate read forgets it). Without the map (library callers/tests) behaviour is unchanged.
- **W2 fixed (wording):** `max_runs` counts launched runs (`runs_count`); docstring, TASK.md Risks, governance HLD §D-2 row. No launch-failure quarantine added (separate ticket).
- **m1 fixed:** `_until_equals` uses `-?[0-9]+` fullmatch; `--5` / `²` stay strings. Guide gets one sentence on typing + restart.
- Tests (`tests/service/test_until.py`): in-process `engine.start()` + restart request -> next `tick()` applies it and fires (queued key cleared); direct path without engine; no-op for non-completed; expired/invalid discarded; CLI enable with engine lock held queues; W1 dedup (+ unseen-map still emits); m1 params. Old "refuses / 409" tests in `test_cli_e2e.py` / `test_schedules_api.py` rewritten to assert queueing. Mutation-checked: removing the drain call and removing the dedup each fail a test.
- Evidence: `pytest tests/service tests/schedules tests/ui`: **2224 passed, 2 skipped**; `ruff check .`, `ruff format --check .` clean; `mypy src` clean (233 files).
- **Still open:** m2 (AC6 text vs HLD — owner/manager call), m3 (gate read via `read_regular_file`), s1 docstring note; independent re-review needed before Done.

## Sync 2026-11-11 (w27-02, developer; docs only; HEAD `c4e3871`)
- Supersedes the w24-07 "awaiting review" line below: review recorded in `REVIEW.md` (`c4e3871`), verdict Not Done.
- **Unfinished:** M1 (restart of a `completed` loop impossible while the service runs; fix via an engine-applied restart request, or document "stop the service"), W1 (`until_unreadable` not de-duplicated), W2 (`max_runs` counts launches), minors m1-m4. No fix has landed; ACs 1-14 remain implemented.

## Reconciliation 2026-11-11 (By: developer · Role: developer · unit `w24-07-a1-task-status-sync` · docs only)
- Checked against branch `ad/enhancements-10nov` at HEAD `1d5c6f9`. Any older line below that says "not started", "uncommitted" or "in the working tree" is superseded by this block where they disagree.
- State (history, w24-07 snapshot; superseded by the header State on line 5): **Implemented — awaiting review (all 14 ACs met, no independent review recorded)**
- ACs 1-14 met per the task record: AC13 (LoopSpec contrast) and AC14 (coverage 100 % statements/0 partial branches of the until code at `76ca870`) landed in w18-03/w19-03. The `## Unfinished` list in this file is superseded.
- **Why not Done**: no review pass of `until` is recorded (only the T-Se4Bk5 gate touched it indirectly).
- Deviation: termination is recorded in `ScheduleState` + event log; no suppressed fire record is written (no instant is burned).

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

## Review 2026-11-11 (By: reviewer · unit `w26-07-a1-until-review` · review only, no src changes)
- Independent Opus review recorded in `REVIEW.md` (HEAD `57c8de4`, clean worktree; service+schedules 1352 passed, mypy clean).
- Verdict: **Not Done.** M1 (major): `ao schedule enable` / dashboard Enable cannot restart a `completed` schedule while the engine runs (engine holds `engine.lock` for its lifetime; same-process flock also conflicts) — AC4 works only with the service stopped.
- Should fix: W1 `until_unreadable` emitted every 15 s eval (no de-dup); W2 `max_runs` counts launches, not fires (wording). Minor: m1 `--until-equals=--5` uncaught ValueError; m2 AC6 text vs HLD; m3 gate read not via `read_regular_file`; m4 stale `queued_*` after completion.
- (History, w26-07 review; superseded by the header State on line 5) State stayed **Implemented — awaiting fixes (M1)**.
