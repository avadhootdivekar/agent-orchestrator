# TASK: T-Ts8Nc2-test-suites

## Metadata
- Task ID: `T-Ts8Nc2-test-suites`
- Epic ID: `E-Ui7Kq2-dashboard-and-general-instructions`
- Owner: `Avadhoot Divekar`
- Created: `2026-07-24`
- Last Updated: `2026-07-24`
- Status: `Done`
- Estimate: `< 3 days`

## Requirements Mapping
- Requirement IDs: `NFR-4`

## Description
Test coverage at every level for the dashboard and general instructions, including a true end-to-end tier.

## Acceptance Criteria
1. Unit: service layer, file browser, run repository, supervisor — no server needed.
2. Integration: real FastAPI routing via `TestClient` over a temp workspace.
3. E2E: a real uvicorn server in a subprocess, driven over real HTTP, launching a real `ao run` child — prompt written, run completes, stats update, run deletes.
4. E2E (CLI): `--prompt`, `--prompt-file`, `--general-instruction`, env and config layers via `CliRunner`.
5. Frontend unit tests with mocked fetch.
6. No regressions; coverage maintained or improved.

## Risks
- See the epic's Risks section; nothing task-specific outstanding.

## Dependencies
- T-Fe6Rv4-dashboard-frontend

## Schemas / Interface Notes
- `tests/ui/conftest.py` — `StubSupervisor`, `make_run_state`, `write_run` (pinned clock)

## Handoff Boundary
- Upstream: `T-Fe6Rv4-dashboard-frontend`
- Downstream: `T-Rm5Jd7-roadmap-and-docs`

## Evidence
- 1528 passed overall (was 1267); coverage 95% vs 94% baseline. Two real defects found and fixed: zombie-process liveness, and the SPA swallowing `/api/*` 404s.

## Artifacts
- Docs/comments: `meta/tickets/E-Ui7Kq2-dashboard-and-general-instructions/T-Ts8Nc2-test-suites/`
- Design doc: [`docs-md/dashboard-and-general-instructions-hld.md`](../../../../docs-md/dashboard-and-general-instructions-hld.md)
