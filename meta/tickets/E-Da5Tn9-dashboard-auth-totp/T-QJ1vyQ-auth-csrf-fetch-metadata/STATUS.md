# STATUS

- ID: `T-QJ1vyQ-auth-csrf-fetch-metadata`
- Updated At: `2026-10-05`
- State: `Draft`
- Owner: `developer` (lane C; v2.1, was lane B)
- Scope: `MVP` · Sprint: `S2` · Estimate: `2.5 d` (v2.1; was 2 d)

## This update
- By: architect · Role: agent · Date: 2026-10-05 · Comment: v2.1 gates folded (HLD §28.9).
  - **Estimate 2 → 2.5 d; lane B → C** (HLD §24.2 rebalance, design-review minor 3).
  - **Security M1:** the enforcement branch now uses `attested = proof_ok or cookie_only`; the proof
    is required on every non-PUBLIC route (API or not) except the app's `COOKIE_ONLY_NAVIGATION`
    routes. AC 7 rewritten; new AC 8 = AC-44 dashboard part (`test_cookie_only_principal.py`, the
    security review's test gate 1).
  - **Security M4:** T-jVqH8w merges only after this task (merge edge).
  - **Design-review minor 2:** the T-QJ1vyQ → T-KOv2qD edge is now drawn in HLD §24.3.
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
- None. OQ-9 is DECIDED (D25 in the MVP), so the enforcement branch ships. It starts when
  T-G7qByZ's skeleton lands, and runs on lane C after T-yfrfxv.

## Next actions
1. developer: implement after T-G7qByZ, run the verification, and record the parity-difference list
   and coverage here.
