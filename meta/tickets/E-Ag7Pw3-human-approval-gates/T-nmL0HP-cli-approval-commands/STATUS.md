# STATUS

- ID: `T-nmL0HP-cli-approval-commands`
- Updated At: 2026-10-05
- State: Draft — Not started
- Owner: developer

## This update
- By: architect · Role: agent · Date: 2026-10-04 · Comment: Ticket created from HLD §9.13.
- By: architect · Role: agent · Date: 2026-10-05 · Comment: Gate 1 revision: `list`/`show` now consume
  `approvals/views.py` (R-05); exit codes come from the `REFUSAL_EXIT_CODE` table, new exit 11 for
  invalid comments, `already_decided` → 6 (R-08, suggestion S-06); e2e key dir from
  `tmp_path_factory.mktemp` outside the workspace (R-02); registration lands as the first commit so
  `T-ZPGoSN` can follow on `cli.py`. Estimate 3 d → 2.5 d; scheduled at day 10 (HLD §22.2).

## Evidence
- None yet.

## Risks / Blockers
- Waits for `T-pfJiXw` (signer/views), `T-1B8hu4`; e2e waits for `T-vwIpSw`.

## Next actions
1. Registration commit in `cli.py`, then commands over views/signer, then unit tests, then the e2e.
