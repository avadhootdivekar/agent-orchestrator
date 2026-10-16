# TASK: T-ag1a01-composable-agent-definitions

## Metadata
- Task ID: `T-ag1a01-composable-agent-definitions`
- Epic ID: `E-79one2-agent-composition-and-model-governance`
- Owner: `TODO (unassigned)`
- Created: `2026-10-16`
- Last Updated: `2026-10-16`
- Status: `Draft`
- Priority: `P1`
- Estimate: `< 3 days` (split further if discovery says otherwise)

## Requirements Mapping
- Requirement IDs: see EPIC.md

## Description
Allow a project to add or override agents without forking the whole `agents.json`.
- `--agents` repeatable (and `agents:` list in config); later wins per key; deep-merge on `AgentSpec` fields; list fields replaced unless `"$append": true`.
- Per-agent `"extends": "<agent-name>"` with field overrides (collapses ~12 near-identical `command_template` copies).
- `agents.d/*.json` directory form, lexical order.
- Single-file form keeps working; schema version bump with migration note.
- Must respect the strict invariants of `agents.recommended.json.tmpl` (only `extra_args`, no `command_template`).

## Acceptance Criteria
1. Two agent files merge deterministically; documented precedence.
2. An agent with `extends` yields a spec identical to the hand-expanded one (golden test).
3. `agents.d/` loads in lexical order; duplicate names resolved by documented rule.
4. Existing single `agents.json` workflows run unchanged; no new warnings become errors by default.
5. Docs + schema + migration note updated.

## Risks
- Interaction with `forbidden_task_models` and the recommended-template invariants must be specified up front.

## Dependencies
None.

## Handoff Boundary
- Upstream: None.
- Downstream: other tasks in `E-79one2-agent-composition-and-model-governance`

## Artifacts
- Docs/comments: `meta/tickets/E-79one2-agent-composition-and-model-governance/T-ag1a01-composable-agent-definitions/`
- Large outputs: `output/...`
