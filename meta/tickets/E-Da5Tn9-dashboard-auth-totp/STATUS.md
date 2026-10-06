# STATUS

- ID: `E-Da5Tn9-dashboard-auth-totp`
- Updated At: `2026-10-06`
- State: `Done` (23/23 tasks; AC-34 browser smoke PASSED 2026-10-06: 3 passed)
- Owner: `manager`

## This update
- By: tester · Role: agent · Date: 2026-10-06 · Comment: **AC-34 run and closed.** 2026-10-06: `pytest -q -m browser tests/auth/test_browser_smoke.py` = **3 passed** (Chrome 138 via Playwright, isolated venv, no prod install; 4 consecutive runs): CSP detector negative control, login + password login + cross-port proof check (S21), and the new forced-enrollment case (`add-user --require-totp` token -> real SVG QR, secret, URI -> confirm with a live code -> 10 recovery codes -> acknowledge -> dashboard; no CSP violation or console error). No defects found in the product code; the only fix was a test race (the QR chunk is lazy-loaded, so the test waits for `svg path`). Screenshots: `output/E-Da5Tn9-dashboard-auth-totp/{login,enroll-qr}.png`. Hub login page and Firefox/WebKit remain uncovered. `tests/auth` full dir: 2182 passed, 1 skipped.
- By: manager · Role: agent · Date: 2026-10-05 · Comment: **Epic closed: all 23 tasks Done.** The security
  + design review gate (T-2wE08U) signed off (0 CRITICAL; H1/H2/M fixed; accepted residuals listed) and
  T-otjIkJ refreshed the docs to the as-built state (HLD "As built", ADR-0021 Accepted, README,
  `docs-md/dashboard-authentication.md`, ROADMAP, `ui/README.md`). Full suite 7471 passed / 13
  skipped. Rollup synchronized from each task STATUS (counts 23/23). Open items are follow-ups, not
  blockers (see Next actions). Branch `ad/dashboard-auth-totp`, not pushed.
