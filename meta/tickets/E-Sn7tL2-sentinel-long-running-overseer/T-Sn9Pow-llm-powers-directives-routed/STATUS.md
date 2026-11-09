# STATUS

- ID: `T-Sn9Pow-llm-powers-directives-routed`
- Updated At: `2026-11-09`
- State: `Done` (one AC5-of-T-Sn6Brr sub-item deferred, below)
- Owner: `developer (claude-sonnet-5-5)`

## This update
- By: claude-opus-5-5 · Role: architect · Date: 2026-11-09 · Comment: Task created from epic breakdown (owner-approved design).
- By: claude-sonnet-5-5 · Role: developer · Date: 2026-11-09 · Comment: Implemented (HLD §8.7): `sentinel_powers.py` (PowerGate,
  directives, planner contract), `sentinel_panel.py` + `GET /runs/{id}/sentinel/panel`, minimal hooks (`SentinelRuntime.proposal_gate`,
  `directive` kind enabled, `SentinelLlmSpec.{llm_kill_min_running_minutes,planner,planner_idle_seconds}`, engine wiring), lane trigger
  `idle_slots_no_backlog`, overseer-runner `digest.sentinel{}` + OV-R12b + ack mirror, routed-runner `sentinel_planner` param, UI panel v2 +
  sentinel graph styling.

## Evidence
- Tests: `tests/test_sentinel_powers.py` (30: per-power/per-flag checker, kill limits, directives, planner trigger, OV-R12b, digest block,
  engine e2e incl. routed idle-slots -> inject with origin `sentinel`), `tests/ui/test_sentinel_panel_v2.py` (2), UI
  `sentinel-panel-v2.test.tsx` (5) + `graph-sentinel-styling.test.tsx` (2). Gate numbers: see the unit report.

## Risks / Blockers
- Sentinel/checkpoint thrash: shared caps (`PowerGate` per-hour rates, existing `GE-*`/stage rules in the checker) bound it; a dedicated
  thrash e2e with a live overseer + sentinel is left to T-SnXAcc (scripted-driver e2e here covers the individual paths only).
- Edited-edge styling (T-Sn6Brr AC5, second half) needs the graph API to expose `graph_edits`: not done.

## Next actions
1. T-SnXAcc: live smoke + e2e matrix (directive -> real checkpoint, planner against a live routed run).
