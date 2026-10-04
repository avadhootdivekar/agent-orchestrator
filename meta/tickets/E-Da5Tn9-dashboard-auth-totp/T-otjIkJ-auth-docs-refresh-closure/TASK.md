# TASK: T-otjIkJ-auth-docs-refresh-closure

## Metadata
- Task ID: `T-otjIkJ-auth-docs-refresh-closure`
- Epic ID: `E-Da5Tn9-dashboard-auth-totp`
- Owner: `developer` (docs, lane B) with `manager` sign-off
- Created: `2026-10-04`
- Last Updated: `2026-10-05`
- Status: `Draft`
- Estimate: `1.5 days` · Sprint `S3`

## Requirements Mapping
- Requirement IDs: post-implementation reconciliation for every FR and NFR; NFR-8.
- Design:
  - HLD §27 (**the scope of this ticket**), §18 (rollout and the required-policy procedure to
    publish), §19 (troubleshooting to publish), §25.3 (the OQs to close);
  - ADR-0021 (to be Accepted).
- Inputs from T-2wE08U: the verdict, the residuals list, and the OQ-8 and OQ-9 outcomes.

## Description
This is the **mandatory post-implementation docs-refresh ticket**. It reconciles `docs-md/`, the
README, the roadmap and the ticket docs with the **actually merged** behaviour, including every
deviation from the design. It is complete **only after** each statement has been checked against the
merged code.

1. **`docs-md/dashboard-auth-hld.md`:**
   - add an **"As built (YYYY-MM-DD)"** section listing every deviation from §1–§24, with code
     references. It must include the final outcome of **OQ-8** (the `Principal` field shapes) and
     **OQ-9** (D25 shipped or deferred);
   - set the Status line to "Implemented";
   - answer or close every OQ in §25.3.
2. **ADR-0021:**
   - Status → **Accepted** (with the date);
   - an amendment section for every decision changed during implementation or review, for example
     D11 if it was deferred.
