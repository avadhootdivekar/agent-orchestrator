# Pending-item audit of the 4-Oct epics (PR #17)

- Date: 2026-10-06 · Base: `main` @ `d5a1de9` (PR #17) · Branch: `ad/6-oct-enhancements`
- Scope: E-Da5Tn9 (auth + TOTP), E-Rc4Hk8 (result cache), E-Ag7Pw3 (approval gates).
  Other tickets with state In Progress/Draft (E-AMSSHX, E-GIytcL, E-or7k2d, E-gd8m4x, E-5I8azA,
  E-hbQnU2, E-Grpp0X, E-Sc9Rt4, E-st5p3q) were not touched by PR #17 (`git show --stat HEAD -- meta/tickets`
  lists only E-Da5Tn9, E-Rc4Hk8, E-Ag7Pw3 and the docs refresh), so they are out of scope here.
- Line numbers are as of this commit and point at the claim, not at code.
- Dispositions: **close now** (wave 2), **defer** (with reason), **out of scope** (accepted residual / other epic).
- Effort: dev-hours for one agent lane, including tests and doc sync.

## 0. Headline findings

1. **Approval gates (E-Ag7Pw3) are design-only.** There is no `src/agent_orchestrator/approvals/`;
   `STATUS.md:5-10` says "Draft rev 2 ... 0/14 implementation tasks started". The re-gate and parent
   confirmations are still open (`STATUS.md:86-90`). Recommendation in section 4: **defer**.
2. **Auth/TOTP is feature-complete but has one real verification gap (AC-34 never ran) and one
   known vulnerable dependency.** Both are cheap to close.
3. **Result cache is closed except for G0 (a business measurement needing operator consent) and
   accepted residuals.** Nothing there is a wave-2 coding item except housekeeping.
4. Stale-doc risk: three places say "NOT RUN"/"follow-up" for items wave 2 will close; they are
   listed in section 5 so A1.4 can be satisfied.

## 1. E-Da5Tn9 auth + TOTP

