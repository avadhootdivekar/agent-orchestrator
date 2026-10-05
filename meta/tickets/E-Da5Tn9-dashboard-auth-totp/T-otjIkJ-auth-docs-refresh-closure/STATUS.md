# STATUS

- ID: `T-otjIkJ-auth-docs-refresh-closure`
- Updated At: `2026-10-05`
- State: `Draft`
- Owner: `developer` (docs, lane B) + `manager` sign-off
- Scope: `MVP` · Sprint: `S3` · Estimate: `2 d`

## This update
- By: architect · Role: agent · Date: 2026-10-05 · Comment: v2.1 gates folded (HLD §28.9).
  Estimate 1.5 d → 2 d (HLD §24.2). Changes:
  - **README additions (HLD §27 v2.1):** TOTP seeds in clear → encrypted backups only (security L5);
    required-policy enrollment tokens and sticky TOTP under `off` (design-review M3); config-only
    disable refusal and TOTP pinning (security M3); reverse-proxy and local-XFF advice (security M2);
    `/api/docs` under auth; `service.env` denial (L7); `--port 0` refused.
  - **`ui/README.md` dependency entry** moved here from T-vCgsU6.
  - **HLD "As built":** confirms the DECIDED OQ-8/OQ-9 outcomes against the code, records any
    cut-line taken (§24.1) and the as-built state of the §16 cross-epic rows X1–X6.
  - **Counts:** 23 tasks after the v2.1 re-baseline (AC 7); new AC 8.
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
- None. OQ-8 and OQ-9 are DECIDED; this ticket confirms them against the code rather than recording
  a decision.

## Next actions
1. developer: run after T-2wE08U signs off. Record the AC-1 verification checklist here, then the
   manager signs off and the epic closes.
