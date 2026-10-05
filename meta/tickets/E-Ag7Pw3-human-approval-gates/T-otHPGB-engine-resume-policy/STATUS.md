# STATUS

- ID: `T-otHPGB-engine-resume-policy`
- Updated At: 2026-10-05
- State: Draft — Not started
- Owner: developer

## This update
- By: architect · Role: agent · Date: 2026-10-04 · Comment: Ticket created from HLD §9.9, §9.10.6.
- By: architect · Role: agent · Date: 2026-10-05 · Comment: Gate 1 revision (dev-security FAIL items):
  resume integrity rebuilt — resume detected from `run_state is not None` (S-01, replaces rev 1's
  `spec_sessions` rule and its `test_library_fresh_state_is_not_treated_as_resume`), per-status gate
  normalisation incl. gate ancestors (S-02 + NC-1), fail-closed missing policy with gate evidence (S-03),
  gate-scoped policy checks instead of the whole-DAG freeze (R-03, OQ-2 modified), loop-clone
  re-derivation and the static base spec for clones (R-04), request binding check (NC-3). Pure helpers
  moved to `T-1MgGb4`, so the estimate stays 3 days.

## Evidence
- None yet.

## Risks / Blockers
- Waits for `T-vwIpSw` (and `T-1MgGb4`'s helpers).

## Next actions
1. `begin_session` in the order of HLD §9.10.6 (policy → clones → statuses), then the re-entry branches.
2. Remove `T-vwIpSw`'s interim fail-closed stub and its temporary test.
