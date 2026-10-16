# EPIC: E-6396jp-workspace-customization-overlays-and-upgrade-flow

## Metadata
- Epic ID: `E-6396jp-workspace-customization-overlays-and-upgrade-flow`
- Title: `Workspace customization overlays and upgrade flow`
- Owner: `TODO (unassigned)`
- Created: `2026-10-16`
- Last Updated: `2026-10-16`
- Status: `Draft`

## Re-triage (2026-10-16, after owner review)
The operator asked for Claude skills in the self-hosting workspace instead of new `ao` features:
`ao-runner-ao/.claude/skills/ao-upgrade` and `ao-overlay-sync` (human-invoked) now do install + dashboard verification
+ three-way overlay sync with `git merge-file`. So most of this epic is demoted: build tool features only if the skills
prove insufficient. Key finding that stays valid: workspace copies of template `instructions/` are `keep_existing`
assets, so upstream fixes never reach them (silent staleness) -- T-ov2b02 (baseline hashes) is the one cheap, worthwhile tool change.

## Summary
- Goal: Let each workspace customize existing builtin workflows/agents (e.g. FinPlan's multi-reviewer overseer) without forking, and have every `ao` update carry customizations forward with conflicts surfaced and dashboards verified.
- Scope In: Overlay layer, drift detection, conflict resolution, scripted self-update with dashboard refresh and health checks, customization manifest/docs.
- Scope Out: Auto-modifying running runs; auto-merging conflicts without human review; changing the self-hosting rule that runs never install.

## Requirements
- FR-1: Customize a builtin template via overlay, no fork.
- FR-2: Upgrades flow into overlays; drift and conflicts reported per workspace.
- FR-3: One human-invoked command to install and verify all dashboards.
- NFR-1: Existing shadowing templates keep working.
- NFR-2: Safe: refuses with active runs; never callable from a run.

## Task List
- [ ] `T-ov1a01-template-overlay-layer` — Per-workspace overlay on a builtin template (customize, don't fork) (priority: P2, reframed: overlay for per-run config + instructions; revisit after the skill has been used)
- [ ] `T-ov2b02-template-version-stamp-and-drift` — Version-stamp scaffolds and detect drift on upgrade (priority: P2, reframed: baseline hashes for keep_existing assets)
- [ ] `T-ov3c03-overlay-conflict-resolution-flow` — Three-way conflict resolution for overlays (priority: P3 backlog; skill uses git merge-file)
- [ ] `T-ov4d04-ao-self-update-command` — One command to install the new `ao` safely and refresh every dashboard (priority: P3 backlog; superseded by /ao-upgrade skill)
- [x] `T-ov5e05-workspace-customization-manifest-and-docs` — Workspace customization manifest and guide (SUPERSEDED by the skills; close)

## Risks and Dependencies
- Depends loosely on E-79one2 T-ag1a01 (agent composition) for agent additions.
- Suggested order: T-ov1a01, T-ov2b02, T-ov4d04, T-ov3c03, T-ov5e05.

## Links
- Motivation: operator request 2026-10-16 (use overseer-runner in FinPlan with the multi-reviewer customization; keep it current across `ao` updates).
- Related: `docs-md/workflow-templates*`, `docs-md/multi-workspace-service-hld.md`, `docs-md/install-flavors.md`.
