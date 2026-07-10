# TASK: T-h5b2q7-nested-emission-verification

## Metadata
- Task ID: `T-h5b2q7-nested-emission-verification`
- Epic ID: `E-rc7k2v-run-control-routing-breakers`
- Owner: tester
- Created: 2026-07-09
- Last Updated: 2026-07-09
- Status: Done
- Estimate: `~1 day`

## Requirements Mapping
- Granular HLD §5 E1 (nested emission). Provides FR-G-E1 to epic `E-gd8m4x`. LLD §13 (E1 row), §11.

## Description
Verification task (no engine change expected): prove that an **injected** `emit_tasks:true` task itself injects
its manifest on success — i.e. the engine's dynamic-expansion hook fires for ANY succeeded task, including one
that was itself injected. This unblocks epic `E-gd8m4x` (granular decomposition) which relies on a planner step
emitting step tasks (nested emission). Add an integration test + a short doc note. If a gap is found, raise a
BLOCKED note with the exact engine behaviour observed (do not silently patch here — scope is verification).

## Acceptance Criteria
1. `Given` a static emitter A that emits emitter B (with `skip_if_outputs_exist:false`), and B emits leaf C,
   `when` run, `then` C executes and completes; `state.injected_tasks` contains B and C (injection depth 2).
2. The test asserts the engine expansion hook fires for the injected B (not only for static A) — B's manifest is
   read and C injected.
3. A doc note in `docs-md/guide-dynamic-task-injection.md` (or the granular HLD) records the verified behaviour
   and the `skip_if_outputs_exist:false` requirement for emitters (memory `skipped-emit-task-never-injects`).
4. Cross-reference: note that `injected_task_count` (E2, T-q5n7k2) is the safety cap for nested emission.
5. `uv run pytest -q` green for the new test; ruff/mypy clean.

## Risks
- If nested emission does NOT work as expected, this becomes a code task — flag BLOCKED with evidence rather than
  weakening the assertion.
- Injected id uniqueness across the whole run (memory `injected-task-ids-globally-unique`) — namespace B/C ids.

## Dependencies
- Upstream: none (verifies current engine); can start in Wave 1 to unblock `E-gd8m4x` early.
- Downstream: `E-gd8m4x` FR-G-E1; the `injected_task_count` cap (T-q5n7k2) references this.

## Pseudocode / Algorithm
See LLD §11 (injection edge cases) + engine.py dynamic-expansion hook (2a, :638 `task.emit_tasks and succeeded`).

## Schemas / Interface Notes
- Interface: none new; integration test only.
- Artifacts: task manifests written by A and B (bounded control JSON, `{"tasks":[...]}`).
- Triggers/events: `task.injected`.

## Handoff Boundary
- Upstream: current engine behaviour.
- Downstream: `E-gd8m4x` starts once E1 verified; injection cap via T-q5n7k2.

## Artifacts
- Docs/comments: `ad/tickets/E-rc7k2v-run-control-routing-breakers/T-h5b2q7-nested-emission-verification/`
