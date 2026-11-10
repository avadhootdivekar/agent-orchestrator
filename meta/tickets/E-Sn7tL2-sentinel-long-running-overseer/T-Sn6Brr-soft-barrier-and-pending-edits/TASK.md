# TASK: T-Sn6Brr-soft-barrier-and-pending-edits

## Metadata
- Task ID: `T-Sn6Brr-soft-barrier-and-pending-edits`
- Epic ID: `E-Sn7tL2-sentinel-long-running-overseer`
- Owner: `developer (claude-sonnet-5-5)`
- Created: `2026-11-09`
- Last Updated: `2026-11-09`
- Status: `Done` (AC5 closed 2026-11-09, W10 w10-01)
- Estimate: `< 3 days`
- Phase: P2
- Agent: developer (+ reviewer-opus: engine core)

## Requirements Mapping
- Requirement IDs: FR-8, FR-11, FR-12, FR-13; D3

## Description
1. `barrier: soft` on `emit_tasks` tasks (spec field; overseer checkpoints set it in T-Sn7Bkl): may start while
non-predecessor tasks run; manifest must be additive w.r.t. in-flight tasks; graph rebuild with tasks in flight.
2. Shared mechanical checker module (extract from overseer `ckpt-check` the structural rules; charter rules only when a
charter exists) used by every mutation source.
3. `inject` and `edit_pending` inbox kinds: add/remove edges between pending tasks, cancel/skip pending, priority;
atomic all-or-nothing; reject rules per FR-12 (cycles, running/finished, input-artifact producer edges, route sinks,
hard-barrier cones). FILL honours priority among ready tasks with stable tie-break.
4. `SPAWN_ORIGIN_SENTINEL` with parent ids `sentinel-tick` / `sentinel-gNN` / `ctl:<actor>`; graph API exposes it.

## Acceptance Criteria
1. Engine test: soft-barrier emitter runs while 3 unrelated tasks are in flight; they settle normally; no double dispatch.
2. Checker matrix tests for every FR-12 rejection rule and every budget stage (FR-11).
3. `ao ctl` / inbox `edit_pending` e2e: accepted edit changes dispatch order; rejected edit quarantined with rule id.
4. Resume after crash between consume and save never duplicates or loses an edit (G5).
5. Graph UI shows sentinel-origin nodes and edited edges (styling).

## Risks
- Spike no-go → implement fallback only (leaf injection, no soft barrier) and record deviation.

## Dependencies
- T-Sn0Spk GO
- T-Sn3Tck

## Pseudocode / Algorithm
```text
_is_barrier: emit_tasks and spec.barrier=='soft' -> 'soft'
FILL: soft barrier may launch if none of its preds in flight; while it runs, FILL continues for non-dependent tasks
settle(soft emitter): validate manifest additive vs in_flight -> inject -> rebuild graph (in-flight aware)
```

## Schemas / Interface Notes
- TaskSpec.barrier: 'hard'|'soft' (default hard for emitters)
- `edit_pending` payload per EPIC §4.5

## Handoff Boundary
- Upstream: T-Sn3Tck
- Downstream: T-Sn7Bkl, T-Sn9Pow

## Artifacts
- Docs/comments: `meta/tickets/E-Sn7tL2-sentinel-long-running-overseer/T-Sn6Brr-soft-barrier-and-pending-edits/`
- Large outputs: `N/A`

## Standard gates (every task)
- `pytest -q`, `ruff check . && ruff format --check .`, `mypy .` green; UI changes also `npm test` + `npm run build` in `ui/`.
- New behaviour off / absent settings ⇒ byte-identical behaviour (golden tests stay green).
- Work only in `./agent-orchestrator` on the epic branch; test against temp workspaces; never `install.sh` / restart `ao.service`.
- Update this TASK.md + STATUS.md and the epic STATUS.md rollup together.
