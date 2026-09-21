# TASK: T-FCC8mT-e2e-verification

## Metadata
- Task ID: `T-FCC8mT-e2e-verification`
- Epic ID: `E-AMSSHX-task-lifecycle-hooks`
- Owner: dev-epic (driven directly, not delegated — see STATUS.md)
- Created: 2026-09-21
- Last Updated: 2026-09-21
- Status: Done
- Estimate: < 1 day

## Requirements Mapping
- Requirement IDs: FR-7 (epic); this is the epic's "late gate."

## Description
Late-gate end-to-end verification (CLAUDE.md's "deploy/run path works" checklist item + the
dev-epic role's own late-gate requirement): run the example workflow from T-fbQIFX through the
REAL `ao` CLI (not internal `Orchestrator` API calls), and produce concrete evidence that
`pre_hook`/`post_hook` actually fire at the right lifecycle point, the no-op path costs nothing
for a hookless sibling task, and the on_failure policies behave as designed.

## Acceptance Criteria
1. `ao validate specs/examples/workflow-hooks.json` (with its reposet/agents companions) —
   actual command output captured (not claimed).
2. `ao run` (or the CLI's `CliRunner`-driven e2e test equivalent, per this repo's
   `tests/test_e2e_cli.py` convention — invoking the CLI as the outermost boundary, per CLAUDE.md
   e2e testing rule) against `workflow-hooks.json` — actual run, actual `RunState`/`run.log`
   inspected afterward.
3. Evidence captured showing: `pre_hook`/`post_hook` capture directories exist with
   `context.json`/`stdout.txt`/`stderr.txt` (+ `result.json` where the hook wrote one);
   `TaskRunState.pre_hook_result`/`post_hook_result` populated in the persisted `state.json`;
   a scenario where `on_failure="fail_task"` on a post_hook flips a succeeded task to failed, and
   the run's overall status reflects it.
4. Evidence saved under `output/E-AMSSHX-task-lifecycle-hooks/` (large-output convention) and
   linked from this ticket + the epic ticket.
5. `pytest -q -m e2e` (or however this repo scopes its e2e marker/tests) — actual pass/fail
   reported.

## Risks
- None beyond the standard "actually run it, don't claim it" discipline CLAUDE.md already
  mandates.

## Dependencies
- T-fbQIFX, T-jI3P4p (suite must be green first).

## Pseudocode / Algorithm
```text
N/A.
```

## Schemas / Interface Notes
- Interface / API: `ao` CLI (`CliRunner`) — outermost boundary per CLAUDE.md e2e rule.
- Spec / data schema (JSON/YAML): `specs/examples/workflow-hooks.json`.
- Triggers / events: N/A (manual trigger).
- Artifacts (inputs/outputs by path): `output/E-AMSSHX-task-lifecycle-hooks/`.

## Handoff Boundary
- Upstream: T-fbQIFX, T-jI3P4p.
- Downstream: epic completion handoff cites this ticket's evidence directly.

## Artifacts
- Docs/comments: `meta/tickets/E-AMSSHX-task-lifecycle-hooks/T-FCC8mT-e2e-verification/`
- Large outputs: `output/E-AMSSHX-task-lifecycle-hooks/`
