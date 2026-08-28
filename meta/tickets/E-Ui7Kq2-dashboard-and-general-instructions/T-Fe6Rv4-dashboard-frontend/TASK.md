# TASK: T-Fe6Rv4-dashboard-frontend

## Metadata
- Task ID: `T-Fe6Rv4-dashboard-frontend`
- Epic ID: `E-Ui7Kq2-dashboard-and-general-instructions`
- Owner: `Avadhoot Divekar`
- Created: `2026-07-24`
- Last Updated: `2026-07-24`
- Status: `Done`
- Estimate: `< 3 days`

## Requirements Mapping
- Requirement IDs: `FR-B1..B4, FR-R1..R5`

## Description
Browser UI: runs list with aggregate stats, per-run detail, new-run form with prompt box, file/code browser, and a workspace view of effective general instructions.

## Acceptance Criteria
1. Builds into `src/agent_orchestrator/ui/static/`, committed so `pip install` needs no node.
2. Prompt box is enabled only for workflows declaring `prompt_path`, and explains why when disabled.
3. Cancel is offered for live runs, Resume for interrupted ones; Delete is blocked while live.
4. Status is conveyed by glyph + word + color — never color alone.
5. Light and dark both explicitly styled; OS setting and in-app toggle both honoured.
6. Wide tables scroll inside their container; the page never scrolls horizontally.

## Risks
- See the epic's Risks section; nothing task-specific outstanding.

## Dependencies
- T-Ap9Kf1-dashboard-api

## Schemas / Interface Notes
- `ui/` — package.json, vite.config.ts, vitest.config.ts, tsconfig.json
- `ui/src/` — `App.tsx`, `api.ts`, `types.ts`, `format.ts`, `styles.css`, `components/`

## Handoff Boundary
- Upstream: `T-Ap9Kf1-dashboard-api`
- Downstream: `T-Ts8Nc2-test-suites`

## Evidence
- `ui/src/test/format.test.ts`, `ui/src/test/components.test.tsx` — 25 vitest tests.

## Artifacts
- Docs/comments: `meta/tickets/E-Ui7Kq2-dashboard-and-general-instructions/T-Fe6Rv4-dashboard-frontend/`
- Design doc: [`docs-md/dashboard-and-general-instructions-hld.md`](../../../../docs-md/dashboard-and-general-instructions-hld.md)
