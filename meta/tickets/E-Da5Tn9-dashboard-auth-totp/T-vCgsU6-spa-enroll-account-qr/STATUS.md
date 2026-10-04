# STATUS

- ID: `T-vCgsU6-spa-enroll-account-qr`
- Updated At: `2026-10-05`
- State: `Draft`
- Owner: `developer` (frontend lane F)
- Scope: `MVP` · Sprint: `S2` · Estimate: `3 d`

## This update
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
- None.

## Next actions
1. developer (frontend): implement, then rebuild and commit the bundle. Record here the gzip sizes
   (main-chunk delta against `bb6d8a0`, QR chunk) and the `npm audit` result.
