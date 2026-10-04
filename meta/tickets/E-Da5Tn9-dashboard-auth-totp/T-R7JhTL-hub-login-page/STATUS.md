# STATUS

- ID: `T-R7JhTL-hub-login-page`
- Updated At: `2026-10-05`
- State: `Draft`
- Owner: `developer` (frontend lane F)
- Scope: `MVP` · Sprint: `S1` · Estimate: `2.5 d`

## This update
- By: architect · Role: agent · Date: 2026-10-05 · Comment: v2 rescope after the Phase-4
  consultations (HLD §28). Estimate raised from 2 d to 2.5 d. Changes:
  - **Session proof (D25 / ADR-0021 D11, dev-security #2).** The proof is stored from issuing
    responses in the hub origin's `localStorage` (memory fallback) and sent as `X-AO-Session-Proof`
    on every `/api` call. It is cleared on logout and on `anonymous`.
  - **Forced enrollment** now asks for the CLI enrollment token first (dev-security #5), and shows
    the `insecure_transport` (dev-security #4) and `totp_required` messages.
  - **Renamed wire state:** `totp_required` → `second_factor_required`.
  - **Code layout:** a testable `hubAuthCore.ts` plus an entry `hubAuth.ts`. The CSS source moved to
    `ui/src/hub/hub-auth.css` and is emitted by the hub build.
  - **Process:** no `mode` option (dev-security #13); a reproducible-build AC feeding the CI
    rebuild-diff gate; fake timers mandatory (tester T-6).
- By: architect · Role: agent · Date: 2026-10-04 · Comment: Task created as part of the
  E-Da5Tn9 design package (design and tickets only; **no code written**). It can start immediately:
  it depends only on the frozen HLD §2 contract and the §17.6 DOM contract.

## Evidence
- None yet.

## Risks / Blockers
- None. The HLD §2 and §17.6 contracts are frozen; changes need a manager-approved entry in the epic
  STATUS.

## Next actions
1. developer (frontend): implement, build, commit both assets, and record here the `hub-auth.js` gzip
   size (budget ≤ 6144 bytes) and the vitest and typecheck results.
