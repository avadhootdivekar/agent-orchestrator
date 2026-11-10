# TASK: T-Rk4Wn7-stuck-kill-retry-loop-and-run-stranding

## Metadata
- Task ID: `T-Rk4Wn7-stuck-kill-retry-loop-and-run-stranding`
- Epic ID: `E-Sn7tL2-sentinel-long-running-overseer`
- Type: BUG (found in a live overseer-runner run, 2026-11-10)
- Owner: `TODO`
- Created: `2026-11-10`
- Last Updated: `2026-11-10`
- Status: `Draft`
- Estimate: `< 3 days`

## Requirements Mapping
- Requirement IDs: Sentinel recovery ladder (HLD §6 / T-Sn4Rec), systemic alarm, LLM lane (T-Sn8Gen / T-Sn9Pow).

## Description
A live run with the sentinel enabled was failed by the sentinel's own mechanical recovery. Four defects,
in descending severity (all observed in one run; evidence below):

**D1 — Retry does not reset the stuck clock (retry loop).** After a `stuck_factor` kill, the recovery ladder
re-dispatches the task, but `TaskRunState.started_at` keeps the ORIGINAL first-attempt time and `attempts`
stays 1. `sample_liveness` recomputes `running_seconds = now - started_at`, so the freshly retried task
immediately exceeds `stuck_factor * median(peers)` again and is killed on the next tick (~15 s). Three
kills in 32 s exhausted the ladder (`retry` → `escalate` → `fail`) and tripped the systemic alarm, with the
task never getting a real second attempt. Escalation (`effort: high`) is also meaningless for a task that
cannot survive one tick.

**D2 — `stuck_factor` peer baseline is wrong for heterogeneous work.** `_peer_durations` compares against
completed tasks of the same *agent* (`tester`), not the same kind of work. A full Rust test sweep (legit 30+ min)
was judged against ~8 short Playwright/doc tasks (median ≈ 6 min → threshold ≈ 18 min vs stuck_min 20 min)
and killed at 1201 s while still producing output. `last_activity_at` was current (a stuck-kill fired even
though idle was not the trigger), i.e. activity does not veto a duration-based kill.

**D3 — A failed task strands independent ready work; run ends `failed` with pending tasks.** After `w02-02`
failed (exit 143), `w03-10` (depends_on only `ck-02`, which had SUCCEEDED) and `ck-03` were never dispatched
and `run.end status=failed` was emitted as soon as the last running task finished, leaving 2 tasks `pending`.
Wave 3 had more ready tasks than `max_parallel` (10); `w03-10` was still queued when the failure landed.
I did not find a fail-fast rule in `scheduler.py`; the exact cause is UNCONFIRMED — needs reproduction
(queued-but-ready task + a sibling failure). After `ao resume` with unchanged DAG, `w03-10` dispatched
immediately, so the DAG/inputs were fine; the stall was in dispatch.

**D4 — Sentinel LLM lane cannot write its outputs (permission mode `default`).** `ClaudeCliSentinelDriver`
spawns `claude -p` with `AgentSpec(executor="claude_cli", ...)` when `llm.agent` is unset. That argv carries no
`--dangerously-skip-permissions` (transcript `permissionMode: default`), so Write/Bash heredoc to
`report.md`, `memory-append.md`, `handoff.json` were refused. Generation g01 ended `sentinel_ungraceful`
(`handoff_error: missing`), cost $0.36, produced nothing. Workaround used: add an agent to `specs/agents.json`
with `--dangerously-skip-permissions` and set `sentinel.llm.agent` to it.
Minor: on the resumed run, g02's note `sentinel-g02-note-001` was rejected `CTL-SCHEMA` (not investigated).

## Acceptance Criteria
1. A re-dispatched (recovery-retried) task gets a fresh `started_at`/`running_seconds` (per-attempt clock) and
   cannot be stuck-killed within `stuck_min_minutes` of its own start. Test: kill → retry → no second kill for
   >= stuck_min_minutes with unchanged peers.
2. `stuck_factor` baseline uses a comparable cohort (same kind/brief tag, or an explicit per-task
   `expected_minutes` / `no_stuck_kill`), and is vetoed by recent output growth (activity within
   `idle_kill_minutes`). Document the heterogeneity limit in `docs-md/guide-sentinel.md`.
3. N kills of the SAME task inside a short window count as one systemic incident (or have a cool-down between
   kills of the same task); the ladder never burns all rungs inside a single poll interval.
4. A failed task fails only its dependents. Independent ready tasks continue to dispatch (queued ones included);
   a run with remaining runnable work does not end `failed`/strand `pending` tasks. Regression test with
   `max_parallel < ready tasks` and a failing sibling.