| ID | Item | Evidence | Effort | Disposition |
|---|---|---|---|---|
| D1 | **AC-34 real-browser smoke NOT RUN**; `tests/auth/test_browser_smoke.py` has never executed | `E-Da5Tn9/STATUS.md:4`, `:100`, `:108`; HLD `dashboard-auth-hld.md:149-152`; `T-U2ERMo/STATUS.md:5,21,53`. Env check today: `python -c "import playwright"` fails; `/usr/bin/google-chrome` exists; extra is `browser = ["playwright>=1.45"]` (`pyproject.toml:31`) | 3-5 h to install (`uv sync --extra browser`), run, fix selectors/timing. If Playwright cannot be installed offline, record the reproducible blocker (exact command + error) | **Close now** (run it or document the blocker) |
| D2 | Forced-enrollment/QR, hub login page, Firefox/WebKit browser cases never written | `T-U2ERMo/STATUS.md:21-22`; HLD `:152` | 4-6 h after D1 passes (Chrome only; Firefox/WebKit only if browsers installed) | **Close now** for Chrome forced-enrollment + hub page if D1 works; Firefox/WebKit **defer** (needs browsers) |
| D3 | **`dompurify` advisory GHSA-55q2-fjhq-7xh7** (moderate, <= 3.4.12); lock pins 3.4.12 | `ui/package.json:19` (`^3.4.12`), `ui/package-lock.json:1282-1284`; HLD `:145`; STATUS `:117`, `:131`; ROADMAP `meta/ROADMAP.md:226`. `npm view dompurify version` returns 3.4.16 today | 1.5-2.5 h: bump to >= 3.4.13 (latest 3.4.16), `npm ci && npm run build`, vitest, rebuild committed `ui/static` bundle in its own commit | **Close now**. Do the bundle rebuild once, after D3 |
| D4 | TOTP seeds stored in clear in `users.json` (`secret_b32`) | HLD `:147` (L5); STATUS `:118`; `meta/ROADMAP.md:223`; `src/agent_orchestrator/auth/store.py:108`, `:572` | 12-20 h to do properly: needs a key source (OS keyring vs a 0600 key file next to the store - a key file in the same dir adds little against same-uid attackers, which is already residual A10), schema `required_features` bump, migration on read, recovery/rotation CLI, tests | **Defer** with reason: same-uid attacker already reads both key and store (HLD A10 accepted residual); needs an owner decision on key custody. Record the decision question in the epic STATUS |
| D5 | Re-check store permissions after startup (permissions are only checked at start) | HLD `:5697`, `:5727-5728`; `meta/ROADMAP.md:224`; check function at `auth/paths.py:~100-125` (`check_store_permissions`, `fix=False`) | 4-6 h: re-run the existing check on a timer/at login (cheap `lstat`), refuse (fail closed) and audit on loosening; tests | **Close now** (reuses existing code; small) |
| D6 | Record OS uid in CLI audit events | HLD `:5697`, `:5728`; ROADMAP `:224`; `auth/audit.py` event model (`:100-102` username fields; no uid) | 3-4 h: add `os_uid` (and optionally `os_user`) to events emitted by `auth/cli.py`, extend `ALLOWED_FIELDS`, tests that web events do not carry it | **Close now** |
| D7 | Config-env denylist should also cover `AO_UI_AUTH*`, `AO_AUTH_DIR`, `AO_AUTH_STATE_DIR` once approvals merges | HLD `:5729-5730` (X6) | 1 h, but only meaningful once the approvals denylist exists (it does not) | **Defer** (blocked on E-Ag7Pw3) |
| D8 | Hub-run login handoff (first follow-up epic) | HLD `:204`, `:1163`, `:1743`; ROADMAP `:213` | ~3 dev-days design estimate (HLD `:5371`) | **Defer** (separate epic, product decision) |
| D9 | Store-scoped SSO, RBAC (`Principal.roles` always `[]`), API tokens, OIDC (+OQ-10 seams), WebAuthn/native TLS | `meta/ROADMAP.md:212-227`; HLD `:1743-1744`, `:5380` | Epic-sized each | **Out of scope** (roadmap) |
| D10 | Fail-closed non-loopback bind without auth (today deprecation notice) | `meta/ROADMAP.md:221` | 4-8 h | **Defer** (behaviour change for existing users, needs owner call) |
| D11 | Hub shows child auth state (OQ-4); client-rendered hub index with proof | HLD `:164`, `:5374`, `:5727`; ROADMAP `:222` | 8-12 h each; OQ-4 changes hub JSON with auth off (NFR-1) | **Defer** |
| D12 | OQ-11 calendar basis confirmation (manager) | STATUS `:119-120`, `:133`; HLD `:5379` area | 0.25 h: write the resolution line | **Close now** (documentation only, in the STATUS rewrite) |
| D13 | Residuals: `start_run` absolute `workflow_path`; local `X-Forwarded-For` claim; lockout DoS / shared throttle bucket behind unconfigured proxy; IPv6 /64 rotation; phantom-eviction oracle; hard link vs denied-path; stale-binary fail-open via `service.env`; per-session counter concurrency | STATUS `:111-116`; HLD `:140-147`; `ui/app.py:332`, `ui/service.py:672` (`start_run`) | `start_run` path restriction: 3-5 h; rest accepted by design | **Close now** only `start_run` path restriction if budget allows (wave 3 otherwise); the rest **out of scope** (documented residuals) |
| D14 | Cut-line items, rotation/coalescing, phantom table, redirect-shaped seam test, `required_features` | HLD `:5184-5187`; ROADMAP `:227` says "No cut-line item was dropped" | n/a | **Done** - no action; verified claim only, not re-tested here |
| D15 | Cosmetic: L-4 health version hash; DRY header constants; `ui/security.py` vs `auth/http/origin.py` origin unification; stripping server-only env from launched runs | HLD `:146`, `:3435`, `:4435`, `:5519` | 2-3 h each | **Defer** (cosmetic) |
| D16 | Merge sequencing: re-verify X1-X6 when E-Ag7Pw3 merges; re-take auth-off header snapshot; rebuild `ui/static` once after all three epics | STATUS `:101-102`, `:123-126` | n/a (post approvals) | **Defer** (X1-X6 blocked on approvals); the bundle rebuild is part of D3 |
| D17 | Post-merge operator steps: `install.sh --force`, restart `ao.service`, verify `ao auth status` | STATUS `:135` | human step | **Out of scope** (self-hosting safety: runs never run install) |

Note: the epic STATUS header (`:3-4`) reads "Done ... AC-34 NOT RUN, carried as a follow-up" and the
"Branch `ad/dashboard-auth-totp`, not pushed" claim at `:11` is stale after PR #17 merged it.

## 2. E-Rc4Hk8 result cache

