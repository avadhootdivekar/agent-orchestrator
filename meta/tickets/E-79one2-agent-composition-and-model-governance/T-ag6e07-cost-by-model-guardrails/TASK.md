# TASK: T-ag6e07-cost-by-model-guardrails

## Metadata
- Task ID: `T-ag6e07-cost-by-model-guardrails`
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
Run-level guard rails for cheap-quantity workflows: cap spend per model, alert when spend on a more expensive model than planned exceeds a threshold. Existing run and task budgets must keep applying.

## Acceptance Criteria
1. Cap per model enforced like the existing budget breaker.
2. Alert/breaker fires when the expensive-model share exceeds the configured threshold.
3. Shown in report/UI.

## Risks
- Pricing source must be the existing price table; no hard-coded prices.

## Dependencies
T-ag2a03 (planned vs actual model data).

## Handoff Boundary
- Upstream: T-ag2a03 (planned vs actual model data).
- Downstream: other tasks in `E-79one2-agent-composition-and-model-governance`

## Artifacts
- Docs/comments: `meta/tickets/E-79one2-agent-composition-and-model-governance/T-ag6e07-cost-by-model-guardrails/`
- Large outputs: `output/...`
