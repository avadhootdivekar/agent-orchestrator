# STATUS

- ID: `T-pIZq3q-dashboard-approvals-frontend`
- Updated At: 2026-10-05
- State: Draft — Not started
- Owner: developer

## This update
- By: architect · Role: agent · Date: 2026-10-04 · Comment: Ticket created from HLD §9.15.
- By: architect · Role: agent · Date: 2026-10-05 · Comment: Gate 1 revision: "Approval wait" stat next to
  wall time in `RunDetail` + `run-approval-wait.test.tsx` (R-07); banners keyed on the refusal `reason`
  (R-08), incl. `already_decided`; `ApiError.detailCode`; live "waiting since" client-side. Still 3 days
  (at the cap). Scheduled to start at day 10 to keep Sprint 1 within capacity (HLD §22.2).

## Evidence
- None yet.

## Risks / Blockers
- Waits for `T-l43hCg`.

## Next actions
1. Types/api/helpers first (unit-tested), then components and the wait stat, then the bundle rebuild.
