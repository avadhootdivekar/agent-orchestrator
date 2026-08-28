# TASK: T-Tb3rtr-builtin-routed-runner

## Metadata
- Task ID: `T-Tb3rtr-builtin-routed-runner`
- Epic ID: `E-Tpl3x9-workflow-templates`
- Owner: `claude`
- Created: `2026-07-24`
- Last Updated: `2026-07-24`
- Status: `Done`
- Estimate: `< 3 days`

## Requirements Mapping
- See epic E-Tpl3x9 FR/NFR listed in description.

## Description
Built-in finplan-free routed-runner template under src/agent_orchestrator/templates/builtin/: template.yaml, workflow.json.tmpl (+prompt_path), prompt/breakdown tmpls, 31 generalized instructions, packaging include + static sanity tests. Maps FR-7. Interface: HLD §2.2/§2.8.

## Acceptance Criteria
1. Contracts in docs-md/workflow-templates-hld.md honored exactly.
2. Tests added and passing; no regressions in existing suites.

## Risks
- See epic Risks section.

## Dependencies
- docs-md/workflow-templates-hld.md (authoritative contracts).

## Handoff Boundary
- Upstream/Downstream: per epic task list ordering.

## Artifacts
- Docs/comments: meta/tickets/E-Tpl3x9-workflow-templates/T-Tb3rtr-builtin-routed-runner/
