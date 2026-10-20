# TASK: T-ov5e05-workspace-customization-manifest-and-docs

## Metadata
- Task ID: `T-ov5e05-workspace-customization-manifest-and-docs`
- Epic ID: `E-6396jp-workspace-customization-overlays-and-upgrade-flow`
- Owner: `TODO (unassigned)`
- Created: `2026-10-16`
- Last Updated: `2026-10-16`
- Status: `Draft`
- Priority: `Superseded`
- Estimate: `< 3 days` (split further if discovery says otherwise)

## Requirements Mapping
- Requirement IDs: see EPIC.md

## Re-triage
Re-triage 2026-10-16: superseded. The manifest/convention lives in the `/ao-overlay-sync` skill (`.ao/overlays/<template>/MANIFEST.md`). Close unless the convention is later promoted into the tool.

## Description
A single, human-readable manifest per workspace (e.g. `.ao/customizations.md` or `.ao/overlays/README`) generated/maintained by `ao`: which builtin templates/agents are customized, why, and the owning ticket, plus a guide for the common cases (add a kind, add an agent, pin a model for a kind, add instruction files). First worked example: FinPlan 'multi-persona reviewers on overseer-runner' (see FinPlan ticket).

## Acceptance Criteria
1. Guide covers add-kind / add-agent / pin-model / add-instruction with copy-paste examples tested in CI.
2. `ao templates` lists customizations.
3. FinPlan example reproduced in a test workspace.

## Risks
- Docs must not drift: example snippets exercised by tests.

## Dependencies
T-ov1a01.

## Handoff Boundary
- Upstream: T-ov1a01.
- Downstream: other tasks in `E-6396jp-workspace-customization-overlays-and-upgrade-flow`

## Artifacts
- Docs/comments: `meta/tickets/E-6396jp-workspace-customization-overlays-and-upgrade-flow/T-ov5e05-workspace-customization-manifest-and-docs/`
- Large outputs: `output/...`
