# TASK: T-PLsJdO-worked-example

## Metadata
- Task ID: `T-PLsJdO-worked-example`
- Epic ID: `E-DOiDqE-workflow-authoring-skill`
- Owner: dev-epic
- Created: 2026-09-21
- Last Updated: 2026-09-21
- Status: Done
- Estimate: < 1 day

## Requirements Mapping
- Requirement IDs: FR-C3-1

## Description
Since the skill is instructional content, not code, validate it by walking through the
decomposition it would produce for one real, moderately complex task (drawn from sibling-repo
survey evidence or synthesized), and materialize that decomposition as an actual example workflow
spec file. Validate it against `specs/workflow.schema.json` via the real `ao validate` CLI path
(not eyeballing). Place it under `specs/examples/` following existing naming conventions
(`workflow-<topic>.json` + any instruction/hook files it needs under
`specs/examples/instructions/`).

## Acceptance Criteria
1. A new example workflow spec exists under `specs/examples/`.
2. `ao validate --workflow <path> --reposets <path> --agents <path>` run for real, exit code 0,
   output captured verbatim in this task's STATUS.md.
3. The spec is internally consistent with what the skill teaches (task sizes, routing/isolation/
   `max_parallel` choices match the skill's own guidance, with inline comments or an
   accompanying note explaining *why* each choice was made).

## Risks
- None significant — additive new file, no existing spec modified.

## Dependencies
- `T-buzXEz-author-skill` (the worked example must reflect the skill's finished guidance).

## Pseudocode / Algorithm
```text
N/A — spec-authoring task.
```

## Schemas / Interface Notes
- Interface / API: `ao validate` CLI (`src/agent_orchestrator/cli.py:875`).
- Spec / data schema (JSON/YAML): validated against `specs/workflow.schema.json`.
- Triggers / events: N/A
- Artifacts (inputs/outputs by path): new file(s) under `specs/examples/`.

## Handoff Boundary
- Upstream: `T-buzXEz-author-skill`.
- Downstream: none (epic's late-gate validation evidence).

## Artifacts
- Docs/comments: `meta/tickets/E-DOiDqE-workflow-authoring-skill/T-PLsJdO-worked-example/`
- Large outputs: `specs/examples/` (the spec itself).
