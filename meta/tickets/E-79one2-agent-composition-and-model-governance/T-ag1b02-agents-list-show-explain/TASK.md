# TASK: T-ag1b02-agents-list-show-explain

## Metadata
- Task ID: `T-ag1b02-agents-list-show-explain`
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
Introspection for layered agents: fully-resolved spec per agent, the source file of every field, model, tools; `diff` between two agents or two layer stacks. Optional dashboard panel.

## Acceptance Criteria
1. `ao validate --explain-agent <name>` prints the resolved spec with per-field provenance.
2. `ao agents list|show|diff` work against the same loader as the engine.
3. Output stable enough for golden tests.

## Risks
- Keep output machine-readable (`--json`).

## Dependencies
Depends on T-ag1a01 (the loader).

## Handoff Boundary
- Upstream: Depends on T-ag1a01 (the loader).
- Downstream: other tasks in `E-79one2-agent-composition-and-model-governance`

## Artifacts
- Docs/comments: `meta/tickets/E-79one2-agent-composition-and-model-governance/T-ag1b02-agents-list-show-explain/`
- Large outputs: `output/...`
