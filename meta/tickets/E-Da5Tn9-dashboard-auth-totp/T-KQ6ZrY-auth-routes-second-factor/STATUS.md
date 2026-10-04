# STATUS

- ID: `T-KQ6ZrY-auth-routes-second-factor`
- Updated At: `2026-10-05`
- State: `Draft`
- Owner: `developer` (lane C)
- Scope: `MVP` · Sprint: `S3` · Estimate: `3 d`

## This update
- By: architect · Role: agent · Date: 2026-10-05 · Comment: TASK.md aligned with the HLD v2 cross-check (HLD §28.8): the two provider-seam OPEN_QUESTIONs are recorded as HLD OQ-10 (non-blocking); added AC 14, the full-configuration route enumeration (AC-17 part).
- By: architect · Role: agent · Date: 2026-10-05 · Comment: Task **created in v2**. It is split out
  of T-rpKCjP, which was over the 3-day cap (developer D-4).
  - It owns the second-factor routes (E3 verify, E4/E5 enrollment, E6 disable, E7 regenerate) and
    the **redirect-shaped** provider-seam test (dev-critic C-5).
  - **New v2 behaviour covered:**
    - token-gated forced enrollment (dev-security #5);
    - `insecure_transport` for E4/E5/E7 (dev-security #4);
    - cookie **and** proof rotation (D25);
    - cross-realm replay via the global TOTP step (S7).
  - **OPEN_QUESTIONs, reported to the architect:**
    1. A production redirect provider has no way to add PUBLIC policy entries without editing
       `policy.py`.
    2. Under D25 a redirect callback cannot hand the session proof to the SPA.

    AC-10 works around both with an explicitly composed test app and a captured proof.
  - Design only; no code written.

## Evidence
- None yet.

## Risks / Blockers
- None blocking. The OPEN_QUESTION above affects only future providers, not the MVP.

## Next actions
1. developer: implement after T-yfrfxv and T-rpKCjP, run the verification, and record the
   conformance-table results here.