| ID | Item | Evidence | Effort | Disposition |
|---|---|---|---|---|
| C1 | **G0 value gate not run** (shadow-mode measurement on a real consumer workflow; thresholds OQ-6) | `E-Rc4Hk8/STATUS.md:4`, `:62-63`; `meta/ROADMAP.md:41`, `:185-186` | Multi-day elapsed, operator consent, finplan workflow | **Out of scope** for agent waves (needs operator + real workload); keep as an explicit operator task |
| C2 | Merge verification with E-Ag7Pw3 / E-Da5Tn9 (HLD 24.2): classify new fields RULED, approval check before lookup seam + pin test, recapture I-2 goldens, rebuild UI bundle, CI `permissions:`/`pip-audit` | STATUS `:105-106` (completion note), `:117-118` area; ROADMAP `:190-192` | The auth half is now done by PR #17 landing together; approvals half is blocked | **Defer** the approval-ordering pin test until approvals lands; verify the goldens (I-2) still pass on `main` - 0.5 h, include in wave 2 verification |
| C3 | Accepted residuals: provider/endpoint env not in key; `filter.<x>.clean` run by guard-3 probe; dirty tracked edits pre-lookup; agent-writable cache dir; nested workspace; active TOCTOU | STATUS `:106-110` (completion note); `T-fXWbqg/STATUS.md:80,101-102` | n/a | **Out of scope** (documented residuals; `--no-cache` for untrusted repos) |
| C4 | Deferred Rev 3 features: `refresh`, `ao cache rm --run/--task`, `verify --repair`; non-MVP list (remote backends, HMAC entries, dir_fd walks, dependency-aware invalidation, warming, validate warning, `explain`) | STATUS `:108` area; `meta/ROADMAP.md:311-323` | Each 6-20 h | **Out of scope** (roadmap) |
| C5 | Hygiene ticket for deferred NITs (HLD 0.6 FU-4): `Path.open`/`read_bytes` AST checks (`T-fXWbqg/STATUS.md:43`), `HexStr` from `SHA256_HEX_RE` (`:44`), docstring R-1b (`:81`), cosmetic items (`:83`), G2 N1/N3/N6 (`:105-106`) | as cited | 6-10 h total | **Defer** (low value; schedule only after wave-2 items) |
| C6 | Pre-existing bugs flagged: missing-inputs branch resets `dispatch_cycle` (engine.py ~1174); `ui/files.read_file` FIFO open; red lint file `output/E-YAAGhk.../repro_emit_lost_on_breaker_trip.py` | STATUS `:56-58`, `:62-65` (completion note + "Next actions") | FIFO open: 1-2 h (a security-flavoured, auth-adjacent fix); dispatch_cycle: 2-3 h with a regression test; lint file: 0.25 h | **Close now**: FIFO open (auth epic's own follow-up too, STATUS `:~190`) and the lint file; dispatch_cycle **defer** unless time remains |
| C7 | OQ-4 (E-Ag7Pw3 representation of cached tasks), OQ-6/7/8 parent decisions | STATUS `:54-55`, `:63-65` | n/a | **Defer** (parent decisions; OQ-8 flips the default cache policy) |
| C8 | Stale headings: `STATUS.md` has two "Next actions" blocks and a trailing developer comment (`:200+`) saying "epic state `In Review`, not Done" that contradicts the header (`:4`) | `E-Rc4Hk8/STATUS.md:61-71` vs the last entries | 0.25 h | **Close now** (doc fix) |

## 3. E-Ag7Pw3 approval gates

| ID | Item | Evidence | Effort | Disposition |
|---|---|---|---|---|
| G1 | Entire implementation (14-16 tasks) not started | `E-Ag7Pw3/STATUS.md:5-10`, `:84-87` ("Start Sprint 1 with T-AGO2L6"); no `approvals/` package in `src/agent_orchestrator/` | 33 dev-days planned (STATUS `:35-37`; HLD sprint plan S1 18.5 / S2 14.5; critical path 18 days) | See section 4: **defer** |
| G2 | Design re-gate (reviewer + dev-security) on rev 2 not run | STATUS `:84-85` | 4-8 h of reviewer/security time | **Defer** with G1 |
| G3 | Parent confirmation of OQ-1, OQ-2, OQ-12..OQ-15, CE-1 | STATUS `:9-10`, `:86`, `:98-104`. Owner already decided OQ-1/OQ-2 and "OQ-12..15 and CE-1 stand" (`:106-108`) | 0.5 h to record | **Close now** (record in STATUS; decisions exist) |
| G4 | Design now has to be reconciled with as-built auth: HLD assumes `Principal` contract, `xdg.resolve_config_dir`, X1-X6 | `STATUS.md:108`; auth HLD `:5728-5733`; `meta/ROADMAP.md:233` ("Authorization on triggers") | 4-6 h architect pass | **Defer** with G1 (do it at the start of that epic) |
| G5 | Stale state: header says "Draft rev 2" and `Updated At 2026-10-05` although PR #17 merged the package | STATUS `:3-5` | 0.25 h | **Close now** (record "design merged, implementation deferred, reason") |

## 4. Approval gates: implement or defer?

**Recommendation: defer; do not start implementation in this run.**

- **Cost to implement:** 33 dev-days planned (HLD plan; critical path 18 d) plus a design re-gate and an
  architect reconciliation against as-built auth (~1 d). In agent-lane terms that is 4-6 waves of
  `max_parallel: 2` work, on the most conflict-prone files in the repo (`engine.py`, `models.py`,
  `project_config.py`, `ui/files.py`, UI bundle; STATUS `:~78-80`), plus new security-critical crypto
  (signed decisions, key store, resume integrity) that needs dev-security gates.
- **Value vs ask A1:** the ask is to close what was left pending. Approval gates were never started, so
  they are a new feature, not pending work. Auth depends on none of it (auth only reserved
  `Principal.roles` and merge rows X1-X6).
- **Risk:** a half-built gate engine is worse than none (resume/policy integrity claims were the
  Gate-1 FAIL). It cannot be completed credibly within the remaining waves alongside A2.
- **What to do instead (wave 2):** record the decision and reason in the epic STATUS (G3, G5), and keep
  the design package. Revisit as its own run once the owner confirms priority; the first task is
  `T-AGO2L6-spec-model-validation`.
- If the owner insists on a slice: the only independently shippable piece is the schema/validation
  (T-AGO2L6, 3 d, plus `T-drPIif`/`T-1MgGb4` at 2.5 d each), but it ships a feature with no engine behaviour,
  which we advise against.

## 5. Ordered closure list for wave 2 (value / cost)

| # | Item | Maps to | Effort | Why this order |
|---|---|---|---|---|
| 1 | Bump `dompurify` >= 3.4.13 (latest 3.4.16), `npm ci`, vitest, rebuild `ui/static` bundle (separate commit) | D3 | 2 h | Known advisory; highest value/cost; A1.2 |
| 2 | Run AC-34 browser smoke (install playwright extra, run on Chrome, fix); else record blocker with exact command/error | D1 (+ D2 Chrome cases) | 3-5 h (+4-6 h D2) | Only unverified AC; do after the bundle rebuild so it tests the final bundle; A1.2 |
| 3 | Permission re-check after startup | D5 | 4-6 h | Small, reuses `auth/paths.py` check; A1.3 |
| 4 | OS uid in CLI audit events | D6 | 3-4 h | Small, independent of D5 (can run in parallel in a second lane; disjoint files: `auth/cli.py`/`audit.py`) |
| 5 | `ui/files.read_file` FIFO open fix; `start_run` absolute `workflow_path` restriction | C6, D13 | 4-7 h | Cheap hardening on the same dashboard surface |
| 6 | Verify I-2 goldens / full suite still green on `main` | C2 | 1 h (suite ~12 min) | A1.5 |
| 7 | STATUS/doc reconciliation: auth STATUS (close D1-D6 or defer with reasons, drop stale "not pushed" claim, OQ-11), HLD "As built" section D and the NOT RUN note (`:149-152`), ROADMAP `:224-226`, `docs-md/dashboard-authentication.md`, cache STATUS stale sections (C8), approvals STATUS decision (G3, G5) | all | 2-3 h | Must come last; A1.4 |
| - | Optional if time: `dispatch_cycle` reset bug (C6), cache NIT hygiene ticket (C5) | C5, C6 | 2-3 h / 6-10 h | Lowest value |

Total wave-2 closure: roughly 20-30 h of agent effort across two lanes. Seed encryption (D4, 12-20 h) is
the largest item and is **deferred** pending a key-custody decision; if the owner wants it anyway it is the
next candidate after item 4.

## 6. Explicitly deferred / out of scope (to be recorded in epic STATUS files)

- Auth: D4 seed encryption (key custody undecided; same-uid residual), D7, D8, D9, D10, D11, D15,
  Firefox/WebKit smoke, residuals in D13 other than `start_run`.
- Cache: C1 G0 (operator), C3 residuals, C4 roadmap features, C5, C7.
- Approvals: the entire implementation and re-gate (section 4).

## 7. Method and limits

Read the three epic `STATUS.md` files, the auth HLD "As built"/residual sections, `meta/ROADMAP.md`,
the T-U2ERMo and T-fXWbqg task statuses, and spot-checked code locations (`auth/store.py`,
`auth/paths.py`, `auth/audit.py`, `ui/app.py`, `ui/service.py`, `pyproject.toml`, `ui/package*.json`).
Environment checks today: Playwright is not importable; `/usr/bin/google-chrome` exists; the npm
registry reports dompurify 3.4.16. Not done: individual per-task STATUS files of every task were not
read (only the ones cited); no tests were run in this unit; effort figures are estimates.
