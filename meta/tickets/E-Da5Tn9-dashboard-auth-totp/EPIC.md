# EPIC: E-Da5Tn9-dashboard-auth-totp

## Metadata
- Epic ID: `E-Da5Tn9-dashboard-auth-totp`
- Title: `Authentication (local accounts + optional TOTP 2FA) for the ao ui dashboard and the ao service hub`
- Owner: `manager` (delivery) · design by `architect`
- Created: `2026-10-04`
- Last Updated: `2026-10-05`
- Status: `Done` (2026-10-05): 23/23 tasks Done (T-U2ERMo with one recorded NOT RUN, AC-34 browser
  smoke); security gate signed off; docs refreshed to the as-built state (HLD "As built", ADR-0021
  Accepted, README, user guide, ROADMAP). Not yet merged to `main`: see STATUS next actions.

## Summary
- **Goal:** Add opt-in authentication to the dashboard (`ao ui`) and the multi-workspace hub
  (`ao service run`):
  - local accounts with a username and a scrypt-hashed password;
  - an optional, configurable TOTP second factor (authenticator apps), with single-use recovery codes;
  - deny-by-default enforcement;
  - an identity contract (`request.state.principal`, v2) and an audit seam that the approval-gates
    epic builds on.
  - With auth off (the default), behaviour is byte-identical to today.
- **v2 additions** (HLD §28):
  - an immutable `user_id` with compare-and-swap writes;
  - an origin-bound session proof header (D25; owner decision OQ-9, default include);
  - CLI-issued enrollment tokens for forced enrollment;
  - `insecure_transport` refusal for enrollment over plain HTTP;
  - a tighten-only workspace config;
  - credential and state directories split (`lockouts.json` and `audit.jsonl` live in the state
    directory);
  - per-app policy tables instead of route decorators;
  - one launch sequence (`prepare_auth`).
- **v2.1 additions** (independent gates, HLD §28.9):
  - `Principal.roles` stays the brief's `list[str]` (fresh per principal, excluded from the hash;
    the additive fields are keyword-only) — owner decision;
  - no principal and no idle slide without the session proof on any route; one explicit
    cookie-only navigation route (the hub index);
  - "loopback" also requires a loopback `Host` and no forwarding headers; unconfigured proxies are
    detected and reported;
  - a workspace-config-only `enabled: false` with accounts refuses to start; a config-only TOTP
    downgrade warns and is audited;
  - `ao auth` never creates or chmods a config-chosen store directory;
  - keyed (HMAC) phantom/audit username digests; `service.env` is never browsable; small launch
    hygiene fixes (`--reload` kwargs, `--port 0`, doc routes off under auth);
  - one-file-one-owner module and test layout, cross-epic rules with the approval-gates epic, DAG
    merge edges, cut-lines, and a re-baselined plan (23 tasks, 61 dev-days).
- **Scope In:**
  - A new package `src/agent_orchestrator/auth/` (layered L0–L4: constants, errors, seams, model,
    settings, paths, store, lockouts, crypto, sessions, policy, principal, throttle, guard, audit,
    scrub, provider, TOTP service, runtime, launch, http middleware and routes, CLI).
  - A neutral `src/agent_orchestrator/fsutil.py`, plus `xdg.resolve_config_dir` and
    `errors.EXIT_CONFIG`.
  - Wiring into `ui/app.py`, `service/hub.py`, `service/cli.py`, `service/supervisor.py`,
    `service/systemd.py`, `cli.py`, `ui/files.py`, `ui/service.py`, `tests/conftest.py`,
    `pyproject.toml` (wheel artifacts) and `.github/workflows/ci.yml`.
  - The `ao auth` CLI.
  - SPA screens: login, 2FA, forced enrollment with QR, account menu, global 401 handling, proof
    handling.
  - The hub login page (TypeScript built into a committed asset).
  - Tests: unit, integration, e2e, vitest, opt-in browser, and a log-scrub sweep.
  - Docs.
