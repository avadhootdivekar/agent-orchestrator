# TASK: T-Sn5Ui1-sentinel-panel-and-control-api

## Metadata
- Task ID: `T-Sn5Ui1-sentinel-panel-and-control-api`
- Epic ID: `E-Sn7tL2-sentinel-long-running-overseer`
- Owner: `unassigned`
- Created: `2026-11-09`
- Last Updated: `2026-11-09`
- Status: `Draft`
- Estimate: `< 3 days`
- Phase: P1
- Agent: developer (UI) + reviewer

## Requirements Mapping
- Requirement IDs: FR-7, FR-20, FR-22, NFR-12

## Description
Backend: `GET /api/runs/{id}/sentinel` (status as in `ao ctl status --json`) and `POST /api/runs/{id}/control`
(builds the same `ao.ctl/v1` entry via shared `control.py`; ADR-0021 auth + CSRF/origin checks; actor = dashboard
principal). Frontend: Sentinel panel v1 on the run page — enable state, powers, live `max_parallel` (limit/ceiling)
control, utilisation gauge + idle-slot timeline, running tasks with liveness age + Kill (confirm + reason), ladder
state, notes box, pause/resume LLM, recent actions. Sentinel settings section in launch/resume forms comes from the
schema-driven form built in E-aYG5vn T-pMpo4O (no hand-written fields). Run page section layout agreed with T-xNFBww.

## Acceptance Criteria
1. API tests: unauthenticated / cross-origin POST rejected; valid POST produces inbox entry with actor.
2. UI tests: panel renders for enabled, disabled and old-backend (404) runs; Kill flow requires reason.
3. Manual check against a temp-workspace run via `ao ui`: change max_parallel live and see dispatch count follow.

## Risks
- Run page clutter — sections collapsible, default collapsed when sentinel disabled.

## Dependencies
- T-Sn3Ctl
- T-Sn4Rec
- E-aYG5vn T-pMpo4O (form)
- E-aYG5vn T-xNFBww (layout)

## Pseudocode / Algorithm
```text
N/A (UI + thin API over control.py)
```

## Schemas / Interface Notes
- `GET /api/runs/{id}/sentinel`
- `POST /api/runs/{id}/control` body = ao.ctl/v1 minus id/actor/created_at

## Handoff Boundary
- Upstream: T-Sn3Ctl, T-Sn4Rec
- Downstream: T-Sn9Pow (panel v2: generations, proposals)

## Artifacts
- Docs/comments: `meta/tickets/E-Sn7tL2-sentinel-long-running-overseer/T-Sn5Ui1-sentinel-panel-and-control-api/`
- Large outputs: `N/A`

## Standard gates (every task)
- `pytest -q`, `ruff check . && ruff format --check .`, `mypy .` green; UI changes also `npm test` + `npm run build` in `ui/`.
- New behaviour off / absent settings ⇒ byte-identical behaviour (golden tests stay green).
- Work only in `./agent-orchestrator` on the epic branch; test against temp workspaces; never `install.sh` / restart `ao.service`.
- Update this TASK.md + STATUS.md and the epic STATUS.md rollup together.