- By: manager · Role: agent · Date: 2026-10-05 · Comment: Independent gates re-verified as NOT previously run (the v2 STATUS 'launched' note referred to the architect's v1 consultations); reviewer + dev-security gates run on v2 and folded into HLD §28.9 (commit 4d2aafd). Implementation started on branch ad/dashboard-auth-totp: Done so far T-kzEzwy (9446c9d), T-pQ73eO (50e0d39), T-R7JhTL (e996c4e), T-s6sJmB (1ce99bf), T-kwwJ82 (3e9e0db), T-8NQP8J (4837d48).
- By: architect · Role: agent · Date: 2026-10-05 · Comment: **Design package v2.1 (gates
  folded).** Both independent gates the manager ran on v2 are folded in with the manager's final
  decisions; dispositions are in HLD §28.9 (finding id, severity, disposition, where changed).
  - **Contract (owner decision, supersedes the v2 tuple):** `Principal.roles: list[str] =
    field(default_factory=list, hash=False)`; `user_id`, `realm`, `session_id`, `amr`,
    `auth_time`, `provider` are keyword-only; `SessionRecord` / `VerifiedIdentity` / `UserView`
    keep tuples and `principal_for` returns a fresh list per request. **OQ-8 and OQ-9 are
    DECIDED.** Manager-approved additive HTTP-contract change: E1 `transport.proxy_suspected`
    (security M2).
  - **Security (all ADOPTED):** M1 proof-gated principal and sliding plus the single
    `COOKIE_ONLY_NAVIGATION` route (hub index); M2 loopback = peer + Host + no forwarding headers;
    M3 config-only disable refused (exit 78) and config-only TOTP downgrade warned/audited; M4 merge
    edges #9 ⇒ #13 and #11 ⇒ #13, epic to `main` only after T-2wE08U; M6 no create/chmod of a
    config-chosen store; L1–L7 (L7's permission re-check is DONE, see D5 below; the CLI uid is DONE, see D6 below); four test
    gates as AC-43..AC-45 and AC-11.
  - **Design review (adopted except minor 8, kept by manager decision):** approvals-compatible
    `xdg.resolve_config_dir` and cross-epic rows X1–X6 in HLD §16 (the approvals epic's files were
    read, not edited); one file one owner; cut-lines; minors 1–7 and 9.
  - **Plan:** new task `T-Hd4wQ2-auth-browse-denial-log-scrub`; 23 tasks, 61 dev-days, critical
    path ≈ 26 dev-days, 3–20 % buffer; every task ≤ 3 days. New **OQ-11** (calendar basis) for the
    manager.
  - Epic state `Draft` → `Approved`. Every task stays `Draft` until it starts.
- By: manager · Role: agent · Date: 2026-10-05 · Comment: Design v2 accepted for the independent gates. Decisions on the architect's open questions (owner-delegate):
  - **OQ-8 (Principal contract): `roles` stays `list[str]`**, exactly as the owner's brief and the approval-gates epic expect (a fresh empty list per Principal, so `principal.roles == []` holds; dataclass hash must exclude it, e.g. `field(hash=False)`). The additive fields (`user_id`, `realm`, `session_id`, `amr`, `auth_time`, `provider`) are accepted; `amr` may remain a tuple. HLD §2.6, ADR-0021 D6, T-kwwJ82 and AC-11 are to be amended accordingly.
  - **OQ-9: the D25 session proof stays in the MVP** (it is the first item to cut if the schedule slips: HLD §24.1).
  - **OQ-1:** no cross-realm SSO/handoff in this epic (first follow-up). **OQ-2:** idle 30 min / absolute 12 h. **OQ-3..7, OQ-10:** architect defaults accepted.
  - The independent design review (reviewer) and security review (dev-security) were launched; their findings are folded into HLD §28 by the architect before any code is written.
- By: architect · Role: agent · Date: 2026-10-05 · Comment: Ticket-authoring cross-check
  complete. All 22 tickets are written to v2 scope. Every HLD inconsistency the ticket authors
  reported is fixed in the HLD (§28.8), and the affected TASK/STATUS files are aligned. Results:
  - `check_tickets` consistency pass: 22 tasks, 55.0 dev-days, IDs valid, `Role: agent` everywhere;
  - one new non-blocking open question, OQ-10 (OIDC follow-up seams).
- By: architect · Role: agent · Date: 2026-10-05 · Comment: Design package **v2** published:
  - HLD + LLD in `docs-md/dashboard-auth-hld.md` (§0–§28, all required sections present);
  - `docs-md/adr/ADR-0021-dashboard-authentication-and-totp.md` v2 (D1–D11, Proposed);
  - 22 task folders, each with `TASK.md` and `STATUS.md`, all in state `Draft` (5 new in v2).

  v2 folds in every Phase-4 consultation; dispositions are in HLD §28. No BLOCKER, MAJOR or HIGH
  finding remains open. The HTTP contract (HLD §2, now including `session_proof` and
  `X-AO-Session-Proof`) and the Python identity contract (HLD §2.6 v2) are **frozen for parallel
  work**, subject to OQ-8 and OQ-9 below. Changes need a manager-approved entry here.
- By: architect · Role: agent · Date: 2026-10-04 · Comment: Design package v1 created (17 tasks),
  superseded by v2.

## Rollup

| Task | State | Sprint | Notes |
|---|---|---|---|
| T-kzEzwy-auth-foundation | Done | S1 | **new (v2)**; owns the root hermetic test fixture and `tests/auth/helpers/core.py`; start first |
| T-s6sJmB-auth-crypto-primitives | Done | S1 | v2.1: lands `totp.py`/`recovery.py` first (T-8NQP8J needs them) |
| T-8NQP8J-auth-user-store | Done | S1 | v2.1: approvals-compatible `xdg` signature; fd-based `fsutil`; denial helpers moved to T-Hd4wQ2 |
| T-kwwJ82-auth-sessions-policy-principal | Done | S1 | v2.1: `roles: list[str]` (OQ-8 decided); cookie-only navigation sets |
| T-PlEROT-auth-settings-layering | Done | S1 | v2.1: 3 d; `ConfigRisk` detection (security M3) |
| T-CsT5gk-auth-throttle-audit-scrub | Done | S1 | v2.1: HMAC name digests (L1); `scrub.py` moved to T-Hd4wQ2 |
| T-Hd4wQ2-auth-browse-denial-log-scrub | Done | S1 | **new (v2.1)**; generic `denied_paths` lands first (cross-epic X2); `scrub.py` |
| T-XchniS-auth-local-provider-runtime | Done | S2 | v2.1: keyed phantom digests; parent-dir warnings |
| T-yfrfxv-auth-provider-second-factor | Done | S2 | **new (v2)**, split from T-XchniS |
| T-G7qByZ-auth-middleware-app-integration | Done | S1→S2 | Start in S1 on a stub runtime (critical path); v2.1: proof-gated principal/sliding |
| T-QJ1vyQ-auth-csrf-fetch-metadata | Done | S2 | **new (v2)**; v2.1: 2.5 d, lane C, AC-44 enumeration; merge edge to T-jVqH8w |
| T-rpKCjP-auth-http-routes | Done | S2 | v2.1: 3 d, lane Q; `client_info` (M2); creates the `routes_second_factor.py` stub |
| T-KQ6ZrY-auth-routes-second-factor | Done | S3 | **new (v2)**; v2.1: owns `routes_second_factor.py`; merge edge to T-jVqH8w (co-critical) |
| T-j9dfsw-ao-auth-cli | Done | S2 | v2.1: config-sourced `store_dir` rule (M6); status flags |
| T-jVqH8w-ao-ui-auth-wiring | Done | S3 | v2.1: merges only after T-QJ1vyQ and T-KQ6ZrY; `ConfigRisk` enforcement |
| T-KOv2qD-hub-service-auth | Done | S2→S3 | v2.1: 3 d; `hub_routes.py`; hub cookie-only route |
| T-PDGw9p-service-cli-supervisor-auth | Done | S3 | **new (v2)**; v2.1: lane A |
| T-R7JhTL-hub-login-page | Done | S1→S2 | v2.1: 3 d; SPA↔hub contract test; owns `package.json` scripts and `tsconfig` |
| T-pQ73eO-spa-auth-gate-login | Done | S1 | v2.1: no longer commits `ui/static` |
| T-vCgsU6-spa-enroll-account-qr | Done | S2 | v2.1: the only `ui/static` committer; baseline check first |
| T-U2ERMo-auth-e2e-regression-sweep | Done | S3 | v2.1: 3 d; **AC-34 browser smoke PASSED 2026-10-06** (3 passed: CSP control, login + cross-port proof, forced enrollment with QR) |
| T-2wE08U-auth-security-review | Done | S3 | v2.1: 2.5 d; re-verify §28.9; epic merges to `main` only after this |
| T-otjIkJ-auth-docs-refresh-closure | Done | S3 | Post-implementation docs refresh; v2.1: 2 d |

Counts: 23 tasks · 23 Done · 0 In Progress · 0 Blocked · 0 Not started · 61.0 dev-days.

## Evidence
- Empirical design evidence (HLD §25.4):
  - scrypt `maxmem` and timings;
  - RFC vectors;
  - Chrome 138 behaviour for Origin, Fetch Metadata and cross-port cookies;
  - uvicorn proxy-header defaults;
  - Starlette state and middleware order;
  - the FastAPI `include_router` opacity that the developer consultation reproduced.
- Consultation record: HLD §28 (developer, reviewer, tester, dev-security, dev-critic).

## Risks / Blockers
- **No blockers.** Everything below is an accepted residual or a follow-up (full list: HLD "As built"
  section D; ROADMAP section 4):
  - ~~AC-34 browser smoke NOT RUN~~ **Closed 2026-10-06**: 3 passed on Chrome 138 (forced-enrollment/QR case added). Still open: hub login page and Firefox/WebKit browser cases.
  - Cross-epic rows **X1-X6 open until E-Ag7Pw3 (approvals) merges**; merge-order coordination with
    E-Rc4Hk8 (result cache) and E-Ag7Pw3 for `ui/app.py`, `cli.py`, `service/*`, `xdg.py`,
    `ui/files.py`, `ui/static`.
  - Closed 2026-10-06 (unit w03-03): `start_run` now rejects a `workflow_path` that resolves outside the workspace/search roots; `ui/files.read_file` refuses FIFOs/sockets/devices without opening them; the red lint file `repro_emit_lost_on_breaker_trip.py` is fixed. **Deferred:** the `dispatch_cycle` reset in the engine's missing-inputs branch (engine bug, not auth; no auth impact; tracked as FU-5 of E-Rc4Hk8).
  - Security residuals: local `X-Forwarded-For` claim under
    `trusted_proxies=127.0.0.1`; account-lockout DoS and a shared throttle bucket behind an
    unconfigured proxy; IPv6 `/64` rotation; phantom-eviction oracle; hard link vs the denied-path
    check; stale-binary fail-open when auth is enabled only via `service.env`; per-session counter
    concurrency; TOTP seeds in clear. (`dompurify` advisory resolved 2026-10-06: bumped to 3.4.16.)
  - **OQ-11** (calendar basis): settled in practice on the agent-lane basis; manager confirmation
    outstanding. **OQ-4** and **OQ-10** remain open follow-ups.

## Next actions
1. manager: merge `ad/dashboard-auth-totp` into the target branch. The security gate (M4) is satisfied.
   Sequence it with E-Rc4Hk8 and E-Ag7Pw3 (shared files above); after both epics are in, rebuild
   `ui/static` once (`npm ci && npm run build`), never hand-merge hashed assets.
2. manager: when E-Ag7Pw3 merges, re-verify X1-X6 and re-take the auth-off header snapshot
   (`tests/auth/test_auth_off_regression.py`).
3. ~~someone with Chrome + Playwright: run AC-34~~ Done 2026-10-06 (3 passed; forced-enrollment/QR added). Remaining: add the hub-login-page and Firefox/WebKit browser cases.
4. ~~developer: bump `dompurify` past 3.4.12 (GHSA-55q2-fjhq-7xh7).~~ Done 2026-10-06: now 3.4.16 (also clears GHSA-6688-9rhm-gjv2); `ui/static` rebuilt in a separate commit.
5. manager: confirm OQ-11 (agent-lane calendar basis) and decide OQ-4 (hub showing child auth state).
6. Follow-up epics in priority order (ROADMAP section 3.1): the **hub-run handoff**, store-scoped
   SSO, RBAC, API tokens, OIDC, fail-closed remote binds, hub child-auth state, at-rest seed
   encryption, then the smaller items (client-rendered hub index
   with the proof; the OS uid in CLI audit events is done, D6).
7. **D5 done (2026-10-06):** `LocalPasswordProvider` re-checks the store (`check_private_paths`, read-only
   `lstat`) on every login/re-auth and, throttled to 5 s, on each session revalidation; a loosened
   store/`users.json` fails closed (503 `store_unavailable`) and audits `auth.store.permissions_loosened`
   once per episode; it recovers when modes are restored.
7. operator, after merge: `bash install.sh --force`, restart `ao.service`, verify with `ao auth status`.
8. **D6 done (2026-10-06):** CLI audit events (`auth/cli.py::_audit`) carry `details.os_uid` (effective uid, never
   `$USER`) and `details.os_user` (passwd name, omitted if unresolvable); both keys added to `AUTH_DETAIL_KEYS`;
   web events carry neither. Tests in `test_cli_e2e.py` and `test_routes_core.py`.