- **Scope Out (NON-MVP):**
  - The hub-run login handoff (**recommended first follow-up**) and store-scoped SSO (second).
  - OIDC, SSO and LDAP providers; WebAuthn and passkeys.
  - RBAC; API tokens.
  - Email password reset; self-service sign-up.
  - Fail-closed refusal of remote binds.
  - Native TLS flags; at-rest TOTP seed encryption.
  - The hub showing each child's auth state (OQ-4).
  - Full list in HLD §4.2.

## Design
- **HLD + LLD (v2):** [`docs-md/dashboard-auth-hld.md`](../../../docs-md/dashboard-auth-hld.md).
  Key sections:
  - §1 decision log D1–D25;
  - §2 the **frozen HTTP contract**, including the session proof;
  - §2.6 the identity contract v2;
  - §6 threat model;
  - §11 LLD;
  - §13 middleware algorithm;
  - §16 shared-file touchpoints;
  - §16 shared-file ledger, incl. the v2.1 cross-epic rows X1–X6;
  - §21 AC matrix (AC-1..AC-46);
  - §24 sprint plan (v2.1 re-baseline, cut-lines);
  - §28 consultation record; **§28.9 the independent gates**.
- **ADR (v2.1):** [`docs-md/adr/ADR-0021-dashboard-authentication-and-totp.md`](../../../docs-md/adr/ADR-0021-dashboard-authentication-and-totp.md)
  (D1–D11, **Accepted 2026-10-05** with 10 as-built amendments, closed by T-otjIkJ).
- **Identity contract for the approval-gates epic:** HLD §2.6. **Decided (OQ-8, owner, v2.1):**
  `roles: list[str] = field(default_factory=list, hash=False)`, with `user_id`, `realm`,
  `session_id`, `amr`, `auth_time` and `provider` added as keyword-only fields, so positional
  `Principal(username, auth_method, roles)` keeps working. Any later change needs manager sign-off.

## Requirements
Full text is in HLD §3. Each requirement maps to the 11 binding decisions B1–B11 of the brief.

