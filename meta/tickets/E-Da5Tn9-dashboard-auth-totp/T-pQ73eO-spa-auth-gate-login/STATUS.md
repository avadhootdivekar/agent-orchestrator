# STATUS

- ID: `T-pQ73eO-spa-auth-gate-login`
- Updated At: `2026-10-05`
- State: `Draft`
- Owner: `developer` (frontend lane F)
- Scope: `MVP` · Sprint: `S1` · Estimate: `3 d`

## This update
- By: architect · Role: agent · Date: 2026-10-05 · Comment: v2 rescope after the Phase-4
  consultations (HLD §28). The estimate is unchanged. Changes:
  - **Added: the session proof (D25 / ADR-0021 D11, dev-security #2).** That covers `proof.ts` (with
    a memory fallback and the blocked-storage notice), the `X-AO-Session-Proof` header on every
    request, storing `session_proof` from issuing responses, and clearing it on logout and on
    `anonymous`.
  - **Added: a `pageshow` bfcache re-check** (dev-security #7).
  - **Added: `fetch-mode-ban.test.ts`** with a file allowlist and a negative control
    (dev-security #13).
  - **Renamed: the second-factor state** to `second_factor_required` / `second_factor`.
  - **Moved out:** keepalive goes to T-vCgsU6, which balances the scope.
  - **Changed: the bundle rule.** This task now rebuilds and commits the SPA bundle, following HLD
    A-10. In v1 that was deferred to T-vCgsU6. The new CI rebuild-diff gate requires committed
    bundles to match a clean rebuild at every merge.
- By: architect · Role: agent · Date: 2026-10-04 · Comment: Task created as part of the
  E-Da5Tn9 design package (design and tickets only; **no code written**). It can start immediately
  against the frozen HLD §2 contract, using mocked fetch.

## Evidence
- None yet.

## Risks / Blockers
- None. The HLD §2 contract is frozen; changes need a manager-approved entry in the epic STATUS.

## Next actions
1. developer (frontend): implement; run vitest, typecheck and build; commit the bundle; record the
   interim main-chunk gzip delta and the manual `npm run dev` login check here.
