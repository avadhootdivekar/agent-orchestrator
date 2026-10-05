# STATUS

- ID: `T-pQ73eO-spa-auth-gate-login`
- Updated At: `2026-10-05`
- State: `Draft`
- Owner: `developer` (frontend lane F)
- Scope: `MVP` · Sprint: `S1` · Estimate: `3 d`

## This update
- By: architect · Role: agent · Date: 2026-10-05 · Comment: v2.1 gates folded (HLD §28.9). The
  estimate is unchanged (3 d). Changes:
  - **Bundle rule reversed (design-review M1/M2; HLD §16 row 15, cross-epic X5):** this task no
    longer commits `ui/static`. It builds locally, records the interim gzip delta and discards the
    output; T-vCgsU6 is the epic's single `ui/static` committer. This supersedes the v2 comment
    below. Description item 8, AC 10, Outputs and Verification updated.
  - **Type (security M2):** `types.ts` adds `transport.proxy_suspected` (manager-approved additive
    contract change); the banner logic is unchanged.
  - **Downstream:** T-R7JhTL's `auth-client-contract.test.ts` exercises this task's `api.ts` /
    `proof.ts` (design-review minor 7).
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
- None. The HLD §2 contract is frozen (v2.1 adds only `transport.proxy_suspected`); further changes
  need a manager-approved entry in the epic STATUS. OQ-8 and OQ-9 are DECIDED (D25 ships, so the
  proof handling is in scope).

## Next actions
1. developer (frontend): implement; run vitest, typecheck and build; **do not commit** `ui/static`;
   record the interim main-chunk gzip delta and the manual `npm run dev` login check here.
