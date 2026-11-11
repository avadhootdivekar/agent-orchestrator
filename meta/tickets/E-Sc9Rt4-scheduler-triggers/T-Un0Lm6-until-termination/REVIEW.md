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

## Re-review 2026-11-11 (By: reviewer (Opus) · Role: reviewer · unit `w28-02-a1-until-rereview`)

- Scope: fix commit `ed84cb4` (M1/W1/W2/m1/m4), read and tested from a clean detached worktree at HEAD
  `44cdf92`. No src/test changes landed between `ed84cb4` and `e913b63`, so the result applies to the
  current branch tip. Files: `service/schedule_engine.py` (`drain_restart_requests`, `apply_restart`,
  `restart_completed_schedule`, `until_condition` dedup, tick wiring), `schedules/cli.py` (`enable_cmd`,
  `_until_equals`), `ui/service.py::set_schedule_enabled`, `tests/service/test_until.py`,
  `tests/schedules/test_cli_e2e.py`, `tests/ui/test_schedules_api.py`, `docs-md/guide-scheduled-runs.md`,
  `docs-md/scheduled-run-governance-hld.md`. Read for context: `fire_store.ScheduleStateStore`,
  `drain_run_now_requests`/`drain_resume_requests`, `ScheduleEngine.start/_acquire_lock`, `service/cli.py`
  (`--no-schedules` builds no engine, so it takes the direct path).

### Evidence (observed, this re-review)
- `uv run --extra dev --extra ui pytest tests/service tests/schedules tests/ui -q`: **2224 passed, 2 skipped**, 0 failed (121 s).
- `ruff check` on the touched files: clean. `ruff format --check src tests`: 675 files clean. `mypy src`: **no issues, 233 files**.
- Mutation checks (each reverted afterwards; run over `test_until.py` + `test_cli_e2e.py` + `test_schedules_api.py`):
  removing the `drain_restart_requests` call in the tick: 1 failed · removing the `not repeated` dedup guard: 1 failed ·
  removing `clear_queued` in `apply_restart` (m4): 1 failed · removing the `runs_count = 0` reset: 5 failed.
- Throwaway probes (temp worktree, deleted):
  - **P1, the original M1 scenario for the dashboard:** engine `start()`ed in-process, then `DashboardService.set_schedule_enabled("s", True)`
    in the same process. Result: 200 with `restarted: true`, one `restart_completed` request file, state still `completed`. Next
    `tick()`: status `active`, the schedule fires in that tick, `runs_count == 1`. Before the fix this path always raised (same-process flock conflict).
  - P2: two enables while the engine runs leave two requests. One tick applies the first; the second is a no-op (status already
    `active`); on-disk `state.json` matches the engine cache.
  - P3: a tick whose registry load fails does not drain the request. It stays on disk and is applied on a later good tick, or
    expires after `REQUEST_TTL_SECONDS` with `schedule.request_expired` (see n2).

### Prior findings: status
| ID | Status | Verification |
|---|---|---|
| M1 restart blocked while engine runs | **Fixed** | CLI and dashboard both call `restart_completed_schedule`. With `engine.lock` free it writes state directly under the lock; with the lock held it drops a `kind: restart_completed` request, which the engine (the single writer, F9 kept) applies at the start of its next tick. Run-now's drain skips foreign kinds (`kind != run_now -> continue`) and resume's drain skips non-`resume_capped`, so the three drains coexist. Tests use an in-process `engine.start()`, which is the strict case. P1 covers the dashboard. |
| W1 `until_unreadable` spam | **Fixed** | One emit per distinct `str(exc)` per `(resolved root, id)`. A clean read or an absent gate forgets it, and so does a restart. The dedup map is in memory, so each `daemon --once` invocation or service restart emits once more. That is acceptable. |
| W2 `max_runs` wording | **Fixed** | The docstring, TASK.md Risks and governance HLD §D-2 row now say "launched runs". A launch-failure quarantine is not added (out of scope, as recommended). |
| m1 `--until-equals` crash | **Fixed** | `-?[0-9]+` fullmatch. `--5` and `²` stay strings (parametrized test). The guide explains the typing rule. |
| m2 AC6 text vs HLD/impl | Open, **non-blocking** | The behaviour is the better one: a missing gate means "not done yet" and stays silent. It is recorded in STATUS.md. Only the AC wording is out of date. It needs an owner/manager edit of the AC text, not a code change. |
| m3 gate read not via `read_regular_file` | Open, **non-blocking** | Low risk: the workspace is operator-owned and path escape is caught at resolve time. It is a hardening/DRY follow-up (bounded size, O_NOFOLLOW). |
| m4 stale `queued_*` after completion | **Fixed** | `apply_restart` clears `queued_*`. Asserted by a test and by mutation. |
| s1 `until_satisfied` only used by tests | Open | Suggestion only. |

