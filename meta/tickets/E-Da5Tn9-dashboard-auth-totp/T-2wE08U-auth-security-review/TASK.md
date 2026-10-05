# TASK: T-2wE08U-auth-security-review

## Metadata
- Task ID: `T-2wE08U-auth-security-review`
- Epic ID: `E-Da5Tn9-dashboard-auth-totp`
- Owner: `dev-security` (lead) + `reviewer`. Fixes are routed to the owning task's `developer`.
- Created: `2026-10-04`
- Last Updated: `2026-10-05`
- Status: `Draft`
- Estimate: `2.5 days` (v2: 2; +0.5 d to re-verify the §28.9 gate findings; HLD §24.2) · Sprint
  `S3`. It may overlap with T-U2ERMo. The sprint buffer and the cut-lines (HLD §24.1) absorb the
  fix-ups.

## Requirements Mapping
- Requirement IDs: every FR and NFR (the verification gate), with emphasis on FR-13, FR-16–FR-21 and
  FR-27–FR-31.
- ACs: every AC (AC-1..AC-46) is re-checked against the **merged** code. Invariants **S1–S30**
  (HLD §6.4; S27 cookie-only principal, S28 loopback definition, S29 config-only disable refusal,
  S30 `roles` aliasing are new in v2.1).
- Design:
  - HLD §6 (attackers A1–A12, invariants), §13 (middleware), §11.1 (layering R1–R5 and R1a), §11.15
    (provider and guard), §16 (ledger, the single-ownership note and cross-epic rows X1–X6);
  - §28.5 (the dev-security dispositions to verify), §28.7 (residuals) and **§28.9** (the two
    manager-run gates: security M1–M6, L1–L8; design review B1, M1–M3, minors 1–9);
  - §18 #11 and §24.3 (security M4: the epic merges to `main` only after this task signs off);
  - §24.1 (cut-lines);
  - ADR-0021 D1–D11 (v2.1).

## Description
This is a post-implementation security audit and code review of the **merged** epic. It is separate
from the pre-implementation design reviews the manager runs on the HLD.

