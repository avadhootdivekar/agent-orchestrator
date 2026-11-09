# TASK: T-Sn3Tck-main-loop-tick-inbox-overrides

## Metadata
- Task ID: `T-Sn3Tck-main-loop-tick-inbox-overrides`
- Epic ID: `E-Sn7tL2-sentinel-long-running-overseer`
- Owner: `unassigned`
- Created: `2026-11-09`
- Last Updated: `2026-11-09`
- Status: `Done`
- Estimate: `< 3 days`
- Phase: P1
- Agent: developer (+ reviewer: engine core)

## Requirements Mapping
- Requirement IDs: FR-2, FR-3 (engine side), FR-7 (engine side), FR-18, FR-20, FR-21, NFR-2/3/4/8

## Description
Engine core changes in `engine.py run()`:
1. DRAIN → `wait(in_flight, timeout=tick_seconds, return_when=FIRST_COMPLETED)` when sentinel enabled (unchanged when disabled);
   settle every completed future in the returned set in deterministic order (sorted by task id).
2. `_sentinel_tick(state, workflow, ctx, now)` after `_reconcile_inputs`, before FILL; injectable clock.
3. Control inbox drain: `control/inbox/*.json` → validate `ao.ctl/v1` → apply or quarantine to `control/inbox-rejected/`
   with rule id; consumed ids recorded in `state.sentinel.inbox_consumed` in the same save as the mutation (G5).
   This task implements kinds `set`, `pause_llm`, `resume_llm`, `note`, `request_hold`, `request_cancel`/`cancel`;
   other kinds are accepted by schema but rejected with `not_enabled_yet` until their tasks land.
4. Live overrides: pool created at `max_parallel_ceiling`; FILL uses live soft limit; overrides persisted in
   `state.sentinel.overrides` and mirrored into `state.effective_settings` with source `live-override`.
5. Utilisation metrics `state.metrics` (busy / idle-with-ready / idle-no-ready slot-seconds, utilisation_pct).
6. Fail-safe: any tick exception → `sentinel.error` log, lane disabled for the run, run continues.
7. Wire graceful cancel (`cancel` inbox kind → `cancel_fn` true) — fixes unwired `cancel_fn` for ao-launched runs.

## Acceptance Criteria
1. Loop test: with all workers blocked, tick still runs every `tick_seconds` (fixed clock + fake executor).
2. Inbox: atomic-rename reader ignores `*.tmp`; 1 MiB cap; duplicate id ignored; replay after crash yields no duplicate effect.
3. `set max_parallel=8` with ceiling 8 → 8 concurrent dispatches observed; above ceiling → quarantined.
4. Metrics correct on a scripted timeline (unit test with fixed clock).
5. Tick exception injected → run completes as if disabled; `sentinel.error` logged once.
6. Disabled ⇒ golden byte-identity; tick < 50 ms at 1k tasks (benchmark test, marked slow).

## Risks
- Multiple completions per wake-up change settle ordering — keep deterministic (sorted ids) and cover with tests.

## Dependencies
- T-Sn2Set
- T-Sn0Spk (c),(d)

## Pseudocode / Algorithm
```text
loop:
  if cancel: ...
  reconcile_inputs
  if sentinel.enabled: sentinel_tick(now)   # liveness, metrics, drain inbox, mechanical triggers (later tasks plug in)
  FILL up to live_limit
  done, _ = wait(in_flight, timeout=tick if sentinel.enabled else None, FIRST_COMPLETED)
  for fut in sorted(done, key=tid): settle(...)
```

## Schemas / Interface Notes
- `ao.ctl/v1`: {schema, id, kind, actor, created_at, payload, reason?}
- `state.sentinel`: {enabled, disabled_reason, overrides{}, inbox_consumed[], llm_paused, notes[]}
- `state.metrics`: see FR-20

## Handoff Boundary
- Upstream: T-Sn2Set settings
- Downstream: T-Sn3Ctl, T-Sn4Rec, T-Sn6Brr, T-Sn7Bkl, T-Sn8Gen plug into the tick

## Artifacts
- Docs/comments: `meta/tickets/E-Sn7tL2-sentinel-long-running-overseer/T-Sn3Tck-main-loop-tick-inbox-overrides/`
- Large outputs: `N/A`

## Standard gates (every task)
- `pytest -q`, `ruff check . && ruff format --check .`, `mypy .` green; UI changes also `npm test` + `npm run build` in `ui/`.
- New behaviour off / absent settings ⇒ byte-identical behaviour (golden tests stay green).
- Work only in `./agent-orchestrator` on the epic branch; test against temp workspaces; never `install.sh` / restart `ao.service`.
- Update this TASK.md + STATUS.md and the epic STATUS.md rollup together.

## Implementation notes (2026-11-09, claude-sonnet-5-5, developer)
- New `src/agent_orchestrator/sentinel_tick.py` (inbox validate/apply/quarantine, overrides, metrics); `engine.py` wires `_start_sentinel` / `_sentinel_tick` / `_halt_cancelled`; `models.py` adds `RunState.sentinel` + `RunState.metrics` (None unless enabled).
- Inbox kinds implemented: `set` (only `max_parallel`, <= ceiling), `pause_llm`, `resume_llm`, `note`, `request_hold` (records `hold_requested`; consumers land later), `request_cancel`/`cancel` (graceful cancel). Others quarantined as `not_enabled_yet`. Rule ids: CTL-SIZE/JSON/SCHEMA/KIND/PAYLOAD/SET-KEY/CEILING/FILENAME.
- Entries drained in lexicographic id order; ids kept in `state.sentinel.inbox_consumed` (ring 1000); processed files moved to `inbox-processed/` (keep 200) only after the save that recorded their ids.
- Tests: `tests/test_sentinel_tick.py` (10, incl. one `slow` tick-cost test).
