# TASK: T-17v8sr-docs-refresh

## Metadata
- Task ID: `T-17v8sr-docs-refresh`
- Epic ID: `E-v0f0c9-logging-dynamic-workflows`
- Owner: architect
- Created: 2026-06-18
- Last Updated: 2026-06-18 (done)
- Status: Done
- Estimate: `< 1 day`

## Requirements Mapping
- Requirement IDs: all (post-implementation reconciliation)

## Description
After all implementation tasks land, reconcile `docs-md/` with the shipped
behavior. Update `docs-md/logging-dynamic-workflows-hld.md` to mark resolved
open questions and record any deviations from this design; ensure the spec
reference docs reflect the new `TaskSpec`/`WorkflowSpec`/`LoopSpec` fields, the
new run-directory layout, and the `ao status` behavior. Add a short
authoring guide for `emit_tasks` and `loops`. Mark complete only after the docs
are verified against the implemented code.

## Acceptance Criteria
1. HLD open questions (§9) are resolved or explicitly carried forward with rationale.
2. Any deviation between this design and the implementation is documented in the HLD ("Deviations" section).
3. Spec reference (schema fields for `TaskSpec.emit_tasks`/`task_manifest_path`,
   `WorkflowSpec.loops`/`LoopSpec`, `TaskResult.output_artifact_path`) is documented in `docs-md/`.
4. Run-directory layout (`run.log`, `status.json`, per-task capture files) is documented.
5. A minimal authoring example for `emit_tasks` and for a `LoopSpec` (dev↔review and post-dev review) is included.
6. Docs cross-checked against actual code (field names, paths, CLI output) — no stale references.

## Risks
- Docs drift if written before code stabilizes → this task runs last, after tests pass.

## Dependencies
- All implementation + test tasks (`T-pd2vu2`, `T-f0xkdw`, `T-1gsn0l`, `T-17av6o`, `T-sfdybw`, `T-38jqbk`, `T-5isej3`).

## Pseudocode / Algorithm
```text
1. Diff implemented models/schema vs HLD §3; update HLD + spec reference doc.
2. Verify run-dir layout on a real sample run; capture in docs.
3. Add authoring examples (emit_tasks, LoopSpec 2b, LoopSpec 2c).
4. Resolve/forward HLD §9 open questions; add Deviations section.
5. Update EPIC.md + STATUS.md to Done.
```

## Schemas / Interface Notes
- Interface / API: documentation only.
- Artifacts: `docs-md/logging-dynamic-workflows-hld.md` + spec reference docs under `docs-md/`.

## Handoff Boundary
- Upstream: all implementation/test tasks.
- Downstream: none (closes the epic).

## Artifacts
- Docs: `docs-md/logging-dynamic-workflows-hld.md` and related `docs-md/` pages.
