# TASK: T-Ap9Kf1-dashboard-api

## Metadata
- Task ID: `T-Ap9Kf1-dashboard-api`
- Epic ID: `E-Ui7Kq2-dashboard-and-general-instructions`
- Owner: `Avadhoot Divekar`
- Created: `2026-07-24`
- Last Updated: `2026-07-24`
- Status: `Done`
- Estimate: `< 3 days`

## Requirements Mapping
- Requirement IDs: `FR-B1..B4, FR-R1..R5, NFR-2, NFR-5`

## Description
Expose `DashboardService` over HTTP and add the `ao ui` command that serves it.

## Acceptance Criteria
1. Thin routes mapping HTTP onto service methods, with correct status codes: 403 traversal, 404 missing, 409 wrong state, 400 bad request, 422 schema violation.
2. `/api/runs/stats` resolves ahead of `/api/runs/{run_id}`.
3. Unknown `/api/*` paths return a JSON 404, never the SPA fallback.
4. `ao ui` binds loopback by default and warns loudly when binding anything else.
5. Missing frontend build degrades to a 503 with build instructions; the API stays usable.
6. `fastapi`/`uvicorn` live behind the optional `[ui]` extra; a missing extra produces actionable install advice, not a traceback.

## Risks
- See the epic's Risks section; nothing task-specific outstanding.

## Dependencies
- T-Db2Hs5-dashboard-backend

## Schemas / Interface Notes
- `ui/app.py` — `create_app(service)`, `create_app_from_env()`, `API_PREFIX`, `STATIC_DIR`
- `cli.ui_cmd` — `--host`/`--port`/`--workspace`/`--reload`/`--open`
- `pyproject.toml` → `[project.optional-dependencies] ui`

## Handoff Boundary
- Upstream: `T-Db2Hs5-dashboard-backend`
- Downstream: `T-Fe6Rv4-dashboard-frontend`

## Evidence
- `tests/ui/test_api_integration.py` (37), `tests/ui/test_ui_command.py` (18).

## Artifacts
- Docs/comments: `meta/tickets/E-Ui7Kq2-dashboard-and-general-instructions/T-Ap9Kf1-dashboard-api/`
- Design doc: [`docs-md/dashboard-and-general-instructions-hld.md`](../../../../docs-md/dashboard-and-general-instructions-hld.md)