5. The LLM lane driver default must be able to write its own generation directory (spawn with the
   permission setting the dispatched agents use, or an allow-list for `gNN/`), and `ao validate` / a startup
   check warns when LLM powers are `act` but the spawned session cannot write outputs. A failed generation due
   to permission denial surfaces as a distinct alarm, not just `sentinel_ungraceful`.
6. Investigate/describe the `CTL-SCHEMA` rejection of an LLM-authored `note`.

## Risks
- Changing the peer baseline changes kill behaviour for existing runs; gate behind sentinel settings, keep defaults safe.
- D3 may be a general scheduler issue, not sentinel-only; verify before scoping.

## Dependencies
- `T-Sn4Rec-liveness-kill-recovery-ladder`, `T-Sn8Gen-llm-generations-pack-handoff`, scheduler/engine dispatch.

## Pseudocode / Algorithm
```text
# D1 sketch: on recovery re-dispatch -> ts.started_at = now; ts.running_seconds = 0; ts.attempt_started_at = now
# D2 sketch: peers = completed tasks with same (agent, kind|tag); if ts.last_activity_at within idle window: no stuck kill
```

## Schemas / Interface Notes
- Interface / API: `sentinel_tick._trigger_for`, `sample_liveness`, `_peer_durations`, `_check_systemic`;
  `engine._start_llm_lane`; `sentinel_driver.ClaudeCliSentinelDriver`/`make_driver`.
- Spec / data schema (JSON/YAML): `sentinel.recovery.{stuck_factor,stuck_min_minutes,max_recovery_kills_per_task,systemic_kill_alarm}`, `sentinel.llm.agent`.
- Triggers / events: `sentinel.kill`, `sentinel.recovery.{retry,escalate,fail}`, `sentinel.alarm kind=systemic`.
- Artifacts: see Evidence below.

## Evidence (where to look)
Observed in workspace `/usr/avadhoot/mounted/ao-runner-finplan` (overseer-runner, `ao 0.1.0 (8bcacc0.dirty)`),
run `o-k6shov-ux-complete-500-20261110T113138Z` (workflow `o-k6shov-ux-complete-500`), 2026-11-10 UTC.
Config at the time: sentinel enabled + llm enabled (`powers: act`, `budget_pct: 10`), recovery defaults
(`idle_kill_minutes 15, stuck_factor 3.0, stuck_min_minutes 20, max_recovery_kills_per_task 2, systemic_kill_alarm 3`), `max_parallel 10`.

- Engine log (all events, kills at 12:23:59 / 12:24:15 / 12:24:31, alarm, `run.end failed` 12:59:11):
  `/usr/avadhoot/mounted/ao-runner-finplan/.orchestrator/ui/logs/launch-ux-complete-500.log`
- Run state (shows `w02-02` `started_at 12:03:58` with `attempts 1`, `recovery.kills 3`, `systemic_alarm`, `kill_log`):
  `/usr/avadhoot/mounted/ao-runner-finplan/.orchestrator/runs/o-k6shov-ux-complete-500-20261110T113138Z/{state.json,status.json,run.log}`
  (NOTE: `state.json` was edited by the operator before resume to clear the alarm; the ORIGINAL pre-edit copy is
  `/tmp/claude-1000/-usr-avadhoot-mounted-ao-runner-finplan/c8e61625-941a-4e8e-b993-b8263fb5b3e8/scratchpad/bak/state.json`;
  also `bak/workflow.json`, `bak/agents.json`, `bak/status.json`.)
- Failed task `w02-02-rust-sweep-verify-after-w01` attempt dirs: `.../runs/<run>/w02-02-rust-sweep-verify-after-w01/{attempt-1,cycle-2,cycle-3}`
- Sentinel lane: `.../runs/<run>/sentinel/{ledger.jsonl,g01/{transcript.jsonl,stderr.txt,triggers.jsonl,pack}}` (g01 = D4; transcript shows `permissionMode: default`)
- Workflow + prompt + wave outputs/manifests: `/usr/avadhoot/mounted/ao-runner-finplan/workflows/overseer-runner/runs/o-k6shov-ux-complete-500/`
  (`workflow.json`, `outputs/manifests/ck-02.json` shows `w03-10` `depends_on: [ck-02]` only; pre-edit snapshot `workflow.snapshot.1cb4d11021c8.json` in the run dir)
- Resume after fix (shows `w02-02` + `w03-10` dispatch at once, g02 note `CTL-SCHEMA`):
  `/usr/avadhoot/mounted/ao-runner-finplan/.orchestrator/ui/logs/resume-ux-complete-500.log`
