# STATUS

- ID: `T-U2ERMo-auth-e2e-regression-sweep`
- Updated At: `2026-10-05`
- State: `Draft`
- Owner: `tester` (lane Q)
- Scope: `MVP` · Sprint: `S3` · Estimate: `2.5 d`

## This update
- By: architect · Role: agent · Date: 2026-10-05 · Comment: v2 rescope after the Phase-4
  consultations (HLD §28). The estimate is unchanged. Changes:
  - **Scrub sweep:** new sentinels for enrollment tokens and session proofs, `session_proof` added
    to the allowed locations, and the redaction mode now restores the LogRecord factory
    (dev-security #11).
  - **Subprocess e2e:** asserts the proof (a missing proof → 401 without destroying the session) and
    uses recovery codes for any second login (tester T-3).
  - **Browser smoke:** a cross-port cookie-replay check (dev-security #2 / D25), a CSP negative
    control, and Firefox/WebKit when installed (dev-security #13).
  - **CI:** a frontend rebuild-diff step (dev-security #13) next to the auth coverage gate.
  - **Ownership and naming:** the hermetic fixture now belongs to T-kzEzwy; the upstream list covers
    the five new tasks; the wire state is renamed to `second_factor_required`.
- By: architect · Role: agent · Date: 2026-10-04 · Comment: Task created as part of the
  E-Da5Tn9 design package (design and tickets only; **no code written**). It owns the cross-cutting
  verification: the scrub sweep, real-process e2e, the opt-in browser smoke, the coverage gate and
  full regression.

## Evidence
- None yet.

## Risks / Blockers
- None.

## Next actions
1. tester: run once every implementation task is Done. Record here the evidence table (including the
   three e2e runs, coverage numbers and which browsers ran) and the caller-matrix table.
