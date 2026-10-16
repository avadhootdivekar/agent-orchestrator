# TASK: T-ag8g09-overseer-unit-effort-override

## Metadata
- Task ID: `T-ag8g09-overseer-unit-effort-override`
- Epic ID: `E-79one2-agent-composition-and-model-governance`
- Owner: `TODO (unassigned)`
- Created: `2026-10-16`
- Last Updated: `2026-10-16`
- Status: `Draft`
- Priority: `P3`
- Estimate: `< 3 days` (split further if discovery says otherwise)

## Requirements Mapping
- Requirement IDs: see EPIC.md

## Description
Unit `model` override is already gated by `allowed_models` (brief.model, OV-R9m). Add the same for `effort`: brief.effort > kind_map.effort > default, validated against an allow-list.

## Acceptance Criteria
1. OV-R9 family check for effort with allow-list.
2. Docs and tests mirroring the model rule.

## Risks
- Keep deterministic single precedence rule.

## Dependencies
None.

## Handoff Boundary
- Upstream: None.
- Downstream: other tasks in `E-79one2-agent-composition-and-model-governance`

## Artifacts
- Docs/comments: `meta/tickets/E-79one2-agent-composition-and-model-governance/T-ag8g09-overseer-unit-effort-override/`
- Large outputs: `output/...`
