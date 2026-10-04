# STATUS

- ID: `T-otjIkJ-auth-docs-refresh-closure`
- Updated At: `2026-10-05`
- State: `Draft`
- Owner: `developer` (docs, lane B) + `manager` sign-off
- Scope: `MVP` · Sprint: `S3` · Estimate: `1.5 d`

## This update
- By: architect · Role: agent · Date: 2026-10-05 · Comment: v2 rescope to the v2 HLD §27. The
  estimate is unchanged. Changes:
  - **HLD "As built" section:** must now state the OQ-8 (`Principal` shape) and OQ-9 (D25 shipped or
    deferred) outcomes.
  - **README additions:**
    - the tighten-only rule (A12);
    - the credential vs state directories;
    - `AO_AUTH_STATE_DIR` and `AO_UI_AUTH_TRUSTED_PROXIES` (env/CLI only);
    - the `ao auth enrollment-token` procedure for the `required` policy;
    - the remote-enrollment refusal;
    - residual-threat wording that matches the OQ-9 outcome.
  - **ROADMAP follow-ups,** in the dev-critic's priority order: hub-run handoff first, store-scoped
    SSO second; the proof header is listed only if D25 was deferred.
  - **Inputs:** this ticket now consumes T-2wE08U's residuals list.
  - **Done rule:** still complete **only after** the docs are checked against the merged code
    (AC-1).
- By: architect · Role: agent · Date: 2026-10-04 · Comment: Task created as part of the
  E-Da5Tn9 design package (design and tickets only; **no code written**). This is the mandatory
  post-implementation docs-refresh ticket (HLD §27). Mark it complete only after the docs have been
  checked against the merged code.

## Evidence
- None yet.

## Risks / Blockers
- None.

## Next actions
1. developer: run after T-2wE08U signs off. Record the AC-1 verification checklist here, then the
   manager signs off and the epic closes.
