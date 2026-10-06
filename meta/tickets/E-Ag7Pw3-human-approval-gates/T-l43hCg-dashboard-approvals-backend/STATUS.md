# STATUS

- ID: `T-l43hCg-dashboard-approvals-backend`
- Updated At: 2026-10-05
- State: Draft — Not started
- Owner: developer

## This update
- By: architect · Role: agent · Date: 2026-10-04 · Comment: Ticket created from HLD §9.14.
- By: architect · Role: agent · Date: 2026-10-05 · Comment: Gate 1 revision: the service is a thin
  adapter over `approvals/views.py` and the signer (R-05); hash cache, semaphore and 429 removed (CUT 2);
  statuses from the one refusal mapping table, `already_decided` 409 (R-08, suggestion S-06); file-browser
  denial of run `approvals/` trees in `ui/files.py` (S-06, OQ-14, CE-4); test key dir outside the
  `tmp_path` workspace (R-02). Estimate 3 d → 2.5 d.

## Evidence
- None yet.

## Risks / Blockers
- Waits for `T-pfJiXw` and `T-1B8hu4`.

## Next actions
1. Principal reader, service, routes, then `files.py` denial and the API tests.
