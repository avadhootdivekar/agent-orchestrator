# TASK: T-Rm5Jd7-roadmap-and-docs

## Metadata
- Task ID: `T-Rm5Jd7-roadmap-and-docs`
- Epic ID: `E-Ui7Kq2-dashboard-and-general-instructions`
- Owner: `Avadhoot Divekar`
- Created: `2026-07-24`
- Last Updated: `2026-07-24`
- Status: `Done`
- Estimate: `< 3 days`

## Requirements Mapping
- Requirement IDs: `NFR-4, NFR-5`

## Description
Project-level documentation and tooling: the roadmap file the user asked for, plus the design record and CI/Make wiring for the new subsystem.

## Acceptance Criteria
1. `meta/ROADMAP.md` — current status summary + general long-term direction + a 3–6 month view with some detail, explicitly not ticket-by-ticket.
2. Deferred items (UI workflow generation, auth/secrets) recorded on the roadmap rather than dropped.
3. HLD covering both features and the reasoning behind each design decision.
4. ADR-0010 recording the 7 decisions, including the deliberate ADR-0003 precedence exception.
5. CI installs the `ui` extra, runs the dashboard tests with a coverage gate, and adds a node job (typecheck, test, build, audit).
6. Makefile `ui`, `ui-build`, `ui-dev`, `ui-test`, `test-ui` targets; README section.

## Risks
- See the epic's Risks section; nothing task-specific outstanding.

## Dependencies
- T-Ts8Nc2-test-suites

## Schemas / Interface Notes
- `meta/ROADMAP.md`
- `docs-md/dashboard-and-general-instructions-hld.md`
- `docs-md/adr/ADR-0010-dashboard-architecture-and-general-instructions.md`
- `.github/workflows/ci.yml`
- `Makefile`
- `README.md`

## Handoff Boundary
- Upstream: `T-Ts8Nc2-test-suites`
- Downstream: `Epic closure`

## Evidence
- Docs reviewed against the as-built implementation; CI config validated by running each gate locally.

## Artifacts
- Docs/comments: `meta/tickets/E-Ui7Kq2-dashboard-and-general-instructions/T-Rm5Jd7-roadmap-and-docs/`
- Design doc: [`docs-md/dashboard-and-general-instructions-hld.md`](../../../../docs-md/dashboard-and-general-instructions-hld.md)
