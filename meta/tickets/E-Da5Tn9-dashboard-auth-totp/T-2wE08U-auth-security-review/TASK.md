# TASK: T-2wE08U-auth-security-review

## Metadata
- Task ID: `T-2wE08U-auth-security-review`
- Epic ID: `E-Da5Tn9-dashboard-auth-totp`
- Owner: `dev-security` (lead) + `reviewer`. Fixes are routed to the owning task's `developer`.
- Created: `2026-10-04`
- Last Updated: `2026-10-05`
- Status: `Draft`
- Estimate: `2 days` · Sprint `S3`. It may overlap with T-U2ERMo. The 13–28 % sprint buffer
  (HLD §24.1) absorbs the fix-ups.

## Requirements Mapping
- Requirement IDs: every FR and NFR (the verification gate), with emphasis on FR-13, FR-16–FR-20 and
  FR-27–FR-31.
- ACs: every AC is re-checked against the **merged** code. Invariants **S1–S26** (HLD §6.4).
- Design:
  - HLD §6 (attackers A1–A12, invariants), §13 (middleware), §11.1 (layering R1–R5), §11.15 (provider
    and guard), §16 (ledger);
  - §28.5 (the dev-security dispositions to verify) and §28.7 (residuals);
  - ADR-0021 D1–D11.

## Description
This is a post-implementation security audit and code review of the **merged** epic. It is separate
from the pre-implementation design reviews the manager runs on the HLD.

**`dev-security`:**
1. **Invariants.** Verify each of **S1–S26** against the code and tests. Each gets a pass/fail line
   that cites the test (file::name) or a manual reproduction.
2. **Dispositions.** For each dev-security finding **#1–#13** in HLD §28.5, confirm that its v2
   disposition actually landed. Specifically:
   - `user_id` plus CAS (remove → re-add, epoch straddle, stale rehash);
   - the proof check and duplicate-cookie rejection;
   - `canonical_client_key` and busy-as-failure;
   - `insecure_transport`;
   - enrollment tokens, and the BLOCKED login under policy `off`;
   - `auth_enabled` / `require_principal`;
   - `no-store` and `Clear-Site-Data`;
   - the `root_path` probes;
   - the phantom cap of 4096;
   - tighten-only config;
   - `hide_input_in_errors` and the record factory;
   - the store-hygiene items;
   - the `fetch`-`mode` ban and the CI rebuild diff.
3. **Attack attempts** (record the commands and outcomes):
   - classification bypasses: `//api/x`, `/API/x`, `/api/x/`, `%2F`, `..`, `HEAD`/`OPTIONS`, mount
     sub-paths, SPA-fallback files, websockets, a route added via `include_router`;
   - **proof:** replaying a harvested cookie without or with a wrong proof, an anonymous status
     probe, logout without the proof (the session must survive), a duplicate cookie (tossing);
   - CSRF and cross-port same-site requests in a real browser, reusing T-U2ERMo's smoke harness;
   - session fixation and rotation gaps; partial-session escalation;
   - TOTP replay across two live processes; enrollment-token guessing (counted toward the lockout)
     and token reuse or expiry;
   - enumeration through status, body, headers or timing (the opt-in timing measurement);
   - lockout bypass through IPv6 rotation inside a /64, `::ffff:` spellings, and `X-Forwarded-For`
     (trusted proxies off and on);
   - store and state-directory permission and symlink tricks against `FileBrowser` denial;
   - a hostile `.ao/config.yaml` trying to loosen any setting, or to set `trusted_proxies`;
   - secret leakage: run T-U2ERMo's sweep, and read the code paths;
   - hub `/login` CSP compliance;
   - the supervisor's `child_env` (settings only, no secrets);
   - `ao auth` secret handling (argv, env, shell history).
4. **Re-evaluate the `start_run` residual** (HLD §16 and §28.7). Either accept it again with a
   rationale, or open a fix task.
5. **Confirm the owner outcomes:**
   - **OQ-9:** was D25 shipped, or deferred? If deferred, the README must state the A4 residual.
   - **OQ-8:** the `Principal` field shapes, as agreed with the approval-gates epic.

   Record both for T-otjIkJ.
