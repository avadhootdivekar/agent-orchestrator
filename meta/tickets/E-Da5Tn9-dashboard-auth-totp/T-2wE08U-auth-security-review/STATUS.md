# STATUS

- ID: `T-2wE08U-auth-security-review`
- Updated At: `2026-10-05`
- State: `Draft`
- Owner: `dev-security` + `reviewer`
- Scope: `MVP` · Sprint: `S3` · Estimate: `2.5 d`

## This update
- By: architect · Role: agent · Date: 2026-10-05 · Comment: v2.1 gates folded (HLD §28.9).
  Estimate 2 d → 2.5 d (HLD §24.2). Changes:
  - **Invariants S1–S30** (S27 cookie-only principal, S28 loopback definition, S29 config-only
    disable refusal, S30 `roles` aliasing); ACs now AC-1..AC-46.
  - **New dispositions table** for the §28.9 gate findings (security M1–M6, L1–L7; design review
    B1, M1–M3, minors 1–7 and 9), each landed/deviates with evidence (AC 2a).
  - **OQ-8 and OQ-9 are DECIDED:** this task confirms the as-built `Principal` shape and D25
    instead of recording a decision; it also records any cut-line taken (HLD §24.1).
  - **Merge gate (security M4):** the epic merges to `main` only after this task's explicit sign-off
    line (new AC 8).
  - **Reviewer conformance:** fastapi/starlette only in the middleware and the three route modules;
    one file one owner (§16); cross-epic rows X1–X6 if the approvals epic has merged.
  - Supersedes the "S1–S26" wording of the v2 comment below.
- By: architect · Role: agent · Date: 2026-10-05 · Comment: v2 rescope after the Phase-4
  consultations (HLD §28). The estimate is unchanged. Changes:
  - **Coverage of the review:**
    - the invariants grow to **S1–S26** (S21–S26 are new);
    - attacker class A12 (the workspace config as attacker input) is in scope;
    - the review now verifies that each dev-security finding #1–#13 of HLD §28.5 actually landed.
  - **New attack attempts:** proof replay, cookie tossing, enrollment-token guessing,
    IPv6/`::ffff:` throttle bypass, hostile workspace config.
  - **Reviewer pass** now covers layering (R1–R5), the policy tables, the flat routes and
    `AttemptGuard`.
  - **Decisions to record:** the `start_run` residual is re-evaluated, and the OQ-8 and OQ-9
    outcomes are recorded for T-otjIkJ.
  - **Report location:** `output/E-Da5Tn9-dashboard-auth-totp/security/review.md`, linked from here.
  - **Fix routing:** fixes go to the owning task or a new fix-up task, never into this one.
- By: architect · Role: agent · Date: 2026-10-04 · Comment: Task created as part of the
  E-Da5Tn9 design package (design and tickets only; **no code written**). This is the
  post-implementation gate, distinct from the manager's pre-implementation design and security
  reviews.

## Evidence
- None yet.

## Risks / Blockers
- None. OQ-8 and OQ-9 are DECIDED (verify, do not decide).
- This task gates the epic's merge to `main` (security M4).

## Next actions
1. dev-security and reviewer: run once T-U2ERMo's evidence is available. Write the report (four
   tables), link it here with the findings summary, the verdict and the explicit sign-off line, and
   hand the residuals list (with the OQ-8/OQ-9 confirmations and any cut-line taken) to T-otjIkJ.
