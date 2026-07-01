# TASK: T-1vuzyi-sum-of-array-workflow

## Metadata
- Task ID: `T-1vuzyi-sum-of-array-workflow`
- Epic ID: `E-dvehbb-e2e-playground-tests`
- Owner: developer
- Created: 2026-07-01
- Last Updated: 2026-07-01
- Status: Done
- Estimate: `< 3 days`
- MVP: **yes**

## Requirements Mapping
- FR-1, FR-2, FR-3, NFR-1, NFR-2, NFR-5. Key design facts 1–5.

## Description
Author the Phase-1 proven example `playground/sum-of-array/` end-to-end: the tiny
problem statement, the workflow spec that maps the requested agent pipeline onto AO
primitives, the instruction files, both agents files, the reposet, and the
deterministic control-file + expected-structure fixtures.

The workflow shape (ADR-001, design doc §7) is a **static spine** with one
`emit_tasks` fan-out and one `LoopSpec` review round:

```
architect-design   (architect)  outputs: output/design.md
design-review      (reviewer)   inputs: output/design.md         outputs: output/design-review.md   depends_on: [architect-design]
architect-breakdown(architect)  inputs: output/design.md,output/design-review.md   depends_on: [design-review]
                   emit_tasks: true   task_manifest_path: output/tasks-manifest.json   outputs: []
      -- emit injects the fixed single chain (unique paths):
      impl-t1        (developer)   depends_on: [architect-breakdown]  inputs: [output/design.md]                       outputs: [output/tasks/t1/impl.md]
      testwrite-t1   (test-writer) depends_on: [impl-t1]              inputs: [output/tasks/t1/impl.md]                outputs: [output/tasks/t1/tests.md]
      taskreview-t1  (reviewer)    depends_on: [testwrite-t1]         inputs: [output/tasks/t1/impl.md,.../tests.md]   outputs: [output/tasks/t1/review.md]
integrate          (integrator)  depends_on: [architect-breakdown]  inputs: [output/tasks/t1/review.md]  outputs: [output/integrated.md]
   -- LoopSpec review-round, body [bugfix, final-review], gate=final-review:
   bugfix           (developer)   depends_on: [integrate]  inputs: [output/integrated.md]  outputs: [output/bugfix.md]
   final-review     (reviewer)    depends_on: [bugfix]     inputs: [output/bugfix.md]      outputs: [output/final-review.md]
                    gate_output_path: output/final-verdict.json  gate_field: continue  max_iterations: 3
done               (integrator)  depends_on: [review-round]  inputs: [output/final-review.md]  outputs: [output/summary.md]
```

All agent roles map to entries in the agents files; use the fewest distinct agent
names needed (`architect`, `reviewer`, `developer`, `test-writer`, `integrator`).
`integrate`/`done` use `integrator`.

## Inputs / Outputs
- Inputs: `specs/workflow.schema.json`, `specs/agents.schema.json`, `specs/reposet.schema.json`,
  `specs/examples/{workflow,agents,reposet}.json` (shape reference), `engine._clone_body`
  behavior (loop clones clear inputs/outputs), `dag.build_dag` inferred-edge rule.
- Outputs (all under `playground/sum-of-array/`):
  - `PROBLEM.md` — trivial: "implement `sum(nums: list[int]) -> int`; low burn".
  - `workflow.json` — the spine above (id `sum-of-array`, all ids `^[a-z0-9][a-z0-9-_]*$`).
  - `reposet.json` — one repo set `sum-set`, `workspace_root: "."`, one primary repo `code`.
  - `agents.fake.json` — every agent → `{"executor": "fake"}`.
  - `agents.claude.json` — every agent → `{"executor":"claude_cli", "command_template":[...], "prompt_template":"...", "context_window":"isolated"}` (ONLY those 4 keys; `additionalProperties:false`).
  - `instructions/*.md` — one per agent role (design, design-review, breakdown, developer, test-writer, reviewer, bugfix, final-review, integrate, done). `architect-breakdown.md` and `final-review.md` **pin the exact control-file contract** the real agent must emit.
  - `fixtures/tasks-manifest.json` — `{"tasks":[<impl-t1>,<testwrite-t1>,<taskreview-t1>]}` (deterministic-tier pre-seed).
  - `fixtures/final-verdict.json` — `{"continue": false}` (happy path: one round).
  - `fixtures/final-verdict-iter2.json` — `{"continue": false}` (used when a 2-round variant pre-seeds iter1=true).
  - `fixtures/expected_paths.json` — the canonical list of files/dirs the deterministic tier asserts exist.
  - `fixtures/expected_events.json` — expected `run.log` event set + expected `task.start` order.