6. **Supply chain:** `pip-audit` (no new Python dependencies expected) and
   `npm audit --omit=dev --audit-level=high`.

**`reviewer`:**
- **Code quality** against CLAUDE.md: SOLID/KISS/DRY, no magic literals (every §12.6 constant is
  named), error handling, and no swallowed exceptions.
- **Conformance:**
  - the layering rules R1–R5, via the AST import test plus reading;
  - only `http/middleware.py` and `http/routes.py` import fastapi or starlette;
  - one policy table per app; flat auth routes;
  - `AttemptGuard` is the only credential-check sequence;
  - every session mutation goes through `SessionManager`.
- **Shared files:** the §16 ledger edits are additive and minimal. List any unlisted shared-file
  edit.

**Findings** are rated CRITICAL, HIGH, MEDIUM or LOW, each with reproduction steps.
- CRITICAL and HIGH are must-fix before closure. They are routed to the owning task, or to a new
  fix-up task with a fresh `T-<6>-<slug>` id, and re-verified. Nothing is fixed silently inside this
  task.
- MEDIUM and LOW are fixed or explicitly accepted, recorded in HLD §25 and the epic STATUS.

## Inputs / Outputs
- **Inputs:** the merged code, T-U2ERMo's evidence, the HLD and ADR-0021.
- **Outputs:**
  - the findings report `output/E-Da5Tn9-dashboard-auth-totp/security/review.md`, with any large
    reproduction artifacts beside it;
  - in this task's STATUS: a link to the report, a findings summary table, and the verdict;
  - a residuals list handed to T-otjIkJ.

## Acceptance Criteria
1. Every invariant **S1–S26** has a pass or fail line with evidence (test file::name or a reproduction
   command). Any fail has a finding.
2. Every dev-security finding **#1–#13** of HLD §28.5 is marked "landed as dispositioned" with
   evidence, or "deviates", with a finding.
3. **Zero open CRITICAL or HIGH findings at exit.** Every MEDIUM and LOW finding is fixed, or accepted
   with a written rationale.
4. The `pip-audit` and `npm audit --omit=dev --audit-level=high` outputs are recorded. If `pip-audit`
   is unavailable, the reason is recorded.
5. The reviewer's §16 ledger conformance check and the layering check are recorded, listing any
   unlisted shared-file edit.
6. The `start_run` residual decision, and the OQ-8 and OQ-9 outcomes, are recorded.
7. The epic STATUS is updated with the verdict and the counts for each severity (opened, fixed,
   accepted). The residuals list for T-otjIkJ exists.

## Risks
- **Late redesign** if a structural bypass is found. The Phase-4 consultations (HLD §28) and the
  manager's pre-implementation reviews reduce this risk.
- **Scope creep through fixes.** Fixes go to the owning task, or to a new fix-up task, never into
  this one.

## Dependencies
- **Upstream:** T-U2ERMo-auth-e2e-regression-sweep (the two may overlap).
- **Downstream:** T-otjIkJ-auth-docs-refresh-closure.

## Pseudocode / Algorithm
N/A (review). Use threat model §6.3 as the test plan, §6.4 as the checklist, and §28.5 as the
dispositions list.

## Schemas / Interface Notes
- The report has three tables:
  - invariants: `S# | verdict | evidence`;
  - dispositions: `#n | landed/deviates | evidence`;
  - findings: `id | severity | summary | repro | owner task | status`.

## Handoff Boundary
- **Upstream:** the implemented and verified epic.
- **Downstream:** the security sign-off plus the residuals list, which the closure ticket needs.

## Verification

```
python -m pytest -q tests/auth tests/ui tests/service
python -m pytest -q -m browser tests/auth/test_browser_smoke.py   # opt-in, local
pip-audit            # if available; otherwise record why not
cd ui && npm audit --omit=dev --audit-level=high
```

## Artifacts
- `output/E-Da5Tn9-dashboard-auth-totp/security/review.md` (plus reproductions)
- `meta/tickets/E-Da5Tn9-dashboard-auth-totp/T-2wE08U-auth-security-review/`