**Merge gate (v2.1, security M4; HLD §18 #11).** The epic branch merges to `main` **only after this
task signs off** (zero open CRITICAL/HIGH, every MEDIUM/LOW fixed or accepted). The sign-off line in
STATUS is the manager's merge trigger.

**`dev-security`:**
1. **Invariants.** Verify each of **S1–S30** against the code and tests. Each gets a pass/fail line
   that cites the test (file::name) or a manual reproduction.
1a. **v2.1 gate findings (HLD §28.9).** For each gate finding, mark "landed as dispositioned" (with
   evidence) or "deviates" (with a finding):
   - **security:** M1 (principal and idle slide only with the proof; the single
     `HUB_COOKIE_ONLY_NAVIGATION` route; AC-44), M2 (`ClientInfo.is_loopback` = loopback peer +
     loopback `Host` + no forwarding headers; one WARNING; `proxy_suspected`; AC-43), M3 (config-only
     `enabled: false` with accounts → exit 78 + `auth.startup.disabled_by_config`; config-only TOTP
     below `required` → warning + `auth.startup.totp_downgraded_by_config`; status flags; AC-45),
     M4 (T-jVqH8w merged only after T-QJ1vyQ and T-KQ6ZrY; check the integration-branch merge order),
     M5 (`Principal.roles: list[str]`, fresh per request; aliasing test; AC-11), M6 (config-sourced
     `store_dir` never created or chmod-ed; `os.mkdir(mode=0o700)` + fd `fchmod`; AC-46),
     L1 (HMAC phantom/audit digests; one bucket for malformed names), L2 (`/redoc`,
     `/docs/oauth2-redirect` absent with auth on, present with auth off), L3 (`--reload` uvicorn
     kwargs), L4 (`--port 0` refused with auth on), L5 (README text, via T-otjIkJ), L6 (euid-owned
     group-writable parent warns; foreign/other-writable refuses), L7 (`service.env` denied; the
     two follow-ups recorded, not implemented);
   - **design review:** B1 (the `Principal` class constructs positionally and with keyword-only
     fields; field order), M1 (one `xdg.resolve_config_dir` with the approvals-compatible signature;
     the generic `denied_paths` mechanism), M2 (one file one owner: `http/routes.py`,
     `http/routes_second_factor.py`, `http/hub_routes.py`, the `tests/auth/helpers/` package, the
     four enumeration files; `ui/static` committed only by T-vCgsU6), M3 (cut-lines respected;
     `ao auth status` policy notes), minors 1–7 and 9 (deterministic `classify`; DAG edges honoured;
     NFR-5 informational only; importorskip guard with an unchanged FastAPI floor; R1a annotation
     test; the shared client contract test; NFR-1 exception list). Minor 8 was not adopted
     (manager decision) and needs no check.
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
     (trusted proxies off and on; v2.1: a loopback peer sending forwarding headers or a non-loopback
     `Host` must not count as loopback; with `trusted_proxies=127.0.0.1`, confirm the documented
     local-XFF spoofing residual and that the account lockout still applies);
   - store and state-directory permission and symlink tricks against `FileBrowser` denial (v2.1:
     also `~/.config/ao/service.env`);
   - a hostile `.ao/config.yaml` trying to loosen any setting, to set `trusted_proxies`, (v2.1) to
     flip `enabled: false` while accounts exist, to lower a config-sourced TOTP policy, or to point
     `store_dir` at a path that `ao auth --workspace` would create or chmod;
   - (v2.1) a harvested cookie without the proof against every PUBLIC route (no principal, no idle
     slide) and against the hub index (the accepted cookie-only residual);
   - secret leakage: run T-U2ERMo's sweep, and read the code paths;
   - hub `/login` CSP compliance;
   - the supervisor's `child_env` (settings only, no secrets);
   - `ao auth` secret handling (argv, env, shell history).
4. **Re-evaluate the `start_run` residual** (HLD §16 and §28.7). Either accept it again with a
   rationale, or open a fix task.
5. **Confirm the decided owner outcomes against the code** (both are DECIDED in v2.1; this task
   verifies the as-built shape, it does not decide):
   - **OQ-9:** D25 shipped in the MVP (it is not a cut-line); the proof is enforced as in HLD §13.3.
   - **OQ-8:** `Principal` is `@dataclass(frozen=True, slots=True)` with `username`, `auth_method`,
     `roles: list[str] = field(default_factory=list, hash=False)` and keyword-only `user_id`, `realm`,
     `session_id`, `amr`, `auth_time`, `provider`; `principal_for` returns a fresh list per call.
   - **Cut-lines (HLD §24.1):** record which, if any, were taken, and confirm the documented
     consequence holds (for example S6's weaker form if the phantom table was cut).

   Record all three for T-otjIkJ.
6. **Supply chain:** `pip-audit` (no new Python dependencies expected) and
   `npm audit --omit=dev --audit-level=high`.

**`reviewer`:**
- **Code quality** against CLAUDE.md: SOLID/KISS/DRY, no magic literals (every §12.6 constant is
  named), error handling, and no swallowed exceptions.
- **Conformance:**
  - the layering rules R1–R5 and R1a (no `TYPE_CHECKING`-only handler annotations), via the AST
    import test plus reading;
  - only `http/middleware.py`, `http/routes.py`, `http/routes_second_factor.py` and
    `http/hub_routes.py` import fastapi or starlette (v2.1);
  - one policy table per app, plus the one explicit `COOKIE_ONLY_NAVIGATION` set (only the hub
    index); flat auth routes; the list-valued `register_route_builder`;
  - `AttemptGuard` is the only credential-check sequence;
  - every session mutation goes through `SessionManager`; `principal_for` is the only `Principal`
    builder and the only tuple → list converter.
- **Shared files:** the §16 ledger edits are additive and minimal, and each file had **one owner at
  a time** (HLD §16 single-ownership note; design-review M2). List any unlisted shared-file edit.
  If the approvals epic has merged, check the cross-epic rows X1–X6 (one `xdg.resolve_config_dir`;
  one `_is_denied` helper; the bundle regenerated, never hand-merged).

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
1. Every invariant **S1–S30** has a pass or fail line with evidence (test file::name or a reproduction
   command). Any fail has a finding.
2. Every dev-security finding **#1–#13** of HLD §28.5 is marked "landed as dispositioned" with
   evidence, or "deviates", with a finding.
2a. **(v2.1)** Every §28.9 gate finding listed in Description 1a (security M1–M6, L1–L7; design
   review B1, M1–M3, minors 1–7 and 9) is marked "landed as dispositioned" with evidence, or
   "deviates", with a finding.
3. **Zero open CRITICAL or HIGH findings at exit.** Every MEDIUM and LOW finding is fixed, or accepted
   with a written rationale.
4. The `pip-audit` and `npm audit --omit=dev --audit-level=high` outputs are recorded. If `pip-audit`
   is unavailable, the reason is recorded.
5. The reviewer's §16 ledger conformance check and the layering check are recorded, listing any
   unlisted shared-file edit.
6. The `start_run` residual decision, the as-built confirmation of the decided OQ-8 and OQ-9
   outcomes, and any cut-line taken are recorded.
7. The epic STATUS is updated with the verdict and the counts for each severity (opened, fixed,
   accepted). The residuals list for T-otjIkJ exists.
8. **(v2.1, security M4)** STATUS contains an explicit sign-off line ("approved for merge to main" or
   "not approved", with reasons). The epic does not merge to `main` before that line says approved.

## Risks
- **Late redesign** if a structural bypass is found. The Phase-4 consultations (HLD §28) and the
  manager's pre-implementation reviews reduce this risk.
- **Scope creep through fixes.** Fixes go to the owning task, or to a new fix-up task, never into
  this one.

## Dependencies
- **Upstream:** T-U2ERMo-auth-e2e-regression-sweep (the two may overlap).
- **Downstream:** T-otjIkJ-auth-docs-refresh-closure; **the epic's merge to `main`** (v2.1, security
  M4).

## Pseudocode / Algorithm
N/A (review). Use threat model §6.3 as the test plan, §6.4 as the checklist, and §28.5 plus §28.9
as the dispositions lists.

## Schemas / Interface Notes
- The report has four tables:
  - invariants: `S# | verdict | evidence` (S1–S30);
  - Phase-4 dispositions: `#n | landed/deviates | evidence` (§28.5 #1–#13);
  - **v2.1 gate dispositions:** `finding (e.g. sec-M1, dr-B1, dr-minor-5) | landed/deviates |
    evidence` (§28.9);
  - findings: `id | severity | summary | repro | owner task | status`.
- Plus the sign-off line (AC 8).

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