- Code (clone paths; line numbers as of clone HEAD): `src/agent_orchestrator/sentinel_tick.py` `sample_liveness` ~522, `_peer_durations` ~541,
  `_trigger_for` ~553, `_check_systemic` ~695; `src/agent_orchestrator/engine.py` `_start_llm_lane` ~2908; `src/agent_orchestrator/sentinel_driver.py` `ClaudeCliSentinelDriver` ~102.

## Findings: CTL-SCHEMA rejection of g02 note (AC6; research unit w01-05, 2026-11-10)

**Evidence** (read-only, finplan run `o-k6shov-ux-complete-500-20261110T113138Z`, `control/inbox-rejected/`):
`sentinel-g02-note-001.reason.json` = `{"rule_id":"CTL-SCHEMA","detail":"created_at must be a non-empty string"}`.
The rejected file has `schema/id/actor/kind` correct, but uses **`"ts": "2026-11-10T14:47:00Z"` instead of `created_at`**, plus two
fields outside the envelope: top-level **`target: {task_id}`** and top-level **`reason`** (the note's task id belongs in
`payload`, the reason in `rationale`). `payload.text` itself was valid (≤4000 chars).

**Validator / rule:** `src/agent_orchestrator/sentinel_tick.py` `_parse_entry` (~L255-290): L276-278 require non-empty string
`kind`/`actor`/`created_at` (this is the check that fired); L286-288 reject unknown top-level fields (would have fired next:
`['reason','target','ts']`). `_Reject` is raised on the *first* failure only, so the sidecar hides the other violations.
Schema documented in `docs-md/sentinel-hld.md` ~L636-650 (`required: [schema,id,kind,actor,created_at]`, `additionalProperties: false`).
Rejection is counted by `_note_rejection` (~L386) toward `consecutive_rejections` (auto-pause of the LLM lane).

**Classification — instructions (pack contract) at fault; schema is correct; note content/format is the proximate symptom.**
- The schema is strict-by-design and consistent with the HLD; `created_at` is mandatory and the HLD/CLI (`control.py` L157) agree.
- The generated `pack/sentinel-contract.md` (built in `sentinel_gen.py` ~L319-345) and the driver prompt
  (`sentinel_driver.py` `render_prompt` L88-99) only say "one `ao.ctl/v1` JSON file per proposal ... id unique, `actor`=`sentinel-gNN`".
  They never list the envelope fields (`created_at`, `rationale`, `payload`), never give an example entry, and never state that
  unknown top-level fields are rejected. The model had to guess the envelope and guessed `ts`/`target`/`reason`.
  (Confirmed by grep of the g02 pack `sentinel-contract.md`: only the one-line mention.)
- Secondary: the rejection diagnostic reaches the LLM only via the quarantine sidecar, and only the first violation.

**Proposed fixes (followups; not coded here):**
1. Render a concrete `ao.ctl/v1` envelope + one `note` and one `kill` example (with `created_at`, `payload`, `rationale`) into
   `sentinel-contract.md` and mention "unknown top-level fields rejected" (`sentinel_gen.py` contract section; add a pack test
   asserting the contract contains `created_at`). Small and obviously correct.
2. Optional hardening: have `_parse_entry` collect all envelope violations into one `detail` (or at least name unknown fields first)
   so the sidecar/ledger shows everything wrong in one go.
3. Optional leniency decision (owner): accept `ts` as alias — not recommended; keeps the contract single-shape.
4. Consider not counting a CTL-SCHEMA rejection of a harmless `note` toward auto-pause, or surfacing the sidecar detail in the next pack.

