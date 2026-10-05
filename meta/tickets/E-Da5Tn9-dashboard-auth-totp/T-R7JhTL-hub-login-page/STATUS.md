# STATUS

- ID: `T-R7JhTL-hub-login-page`
- Updated At: `2026-10-05`
- State: `Draft`
- Owner: `developer` (frontend lane F)
- Scope: `MVP` · Sprint: `S1→S2` · Estimate: `3 d`

## This update
- By: architect · Role: agent · Date: 2026-10-05 · Comment: v2.1 gates folded (HLD §28.9).
  Estimate 2.5 d → 3 d; sprint S1 → S1→S2 (lane F straddle, HLD §24.3 capacity note). Changes:
  - **Ownership (design-review M2; §16 rows 12, 12b):** this task owns the `ui/package.json`
    `scripts` keys and the `ui/tsconfig.json` include only; T-vCgsU6 owns the `qrcode-generator`
    dependency and the lock.
  - **Bundle rule (§16 row 15, cross-epic X5):** commits only `auth/assets/hub-auth.{js,css}`; never
    `ui/static` (discarded after local builds). T-vCgsU6 is the single `ui/static` committer and now
    depends on this task.
  - **New `auth-client-contract.test.ts`** (design-review minor 7; §17.10): one scenario table run
    against the SPA client and `hubAuthCore.ts`; written after T-pQ73eO lands (soft dependency).
  - **Type (security M2):** `transport.proxy_suspected` in the type subset; banner condition
    unchanged.
  - New ACs 11–12 and a no-`ui/static` check in AC 9.
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
- None. The HLD §2 and §17.6 contracts are frozen (v2.1 adds `transport.proxy_suspected`, a
  manager-approved additive change); further changes need a manager-approved entry in the epic
  STATUS. OQ-8 and OQ-9 are DECIDED (D25 ships; this script always sends the proof).

## Next actions
1. developer (frontend): implement the hub script in S1 after T-pQ73eO; add the contract test once
   T-pQ73eO has landed; build; commit only the two `auth/assets` files (discard `ui/static`); record
   here the `hub-auth.js` gzip size (budget ≤ 6144 bytes) and the vitest and typecheck results.