## Acceptance Criteria
1. Given `playground/sum-of-array/workflow.json` + `reposet.json` + `agents.fake.json`,
   When `uv run python -m agent_orchestrator.validate` (or `ao validate`) runs, Then exit 0
   ("OK: all specs valid"). Same for `agents.claude.json`.
2. Every `id` (workflow, tasks, loop) matches `^[a-z0-9][a-z0-9-_]*$`; no `E-`/uppercase ids.
3. `agents.claude.json` entries contain ONLY `executor`, `command_template`,
   `prompt_template`, `context_window` (validates against `agents.schema.json`).
4. Injected chain + spine use unique artifact paths; loop-body tasks are the ONLY tasks
   reachable via the loop, and their clones will be inputs/outputs-cleared by the engine.
   A design-time check (in T-ee8hzo) confirms `build_dag` on the *statically expanded*
   graph (spine + manifest tasks merged) is acyclic with the expected edge set.
5. `fixtures/tasks-manifest.json` parses as `{"tasks":[...]}`, each task validates as a
   `TaskSpec` (ids unique vs spine, instruction paths point to real `instructions/*.md`).
6. `fixtures/final-verdict.json` = `{"continue": false}`; `expected_paths.json` and
   `expected_events.json` are present and self-consistent with the workflow.
7. `instructions/architect-breakdown.md` documents the EXACT manifest the real agent must
   write to `output/tasks-manifest.json` (ids, depends_on, unique paths); `instructions/final-review.md`
   documents writing `{"continue": <bool>}` to `output/final-verdict.json`.
8. `PROBLEM.md` is trivial and explicitly low-burn. No `src/` change (NFR-1).

## Risks
- Loop + emit_tasks interaction: keep emit_tasks on the static spine only (NOT inside the
  loop body) — clones set `emit_tasks:false`, so emitting inside a loop would silently
  no-op on iteration ≥2. Design keeps them separate.
- `integrate` must anchor after the spine (explicit `depends_on: [architect-breakdown]`)
  AND after the injected chain (inferred edge from `output/tasks/t1/review.md`). Author
  both so ordering is unambiguous pre- and post-injection.
- Real agent may deviate from the pinned manifest shape → only structure/completion is
  asserted in the real tier (T-g7rjh0); deterministic tier uses the fixture, not the agent.

## Dependencies
- `T-7592ux` (playground root + harness must exist).

## Pseudocode / Algorithm
```text
# fixtures/tasks-manifest.json  (deterministic-tier pre-seed; also the contract real agents follow)
{ "tasks": [
  {"id":"impl-t1","agent":"developer","instruction":"instructions/developer.md",
   "depends_on":["architect-breakdown"],"inputs":["output/design.md"],"outputs":["output/tasks/t1/impl.md"]},
  {"id":"testwrite-t1","agent":"test-writer","instruction":"instructions/test-writer.md",
   "depends_on":["impl-t1"],"inputs":["output/tasks/t1/impl.md"],"outputs":["output/tasks/t1/tests.md"]},
  {"id":"taskreview-t1","agent":"reviewer","instruction":"instructions/reviewer.md",
   "depends_on":["testwrite-t1"],"inputs":["output/tasks/t1/impl.md","output/tasks/t1/tests.md"],
   "outputs":["output/tasks/t1/review.md"]}
]}
```

## Schemas / Interface Notes
- Interface / API: N/A (assets only).
- Spec / data schema: `workflow.schema.json` (spine + LoopSpec), `agents.schema.json`,
  `reposet.schema.json`; control-file schemas `{"tasks":[TaskSpec...]}` and `{"continue":bool}`.
- Triggers / events: workflow `triggers` default `[{"type":"manual"}]`.
- Artifacts: everything under `playground/sum-of-array/`.

## Handoff Boundary
- Upstream: `T-7592ux`.
- Downstream: `T-ee8hzo` (validates these specs+fixtures), `T-r21p4y` (runs them),
  `T-g7rjh0` (real-tier reuses the same spec + `agents.claude.json`).

## Artifacts
- Docs/comments: this task folder.
- Large outputs: none (assets are product files under `playground/`).