3. **`README.md`:**
   - **Dashboard section:** replace the "There is no authentication" warning (currently at
     `README.md:327`) with an **Authentication** subsection covering:
     - enabling auth (CLI, env, config) and the **tighten-only** rule for workspace config (A12);
     - first-user bootstrap;
     - TOTP policies, recovery codes and the lost-device procedure;
     - the **`required`-policy procedure with `ao auth enrollment-token`** (§18 #8);
     - the multi-realm login note;
     - plain HTTP, TLS proxies and `AO_UI_AUTH_TRUSTED_PROXIES` (env/CLI only; the proxy must keep
       `Host`), and the remote-enrollment refusal;
     - the **credential vs state directories** (`~/.config/ao/auth` vs `~/.local/state/ao/auth`);
     - the **residual-threat statement** (A4/A10: "authenticated = full access; same-user processes
       are out of scope"). **Word it to match whether D25 shipped.** If D25 was deferred, state that
       another local listener can replay a dashboard session.
   - **CLI reference:** every `ao auth …` command (including `enrollment-token`, `unlock`,
     `revoke-sessions`), plus the `--auth`, `--auth-totp` and `--auth-dir` flags on `ao ui` and
     `ao service run`.
   - **Environment variables table:** every `AO_UI_AUTH*` variable, `AO_AUTH_DIR`,
     `AO_AUTH_STATE_DIR`, `AO_UI_AUTH_TRUSTED_PROXIES`.
   - **Per-project config example:** the `ui.auth` block with its tighten-only notes.
   - **Service setup:** `service.env` with `AO_UI_AUTH=1`, and `RestartPreventExitStatus=78`.
   - **Stale-install warning:** `install.sh --reinstall`, then `ao auth status`.
   - **Release note.**
4. **`meta/ROADMAP.md`:**
   - **§1 status table:** "Authentication / multi-user" → "**New, opt-in**: local accounts + TOTP".
     The Dashboard and Service rows (currently at `meta/ROADMAP.md:38–39`) lose "Unauthenticated".
   - **§3.1:** mark authentication delivered, and list the follow-ups **in priority order**:
     1. the hub-run handoff;
     2. store-scoped SSO;
     3. RBAC;
     4. API tokens;
     5. OIDC;
     6. fail-closed remote binds;
     7. the hub showing child auth state (OQ-4);
     8. the session proof header, **only if D25 was deferred**;
     9. at-rest seed encryption.
   - **§4:** rewrite the "Dashboard is unauthenticated" gap (currently at `meta/ROADMAP.md:267`)
     into the residual risks from T-2wE08U's list and HLD §6.3 (A4, A6, A10).
5. **Cross-links:**
   - `docs-md/dashboard-and-general-instructions-hld.md` §2.7 → a pointer to the auth HLD;
   - `docs-md/multi-workspace-service-hld.md` §8 → a hub-auth pointer;
   - optionally, the "Related" lines of ADR-0010 and ADR-0012.
6. **`ui/README.md`:** reconcile the runtime-dependency entry for `qrcode-generator` (added by
   T-vCgsU6) and the layout (`src/auth/`, `src/hub/`) with what actually shipped.
7. **`project_config.py` `_INIT_TEMPLATE`:** a commented `ui.auth` block (comments only; ledger #9).
8. **Tickets:**
   - every task's `TASK.md` and `STATUS.md` (and `HANDOFF.md` where present) are in sync;
   - the epic `EPIC.md` and `STATUS.md` show the final rollup counts;
   - the epic state becomes `Done`.
9. **Optional (manager):** a learnings entry in `meta/learnings.md` and `meta/learning-compact.md` if
   a non-obvious lesson came up. Candidates: Starlette `include_router` hiding routes,
   cookies scoped by host rather than port, and the session proof header.

## Inputs / Outputs
- **Inputs:** the merged code; T-U2ERMo's evidence; T-2wE08U's verdict, residuals and OQ outcomes.
- **Outputs:** the docs and ticket edits listed above, plus a verification checklist in STATUS.

## Acceptance Criteria
1. **Verified against code.** Every statement added to the README, ROADMAP, ADR amendments or the
   HLD "As built" section about behaviour (flags, env names, defaults, exit codes, routes, error
   codes, messages, paths) has been **checked against the merged code or a test**.
   - The checklist is recorded in STATUS, with `file:line` references for the non-obvious items.
   - **This ticket is not Done until that checklist is complete.**
2. `grep -rn "no authentication" README.md docs-md meta/ROADMAP.md` returns only historical or
   contextual mentions, and each remaining hit is justified in STATUS.
3. ADR-0021 is **Accepted**, and every implementation or review deviation appears as an amendment.
4. The README's residual-threat wording matches the recorded OQ-9 outcome, and the HLD "As built"
   section states both the OQ-8 and the OQ-9 outcomes.
5. The ROADMAP §3.1 follow-ups appear in the order above. The proof-header item is present **iff**
   D25 was deferred.
6. `python -m pytest -q tests/test_project_config.py` is green after the template edit.
7. The epic and every task are `Done`, with synchronized TASK/STATUS/EPIC/STATUS files and matching
   counts (22 tasks, plus any fix-up tasks opened by T-2wE08U).

## Risks
- **Docs describing the design instead of the build.** AC-1 forces verification against the code.
- **D25 outcome ambiguity.** AC-4 ties the README wording to the recorded OQ-9 outcome.

## Dependencies
- **Upstream:** T-2wE08U-auth-security-review.
- **Downstream:** epic closure.

## Pseudocode / Algorithm
N/A.

## Schemas / Interface Notes
- The STATUS verification checklist has one row per statement:
  `doc:line | claim | verified by (file:line or test) | ok/fixed`.

## Handoff Boundary
- **Upstream:** the security sign-off, the residuals and the OQ outcomes.
- **Downstream:** the epic is Done.

## Verification

```
python -m pytest -q tests/test_project_config.py
grep -rn "no authentication\|UNAUTHENTICATED\|unauthenticated" README.md docs-md meta/ROADMAP.md
grep -rn "AO_UI_AUTH\|AO_AUTH_DIR\|AO_AUTH_STATE_DIR" README.md   # each documented name exists in src/agent_orchestrator/auth/constants.py or settings.py
```

## Artifacts
- `meta/tickets/E-Da5Tn9-dashboard-auth-totp/T-otjIkJ-auth-docs-refresh-closure/`
