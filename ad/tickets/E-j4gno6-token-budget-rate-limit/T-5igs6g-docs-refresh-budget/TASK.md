# TASK: T-5igs6g-docs-refresh-budget

## Metadata
- Task ID: `T-5igs6g-docs-refresh-budget`
- Epic ID: `E-j4gno6-token-budget-rate-limit`
- Owner: TODO
- Created: 2026-06-18
- Last Updated: 2026-06-18
- Status: Draft
- Estimate: < 1 day

## Requirements Mapping
- Requirement IDs: all (documentation reconciliation)

## Description
Post-implementation reconciliation. After all implementation + test tasks are green,
update `docs-md/token-budgeting-hld.md` so it matches the **shipped** behavior
(including any deviations from this design), resolve the epic's OPEN_QUESTIONs with
the answers learned during implementation, and add/confirm ADRs.

Specifically:
- Reconcile the schema section with the final `workflow.schema.json` `budget` block.
- Reconcile the gate/charge/reconcile/wait flow diagram with the final engine code.
- Record the **actual** `claude --output-format json` `usage` shape that `T-1m9744` confirmed (replace the assumed fixture in the doc).
- Resolve OPEN_QUESTIONs: cache_read discounting decision, max-wait policy, window model (tumbling).
- Update the `docs-md/` index/related docs (cross-link from `hld-agent-orchestrator.md` / `lld-agent-orchestrator.md` if they enumerate features; follow the pattern used by `logging-dynamic-workflows-hld.md`).
- Add/confirm ADRs (ADR-BUD-001..005) in the doc or `docs-md/adr/` consistent with the project's ADR pattern.

## Acceptance Criteria
1. `docs-md/token-budgeting-hld.md` describes the as-built schema, flow, and fallbacks; any deviation from the original design is called out in a "Deviations from design" subsection.
2. Every epic OPEN_QUESTION is resolved (answer recorded) or explicitly carried forward as a tracked follow-up.
3. The recorded `usage` JSON shape matches the fixture used by the shipped parser (`tests/fixtures/claude_usage.json`).
4. The `docs-md/` index / related HLD/LLD docs cross-link the new doc following the existing logging-epic pattern; no broken links.
5. ADRs for the major decisions (config location, hybrid accounting, tumbling window, stop-default + terminal status, provider-429-as-same-handler) are present and consistent with the code. Marked Done only after a final `pytest -q` confirms docs match behavior.

## Risks
- Docs drifting from code if written before tests are green — gate this task on T-67kiia green (do not start earlier).

## Dependencies
- Upstream: T-67kiia (and all implementation tasks).
- Downstream: epic close.

## Pseudocode / Algorithm
```text
N/A — documentation reconciliation task.
```

## Schemas / Interface Notes
- Interface / API: N/A.
- Spec / data schema: reconcile documented `budget` block with shipped `workflow.schema.json`.
- Triggers / events: document the `budget.*` event vocabulary as shipped.
- Artifacts: `docs-md/token-budgeting-hld.md`, `docs-md/adr/` (if used), index updates.

## Handoff Boundary
- Upstream: shipped + tested implementation.
- Downstream: epic closure; future maintainers read the reconciled HLD.
