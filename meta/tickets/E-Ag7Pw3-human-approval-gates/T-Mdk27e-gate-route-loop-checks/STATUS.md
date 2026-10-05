# STATUS

- ID: `T-Mdk27e-gate-route-loop-checks`
- Updated At: 2026-10-05
- State: Draft — Not started
- Owner: developer

## This update
- By: architect · Role: agent · Date: 2026-10-05 · Comment: Ticket created in rev 3 (Gate 2 R-10): W-AG-7
  (route exposure) moved off the critical chain into `approvals/spec_warnings.py`, plus the engine-level
  routes/loops/manifest integration tests moved from `T-otHPGB` so that ticket can absorb the gated-marker
  work at 3 days. W-AG-8 is withdrawn (documentation only), the option Gate 2 allowed. 1 day, stage E.

## Evidence
- None yet.

## Risks / Blockers
- Waits for `T-AGO2L6`, `T-1MgGb4` and `T-vwIpSw`.

## Next actions
1. `spec_warnings.py` + W-AG-7 wiring + tests, then `test_engine_loops_routes.py`.
