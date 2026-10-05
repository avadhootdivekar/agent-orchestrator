# STATUS

- ID: `T-1B8hu4-agent-marker-spawn-sites`
- Updated At: 2026-10-05
- State: Draft — Not started
- Owner: developer

## This update
- By: architect · Role: agent · Date: 2026-10-04 · Comment: Ticket created from HLD §9.11.
- By: architect · Role: agent · Date: 2026-10-05 · Comment: Gate 1 revision: `GitRepo.version`/`probe`
  now marked (S-10), so the count is 8 edit sites / 10 spawn points (suggestion S-03 wording updated); the
  marker must be the last env assignment at every site, with an `AO_IN_AGENT: "0"` overlay test per site and
  a regenerate-resolver test (S-03); `GitRepo._run` sets it after `extra_env` instead of via `_FORCED_ENV`.
  Estimate 1 d → 1.5 d; starts only after `T-AGO2L6` has merged (`spec.py`).

## Evidence
- None yet.

## Risks / Blockers
- Waits for `T-AGO2L6` (package skeleton, `spec.py`).

## Next actions
1. Implement `approvals/marker.py`, then the 8 site edits with one spy test per spawn point and one
   overlay test per overlay-accepting site.
