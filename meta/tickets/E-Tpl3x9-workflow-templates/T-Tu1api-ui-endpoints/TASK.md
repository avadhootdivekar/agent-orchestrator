# TASK: T-Tu1api-ui-endpoints

## Metadata
- Task ID: `T-Tu1api-ui-endpoints`
- Epic ID: `E-Tpl3x9-workflow-templates`
- Owner: `claude`
- Created: `2026-07-24`
- Last Updated: `2026-07-24`
- Status: `Done`
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
- Code: `src/agent_orchestrator/ui/service.py` (`list_templates`, `create_instance`,
  `_derive_slug_from_prompt`), `src/agent_orchestrator/ui/app.py` (`GET /api/templates`,
  `POST /api/templates/{name}/instances`), `tests/ui/test_templates_api.py`,
  `tests/ui/conftest.py` (`write_template` helper).

## Comments
- By: claude · Role: developer · Date: 2026-07-24 · Comment: Implemented and verified —
  see STATUS.md for full evidence. Two judgment calls flagged there: (1) 404/409/400
  status mapping uses message-prefix constants (`TEMPLATE_NOT_FOUND_PREFIX`,
  `PROMPT_CONFLICT_PREFIX`) defined in `service.py` rather than raw substring checks, to
  avoid a real collision I found between `templates.load_template`'s "unknown template
  'x'" (404) and `templates._render`'s "unknown template variable" (400) messages; (2)
  `start: true` launches via `ProcessSupervisor.launch_run` exactly as instructed, with no
  additional pre-flight `ao validate`-equivalent call beyond what `instantiate()` already
  performs internally — matching `start_run`'s existing validation depth, per the task's
  explicit "exactly like start_run does" wording.
