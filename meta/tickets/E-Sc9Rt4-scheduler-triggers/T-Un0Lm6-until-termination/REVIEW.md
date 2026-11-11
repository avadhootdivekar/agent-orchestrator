# REVIEW: T-Un0Lm6-until-termination

- Reviewer: reviewer agent (Opus) · unit `w26-07-a1-until-review` · 2026-11-11
- Scope: branch `ad/enhancements-10nov` at HEAD `57c8de4`, read from a clean detached worktree (other
  units' uncommitted edits to `schedule_engine.py`/`fire_store.py` excluded). Reviewed against TASK.md
  ACs 1-14 and `docs-md/scheduler-triggers-hld.md` §6.8.
  - `src/agent_orchestrator/service/schedule_engine.py`: `evaluate` until block (~L936-950),
    `UNTIL_*` constants, `EventSink`, `_gate_matches`, `until_condition`, `until_satisfied` (~L1735-1812)
  - `src/agent_orchestrator/schedules/cli.py`: `_until_equals`, `_until` (~L906-944), `add` flags
    (~L1045-1152), `list` RUNS column (~L394-433), `enable_cmd` restart (~L1563-1598)
  - Related, read for AC4: `ui/service.py::set_schedule_enabled/_restart_completed` (~L1403-1456),
    `ScheduleEngine.start/_acquire_lock` (~L432-480), `fire_store.ScheduleStateStore.record_fire/set_status`
  - Tests: `tests/service/test_until.py`

## Summary

The `until` core is small, well-placed and matches HLD §6.8: correct ordering (`max_runs` ->
`artifact_exists` -> gate), one call site after the status check and before the queued drain, a
satisfied schedule becomes `completed` with `disabled_reason="until:<cond>"` and the binding is
untouched; bad reads never terminate; the bool/int and float edge cases are handled; paths go through
`LocalFsArtifactStore.resolve`. Tests are thorough and mutation-checked. **One major gap:** the AC4
restart path (`ao schedule enable` / dashboard Enable on a `completed` schedule) **cannot succeed while
the schedule engine is running**, which is the normal `ao service` deployment — so a finished `until`
loop can only be restarted by stopping the service. The rest are minor.

## Evidence (observed, this review)

- `uv run --extra dev --extra ui pytest tests/service tests/schedules -q` at clean HEAD: **1352 passed**, 0 failed.
- `tests/service/test_until.py`: 45 passed.
- `ruff check` + `ruff format --check` on the three files: clean. `mypy src` (with `ui` extra): **Success, 233 files**
  (without the `ui` extra, 29 import errors in auth/ui/webhook modules — environment only, not this task).
- Throwaway probe tests (in the temp worktree, deleted afterwards):
  1. state `completed` + engine lock held (as by a running daemon) -> `ao schedule enable loop` exits 1:
     "a daemon is evaluating; cannot reset a completed schedule's state safely. Stop the daemon ...".
  2. `ScheduleEngine.start()` then a second `open()+flock(LOCK_EX|LOCK_NB)` **in the same process**
     raises `OSError` (flock locks are per open-file-description) -> the dashboard's
     `_restart_completed`, which runs inside the service process that owns the engine, can never succeed.
  3. `ao schedule add ... --until-gate g.json --until-equals=--5` and `--until-equals=²` -> uncaught
     `ValueError: invalid literal for int()` (traceback, exit 1) instead of a usage error.
  4. completed -> manual reset -> evaluate 3 days later: fires once as catch-up (`reason=due`), the stale
     `queued_fire_key` from before completion is still in state (not drained on that tick).

## Critical (block merge)

None.

## Major (blocks marking the task Done)

**M1 — A completed `until` loop cannot be restarted while the service runs (AC4 only works with the daemon stopped).**
- Location: `schedules/cli.py::enable_cmd` (~L1575-1588), `ui/service.py::_restart_completed` (~L1435-1455);
  lock taken for the engine's lifetime in `ScheduleEngine.start -> _acquire_lock` (`schedule_engine.py` ~L432, ~L462).
- Observation: the reset of `runs_count`/`status` is only allowed while holding `engine.lock`
  non-blocking. A running engine holds that lock for its whole lifetime, so the CLI always refuses
  ("Stop the daemon"), and the dashboard (same process as the engine) always refuses with
  "Retry in a moment", which is misleading — retrying never helps. AC12's e2e test passes only because
  it uses `daemon --once` (lock released between ticks).
- Why it matters: AC4 calls `enable` "the documented way to restart an `until` loop"; in the always-on
  deployment it is not usable, and neither `guide-scheduled-runs.md` nor HLD §6.8 says the service must
  be stopped. The single-writer rationale (F9) is right — the fix must go through the engine, not around it.
- Next step: route the restart through the engine, like run-now's drop directory (HLD §6.9): `enable`
  on a completed schedule writes a restart request the engine applies at the start of its next tick
  (reset `runs_count`, `status=active`, clear `queued_*`), and falls back to the direct locked write when
  no engine runs (`probe_engine_lock`). Test: engine `start()`ed in-process + CLI/dashboard enable ->
  next tick fires. Minimum alternative if the owner prefers: document "stop the service to restart a
  completed schedule" in the guide and HLD §6.8 and fix the dashboard message.

## Warnings (should fix)

**W1 — `schedule.until_unreadable` is emitted on every evaluation, with no de-duplication.**
- Location: `schedule_engine.py::until_condition` except-branch (~L1789-1798); called every eval
  (`DEFAULT_EVAL_INTERVAL_SECONDS = 15`), before the not-due checks.
- Why: a persistently malformed gate writes a WARNING ~5 760 times/day per schedule into the size-capped
  event log (`EVENT_LOG_MAX_BYTES`), evicting the history `ao schedule history` relies on.
- Next step: emit only on a change of error (keep last error per schedule in memory or in
  `ScheduleState`), or only when the schedule is otherwise due.

**W2 — `max_runs` counts successful launches, not fires; docstring/ticket say "fires".**
- Location: `fire_store.ScheduleStateStore.record_fire` (sole `runs_count += 1`); `until_condition` docstring
  ("counts *fires*, not successes"); TASK.md Risks.
- Observation: burned/missed instants, governance skips and **launch failures** do not count. A schedule
  whose launch always fails (e.g. a deleted workflow file) never reaches `max_runs`; quarantine only covers
  eval errors (`_quarantine_on_repeat`), so it retries every instant indefinitely. Spend is bounded (no run
  starts), so this is not a cost hole, but "no `until` schedule may loop forever" (AC8) is only true for
  launched runs.
- Next step: fix the wording to "counts launched runs (whatever their outcome)"; optionally add a
  launch-failure quarantine threshold (separate ticket — not this task's scope).

## Minor / Suggestions

- **m1 — `--until-equals` crashes on `--5`, `²`, etc.** `schedules/cli.py::_until_equals` (~L906-915):
  `lstrip("-").isdigit()` accepts strings `int()` rejects. Use `re.fullmatch(r"-?[0-9]+", s)` or
  try/except -> return the raw string; add the two cases to `test_until_equals_typing`.
  Related: a gate string that *looks* like a bool/int (`"true"`, `"3"`) cannot be expressed from the CLI
  (YAML still can) — worth one sentence in the guide.
- **m2 — AC6 text vs HLD/impl.** AC6 says missing file / field absent / wrong type also emit
  `until_unreadable` at WARNING; the HLD pseudocode and the code treat those as silent "not done" and emit
  only for unreadable/malformed/undecodable/path-escape. The implemented behaviour is the better one
  (a missing gate is the normal "not done yet" state), and STATUS.md records it, but the ticket AC text
  was never reconciled. Next step: amend AC6 wording (owner/manager call — not changed by this review).
- **m3 — Gate read does not use the hardened reader.** `until_condition` does `resolve()` then
  `is_file()` + `read_text()` (follows symlinks, unbounded size, TOCTOU between check and open), while
  T-Se4Bk5 R-3 made `governance_gate.read_regular_file` (O_NOFOLLOW, fstat, size cap) public for exactly
  this pattern. Low risk (workspace is operator-owned; escape at check time is caught), but reusing it is
  DRY and bounds a huge gate file read every 15 s.
- **m4 — Completion leaves `queued_fire_key`/`queued_*` in state** (asserted deliberately by
  `test_satisfied_schedule_does_not_drain_queued_fire`). After a restart the stale key can drain later.
  If M1 is fixed via an engine-side restart, clear `queued_*` there; otherwise document.
- **s1 —** `until_satisfied` (bool) is now only used by tests; `evaluate` uses `until_condition`. Fine to keep
  as the HLD-named API, just note it in the docstring.

## Dimensions checked

- SOLID/KISS: helper is a free function with a narrow `EventSink` protocol — good; single call site. OK.
- DRY: m3 (hardened reader). The CLI and dashboard duplicate the locked reset (~12 lines each) — fold into
  one helper when fixing M1.
- Magic literals: `UNTIL_*` names, `DEFAULT_UNTIL_MAX_RUNS`, `STATUS_COMPLETED`; the `"until:"` reason prefix
  is inline (minor). OK.
- Pluggability: paths through `LocalFsArtifactStore` (concrete, constructed per call) — acceptable for a
  local-fs gate; inject if a remote store is ever needed. OK.
- Spec correctness: `UntilSpec` (`ge=1`, workspace-relative validator) from T-Sd1Kq7. OK.
- Determinism/resume: pure w.r.t. clock; state mutation is persisted before returning; restart semantics M1/m4.
- Errors/logging: bad reads → one WARNING event (W1 = frequency); no swallowing of unexpected exceptions
  (only `OSError`/`ValueError`/`ArtifactPathError`). OK.
- Testability: fixed clocks, fake sinks, mutation-checked. Gap: no test with a *running* engine + enable (M1).
- Concurrency/rollout: single-writer rule respected (that is what causes M1); `until` field is expand-only. OK.

## AC verdicts

| AC | Verdict | Note |
|---|---|---|
| 1 order | met | `until_condition` L1778-1801 |
| 2 call site | met | after status/enabled, before drain/next_fire |
| 3 completed + event, binding kept | met | event has `runs_count` + `condition` |
| 4 enable resets | **partial** | works only with no engine running (M1) |
| 5 root guard | met | `store.resolve`/`store.exists`; symlink escape → no-terminate (+event) |
| 6 bad read never terminates | met (behaviour) / text mismatch | m2 |
| 7 gate_equals typing | met | bool/int guard, float/object/null unequal |
| 8 default max_runs | met | m1 crash on odd `--until-equals` input |
| 9 list status + RUNS | met | |
| 10-13 tests | met | AC12 via `daemon --once` only |
| 14 green/lint/mypy/coverage | met | 1352 passed; mypy clean; 100 % of until code per w19-03 |

## Done / not Done

**Not Done.** The core `until` logic is correct and could be merged as-is, but AC4's restart is not
usable under the always-on service (M1). Remaining before Done:
1. M1 — engine-routed restart (or an owner decision to accept + document the "stop the service" limitation).
2. W1, W2 wording, m1 — small fixes, can land with M1.
3. m2 — reconcile AC6 text with the HLD (owner/manager).

## Resolution 2026-11-11 (By: developer · unit `w27-01-a1-until-restart-fix`)
- M1 fixed (engine-applied `restart_completed` request; direct locked write only when no engine runs; test uses in-process `engine.start()`). W1 fixed (dedup per schedule on error change). W2 wording fixed. m1 fixed. m4 fixed as part of M1 (`queued_*` cleared on restart).
- Open: m2 (owner/manager), m3, s1. Not re-reviewed independently — the Done call stays with the reviewer.
