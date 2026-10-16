# TASK: T-ag3b04-agent-tool-and-sandbox-profiles

## Metadata
- Task ID: `T-ag3b04-agent-tool-and-sandbox-profiles`
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
Per-agent `tools: [...]`, `permission_mode`, `strict_mcp`, `empty_cwd`/packet-only working dir, `setting_sources: []` translated to the right Claude CLI flags, so 'read-only reviewer' and 'code-blind reviewer' are properties, not prompt wishes. Driven by the FinPlan review-pool (technical/domain = Read,Grep,Glob; end-user = no repo access).

## Acceptance Criteria
1. A profile with `tools: [Read,Grep,Glob]` produces the matching argv; an attempt to Write fails (real-CLI smoke test).
2. `empty_cwd` runs the agent in an empty or packet-only directory provided per task.
3. `ao validate` flags a profile that conflicts with `--dangerously-skip-permissions` in a hand-written template.
4. Composes with T-ag1a01 (`extends`).

## Risks
- Needs verification of current Claude CLI flags before design; avoid shell-string interpolation (argv only).

## Dependencies
Soft dependency on T-ag1a01.

## Handoff Boundary
- Upstream: Soft dependency on T-ag1a01.
- Downstream: other tasks in `E-79one2-agent-composition-and-model-governance`

## Artifacts
- Docs/comments: `meta/tickets/E-79one2-agent-composition-and-model-governance/T-ag3b04-agent-tool-and-sandbox-profiles/`
- Large outputs: `output/...`