## Handoff Boundary
- Upstream: operator RCA in the finplan workspace session (2026-11-10).
- Downstream: engine/sentinel developer; repro against a TEMP workspace (never this workspace's live runs).

## Artifacts
- Docs/comments: `meta/tickets/E-Sn7tL2-sentinel-long-running-overseer/T-Rk4Wn7-stuck-kill-retry-loop-and-run-stranding/`

## D4 implementation (unit w01-02-d4-llm-lane-perms, 2026-11-10)
- `sentinel_driver.py`: default spawn adds `--permission-mode acceptEdits --allowedTools Read,Glob,Grep,Write,Edit,Bash(mv:*)
  --add-dir <gNN> --add-dir <inbox>` (least privilege; no skip-permissions). Explicit `llm.agent` argv is left untouched.
- `lane_permission_warnings` -> `ao validate` (workflow `sentinel:` block) + engine startup warning (`sentinel.warn`).
- `DriverStatus.permission_denied` (from `permission_denials` in the stream) -> `GenerationLane._permission_alarm`: alarm
  `llm_permission_denied` in `control/sentinel-alarms.jsonl`, ledger `sentinel_permission_denied`, LLM lane paused.
- Tests: `tests/test_sentinel_lane_permissions.py` (6, fake driver / fake Popen). Docs: `guide-sentinel.md` "LLM lane permissions".
- Unverified: live `claude` honouring this exact flag set (no live LLM calls per brief) — smoke-test one real generation.

## D1 implementation (unit w01-01-d1-retry-clock, 2026-11-10)
- `TaskRunState.attempt_started_at` (new): set by `sentinel_tick.register_task` at every dispatch, including
  recovery re-dispatches; `sample_liveness` computes `running_seconds` from it (falls back to `started_at`).
  `started_at` still records the task's first start. Fixes AC1.
- Cool-down (AC3, rung-burning part): `evaluate_recovery` skips a task for `RETRY_COOLDOWN_SECONDS` (60 s) after
  a recovery re-dispatch, so state-based triggers (e.g. `cost_kill_pct`) cannot burn all rungs in one poll interval.
  Not done here: AC3's "N kills of the same task count as one systemic incident".
- Tests: `tests/test_sentinel_recovery.py::test_retried_task_gets_per_attempt_clock_no_second_stuck_kill`,
  `::test_retry_cooldown_blocks_rapid_rekills` (fixed clock; fail without the fix).
- By: developer · Role: developer · Date: 2026-11-10

### AC5 outcome: contract fix landed (unit w02-03, 2026-11-10)
- `sentinel_gen.py`: new `CTL_ENVELOPE_SECTION` rendered into `pack/sentinel-contract.md` — lists the `ao.ctl/v1` envelope fields
  (`created_at` not `ts`, `rationale` not `reason`, ids like `task_id` inside `payload`), states "unknown top-level fields are rejected",
  and gives concrete `note` and `kill` examples.
- Test `test_contract_renders_ctl_envelope_with_valid_examples` asserts `created_at`/rejection wording and runs both examples through
  the real `_parse_entry` validator, so the docs cannot drift from the schema.
- Not done (optional): collecting all envelope violations into one `_parse_entry` detail — left alone because `sentinel_tick.py` has
  concurrent uncommitted edits from another unit. Followups 3 and 4 above remain owner decisions.


## Findings: D3 stranding — reproduced, cause confirmed, fixed (AC4; unit w01-03, 2026-11-11)

**Repro (before fix):** `tests/test_engine_failure_isolation_of_work.py` — roots `a` (fails), `b`..`e`, plus `a2` depends_on `a`,
`max_parallel=2` (more ready tasks than slots). Result before the fix: `a` failed, `b` ran, `c`/`d`/`e` never dispatched, run
`failed` with `pending` tasks (run log: `run.end status=failed` immediately after the in-flight sibling settled).

**Confirmed cause — NOT in `scheduler.py`** (that module is only the cron/event trigger abstraction; the ticket's guess of a
scheduler rule was wrong). It is engine fail-fast: `Orchestrator._settle_completed_task` ended with
`if ts.status not in ("succeeded","skipped"): state.status="failed"; return SettleResult(signal="halt")`, and the `run()` loop
reacts to `halt` with `_drain_remaining` (settle only what is already in flight) then `break`. So any task failure stopped the
FILL loop: ready-but-queued independent tasks (beyond `max_parallel`) were never submitted. `ao resume` recomputes readiness,
which is why `w03-10` dispatched immediately afterwards.

**Fix (engine.py):** task-level `failed`/`timed_out` now records the id in `_RunContext.failed_tasks` and returns `settled`
(no halt). `_ready_ids` skips those ids this run; their dependents never become ready (predecessor unsettled) so only
dependents are left pending. When nothing runnable remains the run ends `failed` (`state.status` set at run end if
`failed_tasks`). Budget/breaker/cancel/injection-error halts still halt immediately (unchanged). Resume re-runs the failed task
and its dependents (tested).

**Behaviour change:** default is now "continue independent work after a task failure" (was fail-fast). Two pre-existing e2e
isolation crash-resume tests used an independent `task_b` as the "never dispatched" task; made it `depends_on: [task_a]`
(same intent). No opt-out flag added — say if a `fail_fast` workflow setting is wanted.

**Status:** AC4 done. mypy src + ruff green. Full pytest: 15 failures, all outside this change — 9 sentinel-generation/powers
e2e + 2 bench fake-subject suites fail identically without my diff (other in-flight units' WIP in the shared checkout);
`test_run_options` snapshot and `nfr2` were from WIP/my test placement (nfr2 fixed by moving tests to a new file).
