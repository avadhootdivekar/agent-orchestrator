# TASK: T-Ui5Ij6-dashboard-usage-feedback

## Metadata
- Task ID: `T-Ui5Ij6-dashboard-usage-feedback`
- Epic ID: `E-Us9Kd4-usefulness-signals`
- Owner: dev-epic (delegated to developer subagent)
- Created: 2026-10-02
- Last Updated: 2026-10-02
- Status: `Done`
- Estimate: `< 3 days`

## Requirements Mapping
- Requirement IDs: `FR-9,FR-10,NFR-4`

## Description
Dashboard: feedback endpoints/UI, Usage view, signals panel. Contract and algorithms: `docs-md/usage-signals-hld.md`.

## Acceptance Criteria
See the epic EPIC.md traceability table and the HLD section for this part; each criterion has a pytest check.

## Handoff Boundary
- Upstream / Downstream: see EPIC.md task order.

## Completion (By: dev-epic | Role: manager | Date: 2026-10-02)
Backend routes + frontend (Usage tab, rating controls, signals panel) delivered; security review (dev-security) found 0 critical / 1 medium / 4 low, all fixed (see STATUS "Security hardening"). Evidence: full pytest 4692 passed / 8 skipped; vitest 101 passed; live `ao ui` smoke (feedback POST 201, cross-origin 403, bad rating 422, CLI `ao rate --show` sees dashboard entry, /api/usage joins it).
