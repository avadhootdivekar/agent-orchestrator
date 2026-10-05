# STATUS

- ID: `T-rpKCjP-auth-http-routes`
- Updated At: `2026-10-05`
- State: `Draft`
- Owner: `developer` (lane A)
- Scope: `MVP` · Sprint: `S2` · Estimate: `2.5 d`

## This update
- By: architect · Role: agent · Date: 2026-10-05 · Comment: **v2 rescope.**
  - **Estimate and sprint:** 3 d / S2→S3 → 2.5 d / S2.
  - **Split** (developer D-4): the second-factor routes (E3–E7) and the provider-seam test moved to
    the new `T-KQ6ZrY-auth-routes-second-factor`. This task keeps the core routes (E1, E2, E8, E9,
    E10), the callable route-builder registry (reviewer R-5), `assert_flat_auth_routes` (developer
    D-1 BLOCKER: flat routes only), and the error handler with `cause_for_log` (reviewer R-10).
  - **New in v2:**
    - `session_proof` in the issuing bodies;
    - status and logout honour the proof (D25, dev-security #2);
    - `Clear-Site-Data` on logout (dev-security #7);
    - realm ids stable across ports (dev-critic C-2);
    - route-level revocation races (dev-security #1).
  - **Precisions, reported to the architect:**
    - E2 replaces the presented session only when `proof_ok`, so that a missing proof never
      destroys anything;
    - `read_json_object` gains an additive `optional_body` keyword for the optional E10 body.
- By: architect · Role: agent · Date: 2026-10-04 · Comment: Task created as part of the E-Da5Tn9
  design package (design and tickets only; **no code written**). It implements the frozen HLD §2
  contract exactly. It may start on a fake provider before T-XchniS merges.

## Evidence
- None yet.

## Risks / Blockers
- None. It runs in parallel with T-QJ1vyQ (lane B) after T-G7qByZ. Its proof cases depend only on
  the `proof_ok` value that T-G7qByZ computes.

## Next actions
1. developer: implement after T-XchniS and T-G7qByZ, run the verification, and record the
   conformance-table results here.
