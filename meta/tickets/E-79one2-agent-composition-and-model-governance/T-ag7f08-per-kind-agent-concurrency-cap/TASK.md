# TASK: T-ag7f08-per-kind-agent-concurrency-cap

## Metadata
- Task ID: `T-ag7f08-per-kind-agent-concurrency-cap`
- Epic ID: `E-79one2-agent-composition-and-model-governance`
- Owner: `TODO (unassigned)`
- Created: `2026-10-16`
- Last Updated: `2026-10-16`
- Status: `Draft`
- Priority: `P2`
- Estimate: `< 3 days` (split further if discovery says otherwise)

## Requirements Mapping
- Requirement IDs: see EPIC.md

## Description
A built-in concurrency group (e.g. `max_parallel: 4` for an agent or kind) so a 49-persona review pool cannot stampede regardless of how many units are emitted. Replaces the 'agent templating' half of the original request C: the overseer already fans out one agent x N briefs.

## Acceptance Criteria
1. Cap configurable per agent and per overseer kind; global `max_parallel` still applies as the upper bound.
2. Observable in UI/status.
3. Tests with fake executor.

## Risks
- Must never exceed global `max_parallel`.

## Dependencies
None.

## Handoff Boundary
- Upstream: None.
- Downstream: other tasks in `E-79one2-agent-composition-and-model-governance`

## Artifacts
- Docs/comments: `meta/tickets/E-79one2-agent-composition-and-model-governance/T-ag7f08-per-kind-agent-concurrency-cap/`
- Large outputs: `output/...`
