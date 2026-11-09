# STATUS

- ID: `T-Sn5Ui1-sentinel-panel-and-control-api`
- Updated At: `2026-11-09`
- State: `Done`
- Owner: `developer (ao run o-bi0rvg-sentinel-dashboard-impl, unit w08-01-sentinel-panel-ui)`

## This update
- By: claude-opus-5-5 · Role: architect · Date: 2026-11-09 · Comment: Task created from epic breakdown (owner-approved design).
- By: claude-sonnet-5-5 · Role: developer · Date: 2026-11-09 · Comment: Implemented `GET /runs/{id}/sentinel`, `POST /runs/{id}/control` (+ `GET .../control/{entry}` verdict probe) over shared `control.py` (`validate_request`, `entry_outcome`, `dashboard_actor`); Sentinel panel v1 on the run page; Sentinel group in the schema-driven launch form; `ao ctl status` now fills liveness age, ladder, pause reason, kills in recent actions. As-built: `docs-md/sentinel-hld.md` §12.2.

## Evidence
- See the unit report `workflows/overseer-runner/runs/o-bi0rvg-sentinel-dashboard-impl/outputs/waves/w08/w08-01-sentinel-panel-ui.md` for gate counts. New tests: `tests/test_control_requests.py`, `tests/ui/test_sentinel_api.py`, `tests/ui/test_sentinel_e2e.py`, `tests/auth/test_sentinel_control_auth.py`, `ui/src/test/sentinel-panel.test.tsx`, `ui/src/test/run-detail-sentinel.test.tsx`.

## Risks / Blockers
- Utilisation is shown as a stacked cumulative bar, not a time-series idle-slot timeline (state keeps only cumulative slot-seconds); a real timeline needs a persisted sample series (follow-up).
- Control actions are accepted whenever `state.status == running` (the dashboard cannot tell a crashed run from a live one without a supervisor record); an entry for a dead engine just stays in `inbox` (visible as pending).
- Fixed: `ao ctl kill --no-retry` sent `payload.retry`, engine reads `no_retry`.
- AC3 was verified by an automated engine+API e2e on a temp workspace, not by a manual `ao ui` session.

## Next actions
1. T-Sn9Pow: panel v2 (generations list, proposals); T-Sn7Bkl: backlog release in the panel.
2. Owner: promote via the live-install flow after merge (UI bundle rebuilt in repo only).
