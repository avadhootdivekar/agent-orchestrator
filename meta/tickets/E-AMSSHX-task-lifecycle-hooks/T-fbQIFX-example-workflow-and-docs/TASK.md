# TASK: T-fbQIFX-example-workflow-and-docs

## Metadata
- Task ID: `T-fbQIFX-example-workflow-and-docs`
- Epic ID: `E-AMSSHX-task-lifecycle-hooks`
- Owner: developer/tester (delegated)
- Created: 2026-09-21
- Last Updated: 2026-09-21
- Status: Done
- Estimate: < 1 day

## Requirements Mapping
- Requirement IDs: FR-7 (epic)

## Description
Add one example workflow spec under `specs/examples/` (mirroring the existing
`workflow-monitoring.json`/`workflow-budget.json` style) that declares both a `pre_hook` and a
`post_hook` on at least one task, plus small standalone hook scripts it invokes, so the mechanism
can be demonstrated end-to-end via the real CLI (`ao validate` / `ao run`) with the repo's
`FakeExecutor` or a trivial local agent — not just via unit tests reaching into `Orchestrator`
directly.

## Acceptance Criteria
1. `specs/examples/workflow-hooks.json` — valid against `specs/workflow.schema.json` (post
   T-DgheoA). **Rev 2 shape**: declares a `"hooks"` registry at workflow root (e.g.
   `check_disk_space` and `grade`), with at least one task referencing a hook via
   `"pre_hook": {"use": "check_disk_space"}` (a script that always passes, demonstrating the
   no-op-adjacent happy path) and a second task referencing `"post_hook": {"use": "grade",
   "on_failure": "ignore"}` — the `grade` hook writes a `result.json` detail payload (`score` +
   `detail`) and its script can be pointed to pass or fail (demonstrating the
   `AO_HOOK_RESULT_PATH` contract from HLD §5/§7).
2. Hook scripts live under `specs/examples/hooks/` (or `scripts/helper/epics/E-AMSSHX/hooks/` if
   that fits repo convention better — match whatever sibling example specs already do for their
   own small scripts), pure-Python (`python3`, no extra deps), so they run in CI without network
   access.
3. `ao validate specs/examples/workflow-hooks.json` (with a matching example agents/reposet file,
   mirroring the other `specs/examples/*.json` companions) passes.
4. Referenced and exercised by T-FCC8mT's late-gate e2e run.

## Risks
- None significant — new files only.

## Dependencies
- T-AHvmYR, T-lzQEyy, T-DgheoA.

## Pseudocode / Algorithm
```text
N/A — see acceptance criteria.
```

## Schemas / Interface Notes
- Interface / API: N/A.
- Spec / data schema (JSON/YAML): `specs/examples/workflow-hooks.json` against
  `specs/workflow.schema.json`.
- Triggers / events: N/A.
- Artifacts (inputs/outputs by path): hook scripts under `specs/examples/hooks/`.

## Handoff Boundary
- Upstream: T-AHvmYR, T-lzQEyy, T-DgheoA.
- Downstream: T-FCC8mT (late-gate e2e run uses this exact spec).

## Artifacts
- Docs/comments:
  `meta/tickets/E-AMSSHX-task-lifecycle-hooks/T-fbQIFX-example-workflow-and-docs/`
- Large outputs: N/A
