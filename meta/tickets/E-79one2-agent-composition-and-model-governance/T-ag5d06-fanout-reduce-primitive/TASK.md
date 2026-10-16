# TASK: T-ag5d06-fanout-reduce-primitive

## Metadata
- Task ID: `T-ag5d06-fanout-reduce-primitive`
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
DAG primitive: run N tasks, then one consolidator receiving only their output paths; proceed when at least K of N succeeded and record the gaps. Overseer runs currently do this by convention; make it deterministic and visible.

## Acceptance Criteria
1. Consolidator starts when >= K of N succeeded; gaps are written to a machine-readable list.
2. K and N surfaced in the UI.
3. Works with dynamic injection.

## Risks
- Interaction with `skip_if_outputs_exist` and runaway-fanout breaker.

## Dependencies
Soft dependency on T-ag7f08 (concurrency cap).

## Handoff Boundary
- Upstream: Soft dependency on T-ag7f08 (concurrency cap).
- Downstream: other tasks in `E-79one2-agent-composition-and-model-governance`

## Artifacts
- Docs/comments: `meta/tickets/E-79one2-agent-composition-and-model-governance/T-ag5d06-fanout-reduce-primitive/`
- Large outputs: `output/...`