### New findings (none Critical, none Major)
- **n1 (minor): the dashboard cannot tell "restarted" from "restart queued".** `ui/service.py::set_schedule_enabled`
  ignores the bool returned by `restart_completed_schedule` and always returns `restarted: true`. The CLI does distinguish
  ("restart queued ..." vs "restarted"). The UI (`ui/src/types.ts`) does not display `restarted` today, so nothing misleads a
  user yet, but the API contract hides the 0-15 s window in which the row still shows `completed`. Next step: return
  `restart_queued: not applied` in the response and the `schedule.enabled` event detail. Small follow-up, also relevant to `T-Ap1Xs3` W-1.
- **n2 (minor): edge cases of the queued restart.** (a) `drain_restart_requests` runs inside `_evaluate_all` only after the
  registry loads, so a run of corrupt-registry ticks lasting more than `REQUEST_TTL_SECONDS` (300 s) expires the request. The
  binding is then `enabled: true` but still `completed`, and the only signal is a `schedule.request_expired` event. (b) Its
  docstring says "Never raises", but an `OSError` from `apply_restart -> _save` propagates. The tick catches it
  (`schedule.eval_failed`), but the request was already unlinked, so that restart is lost, and the rest of that tick's run-now
  and evaluation is skipped. Both are rare and fail safe: no spend, the state stays `completed`, and re-running enable fixes it.
  Next step: wrap each request in `try/except OSError` with an event, matching run-now.
- **n3 (minor, DRY): three near-identical request-drain loops.** `drain_run_now_requests`, `drain_resume_requests` and
  `drain_restart_requests` repeat the same glob, parse, kind filter, delete-first, TTL check and `request_expired` steps (~25
  lines each). Next step: one `_iter_requests(kind, now)` generator in `schedule_engine.py` that yields valid unexpired
  payloads. Not required for Done.
- **n4 (minor, docs): the scheduler HLD does not mention the queued restart.** `docs-md/scheduler-triggers-hld.md` §6.8
  (~L772) still says only "`ao schedule enable <id>` resets it", and the §6.2 tick pseudocode (~L453) and §6.9 request kinds do
  not list `drain_restart_requests` / `kind: restart_completed`. The operator guide is correct. Not fixed here: that file has
  another unit's uncommitted edits in the shared tree. Hand it to `T-Dc6Zr2` or the next HLD sync.
- Pre-existing, not introduced by this fix: the direct path holds `engine.lock` for a few milliseconds. A service engine
  starting at exactly that moment fails with `EngineLockHeldError`. The old code took the same lock the same way.

### Dimensions checked
SOLID/KISS: one helper (`restart_completed_schedule`) replaces the two duplicated locked resets in the CLI and dashboard, and
`apply_restart` is the single mutation used by both paths. Good. DRY: n3. Magic literals: `REQUEST_KIND_RESTART_COMPLETED` and
`STATUS_ACTIVE` are named; the `"until:"` reason prefix is still inline (minor, carried over). Pluggability: unchanged. Spec/DAG:
n/a. Determinism/resume: the request carries `now` from the caller's injected clock; TTL is compared against the engine clock;
delete-first gives at-most-once. Errors/logging: n2(b). Testability: in-process engine start, fixed clocks, mutation-checked.
Concurrency: F9 single-writer kept; probe P2 shows duplicate requests are idempotent.

### Verdict
**T-Un0Lm6 may be marked Done.** M1 is fixed and verified, including the dashboard path. W1, W2, m1 and m4 are fixed. AC4 is now
**met** with the service running. All other AC verdicts from the first review stand. **m2 and m3 are not blocking**: m2 is AC
wording only (owner/manager call) and m3 is a hardening follow-up. n1-n4 are minor follow-ups and should go in tickets, not gate Done.
