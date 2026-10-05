# STATUS

- ID: `T-vCgsU6-spa-enroll-account-qr`
- Updated At: `2026-10-05`
- State: `Draft`
- Owner: `developer` (frontend lane F)
- Scope: `MVP` · Sprint: `S2` · Estimate: `3 d`

## This update
- By: architect · Role: agent · Date: 2026-10-05 · Comment: v2.1 gates folded (HLD §28.9). The
  estimate is unchanged (3 d). Changes:
  - **First step: baseline check** (design-review minor 4): `npm ci && npm run build &&
    git diff --exit-code -- src/agent_orchestrator/ui/static` on the pre-epic baseline, recorded
    here; escalate to the manager if not clean.
  - **Single `ui/static` committer** (design-review M1/M2; HLD §16 row 15, cross-epic X5): rebuilds
    and commits the bundle after T-pQ73eO **and** T-R7JhTL have merged. New upstream dependency on
    T-R7JhTL.
  - **`package.json` split** (§16 row 12): this task owns only the `qrcode-generator` dependency
    and the lock; T-R7JhTL owns the scripts.
  - **Moved out:** the `ui/README.md` dependency entry goes to T-otjIkJ (estimate kept at 3 d).
  - New ACs 9–10.
- By: architect · Role: agent · Date: 2026-10-05 · Comment: v2 rescope after the Phase-4
  consultations (HLD §28). The estimate is unchanged. Changes:
  - **Forced enrollment** now starts with the CLI **enrollment token** (`{enrollment_token}`; v1 sent
    `{}`), per dev-security #5.
  - **New error messages:** `insecure_transport` on begin and regenerate (dev-security #4), and the
    `totp_required` message.
  - **Keepalive moved here** from T-pQ73eO, including an assertion that no timer runs while idle.
  - **Recovery banner** is rendered from `AuthContext.recoveryNotice`.
  - **Explicit logout** also clears the proof.
  - **Process:** a reproducible-bundle AC for the CI rebuild-diff gate (dev-security #13); fake
    timers mandatory (tester T-6).
- By: architect · Role: agent · Date: 2026-10-04 · Comment: Task created as part of the
  E-Da5Tn9 design package (design and tickets only; **no code written**). The QR library choice and
  its budget evidence are in HLD §17.7. `qrcode-generator@2.0.4` was checked at design time: MIT,
  zero runtime dependencies, `dist/qrcode.mjs` 11.2 KB gzip unminified.

## Evidence
- None yet.

## Risks / Blockers
- None. OQ-8 and OQ-9 are DECIDED (D25 ships; every `authApi` call carries the proof).
- Watch: a non-clean baseline rebuild (escalate) and cross-epic bundle conflicts (X5).

## Next actions
1. developer (frontend): run the baseline check first and record it here.
2. developer (frontend): implement; once T-pQ73eO and T-R7JhTL have merged, rebuild and commit the
   bundle. Record here the gzip sizes (main-chunk delta against `bb6d8a0`, QR chunk) and the
   `npm audit` result.
