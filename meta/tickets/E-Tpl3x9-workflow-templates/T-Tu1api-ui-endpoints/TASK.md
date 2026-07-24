# TASK: T-Tu1api-ui-endpoints

## Metadata
- Task ID: `T-Tu1api-ui-endpoints`
- Epic ID: `E-Tpl3x9-workflow-templates`
- Owner: `claude`
- Created: `2026-07-24`
- Last Updated: `2026-07-24`
- Status: `In Progress`
- Estimate: `< 3 days`

## Requirements Mapping
- See epic E-Tpl3x9 FR/NFR listed in description.

## Description
DashboardService.list_templates/create_instance + GET /api/templates + POST /api/templates/{name}/instances with optional start, integration tests with stub supervisor. Maps FR-5. Interface: HLD §2.6. Depends: T-Tc0r3a.

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
- Docs/comments: meta/tickets/E-Tpl3x9-workflow-templates/T-Tu1api-ui-endpoints/
