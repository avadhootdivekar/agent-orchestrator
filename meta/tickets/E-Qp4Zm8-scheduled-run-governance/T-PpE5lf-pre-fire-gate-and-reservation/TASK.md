# TASK: T-PpE5lf-pre-fire-gate-and-reservation

## Metadata
- Task ID: `T-PpE5lf-pre-fire-gate-and-reservation`
- Epic ID: `E-Qp4Zm8-scheduled-run-governance`
- Owner: developer agent
- Created: 2026-11-10
- Last Updated: 2026-11-10
- Status: Draft
- Estimate: 3 days

## Requirements Mapping
- Requirement IDs: FR-1, FR-2, FR-3, FR-4, NFR-1, NFR-2 (see `../EPIC.md`); owner decisions D-3, D-5

## Description
Wire governance into E-Sc9Rt4's fire path:
- one gate step in `ScheduleEngine.evaluate()`, between overlap and concurrency;
- an authoritative `check_and_reserve()` in `ScheduledLauncher` under a flock, which writes the reservation and
  prompt snapshot into the `intended` record;
- the `cost_cap_usd` option on every governed launch, with a fail-closed argv check;
- a retire hook that finalizes cap state when a run ends.

Read HLD §8, §11, §12, §17 and ADR-0025 D4/D5/D7. Read the **merged** code of E-Sc9Rt4 `T-Ev3Qm5`
(`schedule_engine.py`: the normative check order, `FireDecision`, `FireReason`, `retire_finished`), `T-Lp4Wt6`
(`schedule_launch.py`), `T-Ri7Dz2` (`--run-id`, and how it got allow-listed), plus `T-Uz0Uol` and `T-et7Edf`.

Files you own:
- `src/agent_orchestrator/service/governance_gate.py` (new): `GateResult`, `gate()`, `check_and_reserve()`,
  `read_prompt()` / `PromptSnapshot`, `GOVERNANCE_LOCK_NAME = "usage.lock"`, the reason constants.
- `src/agent_orchestrator/service/schedule_engine.py` (edit): (a) add the reason constants to `FireReason`; (b) the
  single gate step at the documented position; (c) `FireDecision` gains `effective_limits` and `gate` (defaulted);
  (d) the queued-fire drain re-gates against its original `scheduled_for`, read from `queued_scheduled_for` (M2: stored
  by E-Sc9Rt4 T-Ev3Qm5 next to `queued_fire_key`, see STATUS cross-epic requests); (e) the `retire_finished` hook sets
  `cap_state` (`paused`/`aborted`) from `state.json` (`builtin.cost_cap` in `tripped_breakers` + `status == failed`),
  sets `pause_expires_at`, and emits `schedule.governance.run_cost_capped`; (f) `__init__` gains
  `usage_ledger_factory` and `governance_lock_path` kwargs with defaults.
- `src/agent_orchestrator/service/schedule_launch.py` (edit): call `check_and_reserve()` **in place of** (not in
  addition to) T-Lp4Wt6's own `intended` write, so there is exactly one `intended` write per launch; put `cost_cap_usd` in the options when reserved; perform the pre-spawn and post-spawn argv checks
  (HLD §8.4); pass `prompt=snapshot.text`.
- `tests/service/test_governance_gate.py` (new); additive cases in `tests/service/test_schedule_engine.py` and
  `tests/service/test_schedule_launch.py`.

Do NOT touch: `engine.py`, `models.py`, `cli.py`, `run_options.py` (T-et7Edf), `ui/processes.py` (read-only), `schedules/`
(read-only), `usage_ledger.py` (T-Uz0Uol; request changes via STATUS), `service/cli.py`/`hub.py`, `ui/`.