- FR-1 Settings layering: CLI > env > workspace `ui.auth` > default (hub: CLI > env > default); off by default (B1)
- FR-2 Refuse to start (exit 78) with zero users or invalid/unsafe configuration; no default credentials; no web sign-up (B1)
- FR-3 scrypt password hashing; per-hash salt; bounded parameters in the hash string; CAS rehash on login; NFKC; `compare_digest` (B2)
- FR-4 TOTP per RFC 6238 (SHA-1/6/30, ±1 step); global replay rejection (B2)
- FR-5 Single-use recovery codes, stored hashed, shown once (B2)
- FR-6 Enrollment shows the manual secret and the `otpauth://` URI; the SPA also shows a QR code (B2/B11)
- FR-7 `AuthProvider` ABC + `LocalPasswordProvider`; the seam is proven by a redirect-shaped fake-provider test (B3)
- FR-8 `request.state.principal` (v2.1 shape: `roles: list[str]`, keyword-only additive fields; set only with a matching session proof, except the one cookie-only hub index) and `request.state.auth_enabled` are always present; `current_principal()`, `auth_enabled()`, `require_principal()` (B4)
- FR-9 Partial sessions are confined to their 2FA or enrollment endpoints (B4)
- FR-10 Credential store 0700/0600, atomic + flock, schema-versioned and forward-compatible; mutable state in a separate state directory (B5)
- FR-11 Server-side sessions, 256-bit tokens with only the hash stored, rotation, logout invalidation, idle and absolute expiry, no restart survival (B5)
- FR-12 Cookie name includes the port; `__Host-` + `Secure` on https; proven isolation between realms with stable realm ids (B6)
- FR-13 Deny-by-default ASGI middleware inside `SecurityMiddleware`, driven by per-app policy tables; route-enumeration test (B7)
- FR-14 Every internal HTTP caller has a working path with auth off and on (B7)
- FR-15 Hub protected; hub login page including text-only forced enrollment (B7)
- FR-16 Per-address (canonical key) and per-account (`lockouts.json`) throttling with exponential backoff; X-Forwarded-For only from trusted proxies (B8)
- FR-17 Uniform errors and work for "unknown user" vs "bad password" (B8)
- FR-18 Re-authentication for a password change, enabling 2FA, disabling 2FA and regenerating codes (B8)
- FR-19 Stronger CSRF checks for cookie-authenticated mutations; existing checks kept (B8)
- FR-20 No secrets in logs, exceptions, audit, status JSON or URLs (B8)
- FR-21 Loud warning for plain HTTP on a non-loopback bind (B8)
- FR-22 `ao auth` CLI incl. `enrollment-token`; passwords only from a hidden prompt or stdin; works without `[ui]` (B9)
- FR-23 Append-only JSONL audit log (0600, rotated, state directory) and a `record()` seam (B10)
- FR-24 SPA login, 2FA (with a recovery option), forced enrollment, account menu, global 401 handling; stores never browsable (B11/B5)
- FR-25 The supervisor propagates CLI-sourced settings; the hub refuses to start before spawning; exit 78 does not cause a restart loop (B1/B7)
- FR-26 `ao service list/status` handle the hub's 401 (B7)
- FR-27 **(v2)** Session proof header required on every non-PUBLIC `/api` request (D25; B6/B8)
- FR-28 **(v2)** Forced enrollment needs a CLI-issued, single-use, time-limited enrollment token (B2/B9)
- FR-29 **(v2)** No TOTP seed or recovery code over plain HTTP to a non-loopback client (`insecure_transport`) (B8)
- FR-30 **(v2)** Workspace config can only tighten; `trusted_proxies` CLI/env-only (B1/B8). **(v2.1)** A config-only `enabled: false` with accounts refuses (exit 78); a config-only TOTP downgrade warns and is audited; `ao auth` never creates or chmods a config-chosen store directory
- FR-31 **(v2)** Immutable `user_id`, snapshot-consistent epochs and CAS writes prevent session revival (B5)
- NFR-1 Auth off is byte-identical. The only additions are `GET /api/auth/status` and its OpenAPI entry; one deliberate exception: the store and state directories and `~/.config/ao/service.env` are never browsable
- NFR-2 No new Python dependencies; one pinned frontend dependency within a gzip budget, with a clean audit
- NFR-3 Import boundary: `ao auth` works without fastapi, starlette or uvicorn; AST layer test
- NFR-4 Deterministic tests (injected clock, entropy and hasher; fake timers in vitest)
- NFR-5 Per-request overhead p95 ≤ 1 ms and login p95 ≤ 600 ms are **targets** (measured informationally, not ACs; v2.1); hashing memory bounded; no blocking I/O on the event loop (AC-40)
- NFR-6 Coverage ≥ 90 % for `agent_orchestrator.auth`; the UI gate stays ≥ 80 %
- NFR-7 No magic literals
- NFR-8 Observability: audit log and `ao auth status` with the source of every setting
- NFR-9 Fail closed on store corruption or locking; never crash
- NFR-10 Accessible auth screens

## Task List
Sprint plan and capacity math: HLD §24 (team of 5, three 2-week sprints; **v2.1: 61 dev-days** of
demand against 63–76.5 dev-days of commitment capacity, with the cut-lines of HLD §24.1 as the
planned relief). Every task is ≤ 3 days.

