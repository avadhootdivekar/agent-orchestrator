# STATUS

- ID: `T-ZPGoSN-consumer-surfaces-accounting`
- Updated At: 2026-10-05
- State: Draft — Not started
- Owner: developer

## This update
- By: architect · Role: agent · Date: 2026-10-04 · Comment: Ticket created from HLD §9.2.4–§9.2.6.
- By: architect · Role: agent · Date: 2026-10-05 · Comment: Gate 1 revision: `approvals/accounting.py`
  moved to `T-drPIif` (views need it earlier); the run-level wait is the union of waiting intervals with
  the wall-time end marker, so it never exceeds wall time (R-07, NC-6); `current_task` keeps the existing
  rule and only falls back to an awaiting gate (suggestion S-08 modified, T-15); `cli.py` work starts
  after `T-nmL0HP`'s registration commit. Estimate 2 d → 1.5 d.

## Evidence
- None yet.

## Risks / Blockers
- Waits for `T-vwIpSw`, `T-drPIif` and `T-nmL0HP`'s registration commit.

## Next actions
1. Record the gate-free `ao status` golden first, then the surface edits and `test_surfaces.py`.
