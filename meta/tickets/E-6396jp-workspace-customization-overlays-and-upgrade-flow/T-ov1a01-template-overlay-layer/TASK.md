# TASK: T-ov1a01-template-overlay-layer

## Metadata
- Task ID: `T-ov1a01-template-overlay-layer`
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
Re-triage 2026-10-16: demoted. A manual skill (`/ao-overlay-sync`) plus an added-files + `post-scaffold.sh` convention covers it today. Build this only if the manual flow proves error-prone. Not required for the FinPlan persona-review work.

## Description
Today a workspace template with the same name shadows a builtin wholesale (`docs-md/workflow-templates*`), so customizing e.g. `overseer-runner` for FinPlan means forking it and then silently missing every upstream fix. Add an overlay mechanism: a workspace declares `overlay_of: <builtin>` plus a small set of patches, applied on top of the current builtin at scaffold time:
- add/override files (extra instruction files, contract fragments),
- config-fragment merges (e.g. extra `kind_map` entries such as `persona-review` with a pinned model; extra params with defaults),
- agent additions (via composition, see E-79one2 T-ag1a01),
- explicit 'replace file X' only when unavoidable (tracked as a conflict-prone patch).
The overlay lives in the workspace (e.g. `.ao/overlays/<template>/`) so it is versioned with the workspace, and is applied identically by CLI, dashboard launcher and `--validate-only`.

## Acceptance Criteria
1. A workspace overlay on `overseer-runner` adding one kind_map entry and one instruction file scaffolds a run containing builtin + customization, without copying the builtin.
2. Upgrading `ao` changes the builtin part of new runs automatically; the overlay part is unchanged.
3. Resolution order and precedence documented; `ao templates` shows overlaid templates and what they patch.
4. Existing full-shadow workspace templates keep working.

## Risks
- Highest-value ticket; define merge semantics for JSON/YAML fragments vs text files carefully (per-file strategy: append, merge, replace).

## Dependencies
None (soft: E-79one2 T-ag1a01 for agent additions).

## Handoff Boundary
- Upstream: None (soft: E-79one2 T-ag1a01 for agent additions).
- Downstream: other tasks in `E-6396jp-workspace-customization-overlays-and-upgrade-flow`

## Artifacts
- Docs/comments: `meta/tickets/E-6396jp-workspace-customization-overlays-and-upgrade-flow/T-ov1a01-template-overlay-layer/`
- Large outputs: `output/...`