| # | Task | Agent | Est | Depends on | Sprint | Lane |
|---|---|---|---|---|---|---|
| 0 | [x] `T-kzEzwy-auth-foundation` (**new in v2**) | developer | 1 d | — | S1 | B |
| 1 | [x] `T-s6sJmB-auth-crypto-primitives` | developer | 2.5 d | #0 | S1 | A |
| 2 | [x] `T-8NQP8J-auth-user-store` | developer | 3 d | #0; #1 (late-binding, v2.1) | S1 | B |
| 3 | [x] `T-kwwJ82-auth-sessions-policy-principal` | developer | 2 d | #0 | S1 | C |
| 4 | [x] `T-PlEROT-auth-settings-layering` | developer | **3 d** (v2.1; was 2.5) | #0, #2 | S1 | A |
| 5 | [x] `T-CsT5gk-auth-throttle-audit-scrub` (v2.1: `scrub.py` moved to #22) | developer | 3 d | #0, #2 | S1 | Q |
| 6 | [x] `T-XchniS-auth-local-provider-runtime` | developer | 3 d | #1–#5 | S2 | A |
| 7 | [x] `T-yfrfxv-auth-provider-second-factor` (**new in v2**) | developer | 2 d | #6 | S2 | C |
| 8 | [x] `T-G7qByZ-auth-middleware-app-integration` | developer | 3 d | #3 (stub runtime); #6 for the final wiring only | S1→S2 | C |
| 9 | [x] `T-QJ1vyQ-auth-csrf-fetch-metadata` (**new in v2**) | developer | **2.5 d** (v2.1; was 2) | #8 | S2 | **C** (v2.1; was B) |
| 10 | [x] `T-rpKCjP-auth-http-routes` | developer | **3 d** (v2.1; was 2.5) | #6, #8 | S2 | **Q** (v2.1; was A) |
| 11 | [x] `T-KQ6ZrY-auth-routes-second-factor` (**new in v2**) | developer | 3 d | #7, #10 | S3 | C |
| 12 | [x] `T-j9dfsw-ao-auth-cli` | developer | 3 d | #1, #2, #4, #5 | S2 | B |
| 13 | [x] `T-jVqH8w-ao-ui-auth-wiring` | developer | 3 d | #4, #6, #8, #10, #22; **merges only after #9 and #11** (v2.1) | S3 | B |
| 14 | [x] `T-KOv2qD-hub-service-auth` | developer | **3 d** (v2.1; was 2.5) | #9 (v2.1), #10, #16 | S2→S3 | A |
| 15 | [x] `T-PDGw9p-service-cli-supervisor-auth` (**new in v2**) | developer | 2.5 d | #13, #14 | S3 | **A** (v2.1; was Q) |
| 16 | [x] `T-R7JhTL-hub-login-page` | developer (frontend) | **3 d** (v2.1; was 2.5) | HLD §2 contract; #17 for the contract test only | S1→S2 | F |
| 17 | [x] `T-pQ73eO-spa-auth-gate-login` | developer (frontend) | 3 d | HLD §2 contract | S1 | F |
| 18 | [x] `T-vCgsU6-spa-enroll-account-qr` | developer (frontend) | 3 d | #16 (v2.1), #17 | S2 | F |
| 19 | [x] `T-U2ERMo-auth-e2e-regression-sweep` | tester | **3 d** (v2.1; was 2.5) | #6–#18, #22 | S3 | Q |
| 20 | [x] `T-2wE08U-auth-security-review` | dev-security + reviewer | **2.5 d** (v2.1; was 2) | #19 (may overlap) | S3 | review |
| 21 | [x] `T-otjIkJ-auth-docs-refresh-closure` (**post-implementation docs refresh**) | developer / manager | **2 d** (v2.1; was 1.5) | #20 | S3 | B |
| 22 | [x] `T-Hd4wQ2-auth-browse-denial-log-scrub` (**new in v2.1**) | developer | 2 d | #0; #2 (`paths.py`) | S1 | Q |

Total: **23 tasks, 61 dev-days** (v2: 22 tasks, 55 dev-days).

- **Parallel lanes:**
  - A, B, C: backend.
  - **F: frontend.** Runs **in parallel with the backend from day 1** (#17, then #16, then #18),
    using the frozen HLD §2 contract with mocked `fetch`. #18 is the epic's only `ui/static`
    committer and runs last (v2.1).
  - Q: developer-in-test. Takes #5 and #22 in S1, #10 in S2, then owns the verification sweep #19.
- **Critical path** (≈ 26 dev-days; v2: 24): #0 → #2 → #5 → #6 → #10 → #13 (with #11 co-critical
  through its merge edge) → #15 → #19 → #20 → #21.
  - This assumes #8 starts in S1 against a stub runtime. If #8 waits for #6, the path grows to
    ≈ 29 dev-days (HLD §24.3).
  - **Merge rules (v2.1, security M4):** #13 merges to the integration branch only after #9 and
    #11; the epic merges to `main` only after #20.
  - Calendar basis: OPEN_QUESTION OQ-11 (HLD §24.1, §25.3).
- **Cut-lines (v2.1, manager decision), in order:** audit rotation + coalescing (#5) → phantom
  lockout table (#5, #6) → the redirect-shaped AC-10 test (#11) → `required_features` (#2). D25,
  enrollment tokens, `insecure_transport` and tighten-only are not cut-lines.
- **v1 → v2 re-slicing** (developer finding D-4: six v1 tasks broke the 3-day cap):
  - #0 is new (foundation, D-3);
  - T-XchniS split, giving #7;
  - T-G7qByZ split, giving #9;
  - T-rpKCjP split, giving #11;
  - T-KOv2qD split, giving #15.
- **v2 → v2.1 re-baseline** (independent gates, HLD §24 and §28.9): #22 is new. It takes the
  file-browser denial from #13, the `paths.py` denial helpers from #2 and `scrub.py` from #5, so
  those tasks stay ≤ 3 days and the cross-epic `denied_paths` mechanism lands in S1. The store-busy
  CLI test (#12) and the informational p95 benchmark (#8) move to #19; the `ui/README.md`
  dependency entry moves from #18 to #21.

## Risks and Dependencies
Full lists are in HLD §25. The main risks:
- **R1 composition bugs.** Mitigated by RFC vectors, the invariant tests, and the security review.
- **R2 deny-by-default mistakes.** Mitigated by the exact-set route-enumeration test over policy
  tables.
- **R3 regressions with auth off.** Mitigated by the hermetic env fixture (#0) and header snapshots.
- **R4 multi-realm login friction.** The hub-run handoff is the first follow-up (OQ-1).
- **R5 cookie harvesting by another local listener.** Closed for API replay by D25 (OQ-9 decided:
  in the MVP); v2.1 also denies the principal and idle slides without the proof on every route.
  The residual (hub index render, forced logout by tossing) is documented.
- **R7 stale global installs silently ignore auth.** Mitigated by docs and `ao auth status`.
- **R13 merge conflicts with sibling epics** (`app.py`, `cli.py`, `service/*`, and in v2.1
  `xdg.py`, `ui/files.py`, `ui/security.py`, `ui/static`): one owner and a merge order per file in
  HLD §16 (rows X1–X6).
- **R15–R17 (v2):**
  - proof UX when browser storage is blocked;
  - enrollment-token friction;
  - the contract change colliding with the approvals epic: **resolved in v2.1** by the owner
    decision (`roles: list[str]`).
- **R18–R19 (v2.1):** reverse proxies deployed without `trusted_proxies` (detected and reported);
  the ~6 dev-day growth from the gate fixes (cut-lines; OQ-11).
- **Sibling epics:**
  - the approval-gates epic consumes HLD §2.6 and shares `xdg.py`, `ui/files.py`, `ui/app.py`,
    `ui/security.py` and the bundle (HLD §16 X1–X6; its own tickets are not edited here);
  - the cross-run cache epic has no expected interaction.

## Links
- Design doc: `docs-md/dashboard-auth-hld.md`
- ADR: `docs-md/adr/ADR-0021-dashboard-authentication-and-totp.md`
- Sprint plan: HLD §24
- User guide: `docs-md/dashboard-authentication.md`
- Output artifacts: `output/E-Da5Tn9-dashboard-auth-totp/` (opt-in browser-smoke screenshots from
  T-U2ERMo: none yet, AC-34 NOT RUN). The security review's findings, remediation and sign-off are
  recorded in `T-2wE08U-auth-security-review/STATUS.md` (no separate report file was written).

## Comments
- By: architect · Role: agent · Date: 2026-10-04 · Comment: Design package v1 created (HLD + LLD,
  ADR-0021, 17 tasks). No code written.
- By: architect · Role: agent · Date: 2026-10-05 · Comment: v2 published. All five Phase-4
  consultations are folded in and their dispositions are recorded in HLD §28:
  - developer: 1 BLOCKER, 4 MAJOR;
  - reviewer: 8 MAJOR;
  - tester: 1 BLOCKER, 5 MAJOR;
  - dev-security: 2 HIGH, 5 MEDIUM;
  - dev-critic: 3 HIGH.

  The plan was re-baselined to 22 tasks (5 new), 55 dev-days, a team of 5, and a critical path of
  about 24 days. Owner or manager decisions with non-blocking defaults:
  - **OQ-8:** `Principal.roles` as a tuple plus additive fields. Confirm with the approvals epic
    before T-kwwJ82 freezes `principal.py`. *(Superseded in v2.1: decided as `list[str]` with
    keyword-only additive fields; see the v2.1 comment below.)*
  - **OQ-9:** ship the D25 session proof in the MVP (default: yes).
  - **OQ-1:** handoff vs SSO timing (default: handoff first, after the MVP).
  - **OQ-2:** 30-minute idle timeout with non-sliding polling (default: keep).
- By: architect · Role: agent · Date: 2026-10-05 · Comment: Ticket-authoring cross-check done.
  The 22 tickets were written against HLD v2, and every gap they surfaced is resolved in the HLD
  (§28.8) with the affected tickets aligned. Highlights:
  - lost v1 detail restored: CLI, audit, scrub, throttle, `check_ready`, audit schema;
  - `with_identity` guarded web writes;
  - login destroys a session only with its proof;
  - AC-4 split into 4a/4b/4c;
  - a merge-order-safe route-enumeration rule.

  New non-blocking **OQ-10**: provider-contributed policies and a proof handoff for redirect
  providers (OIDC follow-up).
- By: architect · Role: agent · Date: 2026-10-05 · Comment: **v2.1 (gates folded).** The
  manager's two independent gates on v2 (design review: 1 BLOCKER, 3 MAJOR, 9 MINOR; security
  review: 0 CRITICAL/HIGH, 6 MEDIUM, 8 LOW, 4 test gates) are folded into the HLD, ADR and tickets
  with the manager's final decisions; dispositions in HLD §28.9. Every finding is adopted except
  design-review minor 8 (kept by manager decision). Highlights:
  - **OQ-8 decided** (owner): `roles: list[str] = field(default_factory=list, hash=False)`,
    keyword-only additive fields, fresh list per principal. **OQ-9 decided**: D25 stays in.
  - Security: proof-gated principal and sliding (M1), loopback/forwarding rule (M2), config-only
    disable refused (M3), DAG merge edges and merge-to-main after T-2wE08U (M4), no create/chmod
    of a config-chosen store (M6), HMAC phantom digests (L1) and L2–L7.
  - Design: approvals-compatible `xdg.resolve_config_dir`, cross-epic rows X1–X6, one file one
    owner (`hub_routes.py`, `routes_second_factor.py`, `tests/auth/helpers/` package, split
    enumeration files, single `ui/static` committer), cut-lines, minors 1–7 and 9.
  - New ACs AC-43..AC-46; invariants S27..S30; new task `T-Hd4wQ2-auth-browse-denial-log-scrub`;
    plan re-baselined to 23 tasks, 61 dev-days, critical path ≈ 26 dev-days. New **OQ-11**
    (calendar basis) for the manager.
  - Epic status: `Draft` → `Approved` (gates passed).
- By: manager · Role: agent · Date: 2026-10-05 · Comment: **Epic closed (Done).** All 23 tasks are
  Done and synchronized. Delivered scope, decisions, validation and follow-ups are in the completion
  note in `T-otjIkJ-auth-docs-refresh-closure/STATUS.md`. HLD "As built" (deviations, accepted
  residuals, OQ dispositions) governs over the design text. No cut-line was taken. One recorded gap:
  AC-34 (real-browser smoke) NOT RUN, because Playwright is not installed on the build machine.
