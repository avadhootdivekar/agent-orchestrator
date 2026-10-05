# STATUS

- ID: `T-QJ1vyQ-auth-csrf-fetch-metadata`
- Updated At: `2026-10-05`
- State: `Draft`
- Owner: `developer` (lane B)
- Scope: `MVP` · Sprint: `S2` · Estimate: `2 d`

## This update
- By: architect · Role: agent · Date: 2026-10-05 · Comment: TASK.md aligned with the HLD v2 cross-check (HLD §28.8): `AUTH_API_PREFIX` is now an HLD §12.6 constant owned by T-kzEzwy; import it.
- By: architect · Role: agent · Date: 2026-10-05 · Comment: Task **created in v2**. It is split out
  of T-G7qByZ, which was over the 3-day cap (developer D-4).
  - It owns HLD §13.3 step 2 (Origin required on every mutation; Fetch Metadata with the navigation
    exemption), step 3 (the auth body cap) and step 5 (session-proof **enforcement**, D25,
    dev-security #2), plus the pure `http/origin.py` with a parity test against `ui/security.py`
    (reviewer R-2, which was partly adopted).
  - T-G7qByZ's hook already *computes* `proof_ok` for the E1/E10 routes. This task adds only the
    enforcement branch.
  - **Gap found:** `AUTH_API_PREFIX`, used in §13.3, is missing from §12.6. This task defines it.
    Reported to the architect.
  - Design only; no code written.

## Evidence
- None yet.

## Risks / Blockers
- None. It starts when T-G7qByZ lands. It runs in parallel with T-rpKCjP (lane A).

## Next actions
1. developer: implement after T-G7qByZ, run the verification, and record the parity-difference list
   and coverage here.
