# TASK: T-ov2b02-template-version-stamp-and-drift

## Metadata
- Task ID: `T-ov2b02-template-version-stamp-and-drift`
- Epic ID: `E-6396jp-workspace-customization-overlays-and-upgrade-flow`
- Owner: `TODO (unassigned)`
- Created: `2026-10-16`
- Last Updated: `2026-10-16`
- Status: `Draft`
- Priority: `P2`
- Estimate: `< 3 days` (split further if discovery says otherwise)

## Requirements Mapping
- Requirement IDs: see EPIC.md

## Re-triage
Re-triage 2026-10-16: REFRAMED to the one change worth making in the tool: record a baseline content hash (and template version) for `keep_existing` assets at scaffold time, so `ao` can auto-refresh workspace files the workspace never modified and only flag the modified ones. Today those assets never refresh, silently staleing every workspace's instructions after an upgrade. Reporting/status UI is secondary.

## Description
Every scaffolded run and every overlay records the builtin template version/hash it was built against. `ao templates status` (or `ao doctor`) reports, per workspace: builtin changed since the overlay was last verified; overlay patches that no longer apply cleanly (conflict); runs scaffolded from an older template (informational; running runs are never touched).

## Acceptance Criteria
1. After an `ao` upgrade that changes a file an overlay replaces, status reports a conflict naming the file and both versions.
2. Clean overlays report 'verified against <version>'.
3. Non-destructive: nothing is rewritten without an explicit command.

## Risks
- Define 'template version' (package version + per-file content hash).

## Dependencies
T-ov1a01.

## Handoff Boundary
- Upstream: T-ov1a01.
- Downstream: other tasks in `E-6396jp-workspace-customization-overlays-and-upgrade-flow`

## Artifacts
- Docs/comments: `meta/tickets/E-6396jp-workspace-customization-overlays-and-upgrade-flow/T-ov2b02-template-version-stamp-and-drift/`
- Large outputs: `output/...`