## Acceptance Criteria
1. **Order:** `evaluate()` performs `... already_fired → overlap → governance → concurrency → FIRE`. Tests:
   (a) an overlap-blocked instant over quota reports `overlap_skip`, not `quota_exhausted`; (b) an instant over quota
   that would also be concurrency-blocked reports `quota_exhausted` and is burned (`fires.exists(key)`); (c) a
   concurrency-blocked instant within quota is **not** burned (E-Sc9Rt4's asymmetry preserved).
2. Every governance skip writes `suppressed(reason=...)` with a `governance` skeleton (`limits_digest`, tz,
   week_start, the gate `detail`) and emits `schedule.governance.skipped` with `reason`, `period`, `used`, `max`,
   `remaining_usd`, `needed_usd`, `resets_at`, as applicable.
3. `gate()` matches HLD §8.2: quotas in declared order (first failing reported); with `per_run_usd`, skip iff
   `remaining < per_run_usd` (Q-3), reservation = `per_run_usd`; period-only, skip iff `remaining <
   MIN_RESERVATION_USD`, reservation = remaining rounded **down** to cents. There are boundary tests at ±$0.01.
4. `check_and_reserve()` takes an exclusive flock on `<state_dir>/schedules/usage.lock`, invalidates the ledger
   index for the schedule, re-gates, reads the prompt, and writes the `intended` record **with**
   `governance.reserved_usd` and `run_cost_cap_usd` and `prompt{path, sha256, bytes}` in one atomic write, then
   releases the lock before spawning.
5. **Races:** (a) a natural fire + a drained run-now request in one tick with `quota day=1` ⇒ exactly one launch, and
   the second is `suppressed(quota_exhausted)`; (b) two threads calling the in-process run-now path concurrently with
   `quota day=1` ⇒ exactly one launch (flock).
6. **Crash between reserve and spawn:** a fault injected after the `intended` write, then `start()` ⇒ `orphaned`.
   The ledger counts runs=1, $0. No relaunch.
7. **Fail-closed argv:** with a stubbed options renderer that drops `cost_cap_usd`, no process is spawned, the record
   is `launch_failed(reason="cost_cap_not_forwarded")`, and `schedule.governance.launch_unsafe` is emitted at ERROR.
   With the real renderer, `StubSupervisor` receives `options["cost_cap_usd"] == reservation` and the rendered argv
   contains `--cost-cap-usd`.
8. **Prompt:** missing ⇒ `prompt_missing` (burned); > `MAX_PROMPT_BYTES` ⇒ `prompt_missing(detail=too_large)`;
   invalid UTF-8 ⇒ `prompt_missing(detail=not_utf8)`; pin mismatch ⇒ `prompt_changed`; pin match ⇒ fires. The bytes
   handed to `launch_run(prompt=...)` equal the file bytes, and the sha in the record equals `sha256(file)`.
9. **Retire hook (M3):** a stub run whose `state.json` has `status=failed` + a `builtin.cost_cap` trip **whose `at` is
   after the latest `governance.resumes[].at`** (for a never-resumed run: any such trip) ⇒ `cap_state`
   `paused` (on_cap pause) with `pause_expires_at = paused_at + ttl`, or `aborted` + `final=true` (on_cap abort), and
   one `schedule.governance.run_cost_capped` event carrying `overshoot_usd`. A run ending `succeeded` ⇒ `final=true`,
   `cap_state=none`.
10. Period cap mid-run integration: overlap `allow`, `per_run=2`, `week=5`, two running stubs ⇒ the third instant
    is skipped `cost_cap_reached`. Settle one at $0.50 ⇒ the next instant is admitted.
11. **Fail closed on error:** a gate that raises ⇒ `suppressed(governance_error)` + ERROR event, and E-Sc9Rt4's
    `bump_eval_error`/quarantine path still applies.
12. `run-now --force` does not bypass governance (test).
13. Tick budget: with 200 governed schedules × 30 fire records each, one tick stays within
    `SCHEDULE_TICK_BUDGET_SECONDS` on CI, using the mtime cache (report the measured time).
14. ≥ 85 % coverage on `governance_gate.py`; E-Sc9Rt4 engine/launcher tests green; ruff/format/mypy clean.
15. **(M2) Two governance call sites.** The gate runs at the main `evaluate()` position **and** on the `overlap: queue`
    drain return (E-Sc9Rt4 §6.3 step 3 returns `FIRE(from_queue=...)` before `next_fire`/`already_fired`/overlap/
    concurrency). Test: a queued fire that is over quota at drain time is cleared and burned `quota_exhausted` in its
    **original** window (via `queued_scheduled_for`), not the drain-time window; one within quota fires.
16. **(M3) Paused classification after a resume.** Test: trip → governed resume → a later non-cap failure (e.g. a task
    failure under T-Rk4Wn7 D3) ⇒ `cap_state` is **not** `paused` and no new `pause_expires_at` is set. A resume followed
    by a second cap trip ⇒ `paused` with a fresh TTL.
17. **(single `intended` write)** Test: exactly one `intended` write per launch (count store writes), and it already
    carries `governance.reserved_usd`, `run_cost_cap_usd` and `prompt{...}`.

## Risks
- Modifying E-Sc9Rt4's normative order is the single most review-sensitive change in this epic. Keep it to one
  inserted block with a comment citing HLD §8.1.
- Holding the flock across `Popen` would serialize launches needlessly. Release it before spawning (AC4).
- `_render_options` silently drops keys. That is why AC7 exists. Never "fix" it by editing `ui/processes.py` here.

## Dependencies
- Upstream: E-Sc9Rt4 `T-Ev3Qm5` (with `queued_scheduled_for`), `T-Lp4Wt6`, `T-Ri7Dz2` (merged); `T-Uz0Uol`; `T-et7Edf`; `T-tPlK1T`.
- Downstream: `T-nI4DBf`, `T-rwj4nV`, `T-88wSLC`.

## Pseudocode / Algorithm
```text
HLD §8.2 gate()/check_and_reserve() and §11 read_prompt() are normative.

# evaluate(), inserted after the overlap block:
limits = b.effective_limits(file.defaults)
g = governance_gate.gate(b, limits, scheduled_for, ledger=self._ledger(entry.root), tz=..., week_start=...)
if not g.admit:
    fires.write_suppressed(fire_key, reason=g.reason, governance=skeleton(limits, g))
    emit("schedule.governance.skipped", schedule_id=b.id, reason=g.reason, **g.detail)
    return NO_FIRE(reason=g.reason)
# fall through to concurrency
```

## Schemas / Interface Notes
- Interface: `service.governance_gate.{gate, check_and_reserve, read_prompt, GateResult, PromptSnapshot}`.
- Spec / data schema: fire record `governance` (HLD §6.4); the drop-request schema is untouched here (T-nI4DBf).
- Triggers / events: `schedule.governance.{skipped, reserved, run_cost_capped, launch_unsafe, error}`.
- Artifacts: `<state_dir>/schedules/usage.lock`, fire records, launch prompt files (written by `launch_run`).

## Handoff Boundary
- Upstream: E-Sc9Rt4 engine + launcher merged code; ledger; engine cost cap.
- Downstream: `T-nI4DBf` adds the resume path on top of the retire hook and the lock.

## Artifacts
- Docs/comments: `meta/tickets/E-Qp4Zm8-scheduled-run-governance/T-PpE5lf-pre-fire-gate-and-reservation/`
- Large outputs: N/A
