# TASK: T-ov3c03-overlay-conflict-resolution-flow

## Metadata
- Task ID: `T-ov3c03-overlay-conflict-resolution-flow`
- Epic ID: `E-6396jp-workspace-customization-overlays-and-upgrade-flow`
- Owner: `TODO (unassigned)`
- Created: `2026-10-16`
- Last Updated: `2026-10-16`
- Status: `Draft`
- Priority: `P3 (backlog)`
- Estimate: `< 3 days` (split further if discovery says otherwise)

## Requirements Mapping
- Requirement IDs: see EPIC.md

## Re-triage
Re-triage 2026-10-16: demoted to backlog; the skill resolves conflicts with `git merge-file` and human review. Revisit only if the skill is used often and conflicts are routine.

## Description
When the builtin changes under an overlay patch: show base (version the overlay was written against), theirs (new builtin) and ours (overlay); support `ao templates resolve <ws> <template>` (interactive/--keep-overlay/--take-builtin) and record the resolution. Overlays that only add files or merge fragments should almost never conflict.

## Acceptance Criteria
1. Conflict output shows three-way diff for a replaced file.
2. Resolution recorded and re-verifiable.
3. Scaffolding is blocked (clear error, not a silent mix) only for unresolved replace-conflicts; add/merge patches still apply.

## Risks
- Keep UX non-interactive friendly for runs and CI.

## Dependencies
T-ov1a01, T-ov2b02.

## Handoff Boundary
- Upstream: T-ov1a01, T-ov2b02.
- Downstream: other tasks in `E-6396jp-workspace-customization-overlays-and-upgrade-flow`

## Artifacts
- Docs/comments: `meta/tickets/E-6396jp-workspace-customization-overlays-and-upgrade-flow/T-ov3c03-overlay-conflict-resolution-flow/`
- Large outputs: `output/...`
