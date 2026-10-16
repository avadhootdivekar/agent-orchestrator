# TASK: T-ag9h10-ao-plan-dry-run

## Metadata
- Task ID: `T-ag9h10-ao-plan-dry-run`
- Epic ID: `E-79one2-agent-composition-and-model-governance`
- Owner: `TODO (unassigned)`
- Created: `2026-10-16`
- Last Updated: `2026-10-16`
- Status: `Draft`
- Priority: `P3 (backlog)`
- Estimate: `< 3 days` (split further if discovery says otherwise)

## Requirements Mapping
- Requirement IDs: see EPIC.md

## Description
Show the exact argv, model, cwd and tool set each task would use without spawning anything. First check how much `ao validate` / `--validate-only` already cover; only build the delta.

## Acceptance Criteria
1. Gap analysis vs existing validate output recorded first.
2. If built: prints argv/model/cwd/tools per task, no processes spawned.

## Risks
- May be mostly covered; close as duplicate if so.

## Dependencies
T-ag1a01, T-ag3b04 make it more useful.

## Handoff Boundary
- Upstream: T-ag1a01, T-ag3b04 make it more useful.
- Downstream: other tasks in `E-79one2-agent-composition-and-model-governance`

## Artifacts
- Docs/comments: `meta/tickets/E-79one2-agent-composition-and-model-governance/T-ag9h10-ao-plan-dry-run/`
- Large outputs: `output/...`
