# STATUS

- ID: `E-Grpp0X-injected-task-dag-validation-gap`
- Updated At: 2026-09-21
- State: Draft (backlog — not started)
- Owner: unassigned

## This update
Epic filed as a spin-off from Epic C's (`E-DOiDqE-workflow-authoring-skill`) C0
reliability/failure-mode audit. A research fork surfaced `meta/learnings.md`'s
`LRN-20260704-emit-tasks-skips-validation` entry; re-verified independently against current code
in this session (not taken on the 2026-07-04 learning's word alone, since Epics A/B and the
isolation/parallel-execution work have landed since) — confirmed `spec.py::cross_validate` has an
explicit "unknown depends_on" rule (lines ~132-136) that runs once at spec-load time, and
`engine.py::_inject` (line 4084) never re-invokes it for runtime-injected tasks; `dag.py::build_dag`
(lines ~162-164) tolerates an unknown dep by inserting a phantom adjacency entry rather than
raising, deferring the failure downstream instead of catching it at the point of injection. Not
implemented as part of Epic C (out of that epic's scope per its own change-boundary rules).

By: dev-epic · Role: developer · Date: 2026-09-21 · Comment: Found + independently re-verified
during Epic C's C0 audit; unrelated to skill-authoring content (engine/DAG-validation code, not
spec-authoring guidance), so spun off per Epic C's "do NOT fold unrelated fixes into this epic's
scope" instruction rather than fixed inline. Cross-referenced from the workflow-authoring skill's
"common failure modes" section as a caution for routing/`emit_tasks` authors, not fixed there.

## Evidence
- `meta/learnings.md` `LRN-20260704-emit-tasks-skips-validation` (original finding, 2026-07-04).
- `src/agent_orchestrator/spec.py:82-136` — `cross_validate`'s docstring explicitly lists "Task
  depends_on referencing an unknown task id or an unknown loop id not resolvable" as a rule it
  enforces (line 92); implemented at lines 132-136.
- `src/agent_orchestrator/engine.py:4084-4115` — `_inject` checks only for duplicate ids (NFR-6);
  no `depends_on`/agent/hook reference validation against the injected `TaskSpec` objects.
- `src/agent_orchestrator/dag.py:157-166` — `build_dag`'s explicit "Unknown dep — leave as-is so
  cycle/missing detection fires" comment; confirmed this inserts an empty adjacency entry for the
  unknown id rather than raising, so cycle detection (which only checks in-degree > 0) does not
  actually catch it either — the phantom node has in-degree 0 and is silently treated as a
  ready, schedulable node with no corresponding `TaskSpec`.

## Risks / Blockers
- None blocking — no evidence this has manifested in a real captured run in this repo's own
  history; a real, verified code-path gap, not an observed production incident.

## Next actions
1. Prioritize against other backlog epics.
2. When picked up: `T-17PhBS-validate-injected-depends-on` reproduces the current failure mode
   with a test first, then implements the fix per EPIC.md FR-1..FR-3.
