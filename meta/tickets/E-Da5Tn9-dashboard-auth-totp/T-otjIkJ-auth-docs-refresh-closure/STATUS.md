# STATUS

- ID: `T-otjIkJ-auth-docs-refresh-closure`
- Updated At: `2026-10-05`
- State: `Done`
- Owner: `developer` (docs, lane B) + `manager` sign-off
- Scope: `MVP` · Sprint: `S3` · Estimate: `2 d`

## This update
- By: manager · Role: agent · Date: 2026-10-05 · Comment: **Done.** Docs refreshed to the as-built
  state and the epic closed. Delivered:
  - `docs-md/dashboard-auth-hld.md`: Status -> Implemented; new "As built (2026-10-05)" section
    (OQ-8 and OQ-9 confirmed against the code, no cut-line taken, X1-X6 as-built state, 24
    deviations, accepted-residual table, open-question dispositions OQ-1..OQ-11, AC-34 NOT RUN).
  - `docs-md/adr/ADR-0021-...md`: Proposed -> **Accepted (2026-10-05)** plus 10 amendments and the
    sign-off residuals; ADR-0010 D7 gets a pointer amendment.
  - New user guide `docs-md/dashboard-authentication.md`; README (Authentication subsection replaces
    the "no authentication" warning, CLI row, options, env table, config example, service and
    stale-install notes, release note); `meta/ROADMAP.md` (§1 rows, §3.1 follow-ups in priority
    order, §4 residuals); cross-links in `dashboard-and-general-instructions-hld.md` §2.7 and
    `multi-workspace-service-hld.md` §8/§11; `ui/README.md` (`qrcode-generator` entry with the
    measured gzip sizes, `src/auth/` and `src/hub/` layout); commented `ui.auth` block in
    `project_config.py` `_INIT_TEMPLATE`; learnings.
  - Epic closure: EPIC.md State `Done`, rollup 23/23 Done (see the epic STATUS).
  - **Docs corrections found by checking against the code** (the design said otherwise):
    `install.sh --reinstall` does not exist, the command is `bash install.sh --force`;
    `AO_UI_AUTH_TRUSTED_PROXIES` is **env only** (no CLI flag); under `totp: off` web enrollment is
    refused (`totp_disabled_by_policy`); regenerating recovery codes needs the password **and** a
    current code; `ao service install --auth` exists (OQ-3 reversed).
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

### Verification checklist (AC-1): `doc:line | claim | verified by | ok/fixed`

Doc abbreviations: README = `README.md`; GUIDE = `docs-md/dashboard-authentication.md`;
HLD-AB = HLD "As built"; RM = `meta/ROADMAP.md`. Code paths are under `src/agent_orchestrator/`.

| doc | claim | verified by | result |
|---|---|---|---|
| README Authentication | `ao auth add-user` prompts, `--password-stdin`, min length 12 | `ao auth add-user --help` (run), `auth/constants.py` `DEFAULT_MIN_PASSWORD_LENGTH` | ok |
| README, GUIDE s2 | `ao ui --auth`, `--no-auth`, `--auth-totp off\|optional\|required`, `--auth-dir` | `ao ui --help` (run) | ok |
| README, GUIDE s7 | `ao service run` has `--auth/--no-auth --auth-totp --auth-dir`; `ao service install --auth` | `ao service run --help`, `ao service install --help` (run); `service/systemd.py:75` | ok |
| README, GUIDE s3 | all 11 `ao auth` commands and their flags (`--yes`, `--force`, `--require-totp`, `--json`, `-w`) | `ao auth --help` and each `ao auth <cmd> --help` (run) | ok |
| README env table | every `AO_UI_AUTH*`, `AO_AUTH_DIR`, `AO_AUTH_STATE_DIR` name exists | `auth/settings.py:69-79`, `auth/paths.py:28-29` (grep loop, all found) | ok |
| README, GUIDE s4 | defaults 30 min / 12 h / 5 / 30 s / 900 s / 20 / 12 | `auth/constants.py:27-47,59` | ok |
| README, GUIDE s4 | `trusted_proxies` env/CLI only (ticket text) | no CLI flag in `cli.py`/`service/cli.py`; `auth/settings.py:491` refuses the config key | **fixed**: docs say env only |
| README, GUIDE s4 | workspace config tighten-only, 7 numeric fields | `auth/settings.py:129-137` `TIGHTEN_RULES`; `tests/auth/test_settings.py::test_tighten_rules_cover_exactly_the_seven_fields` | ok |
| README, GUIDE s4 | config-only `enabled: false` with accounts refuses (exit 78); `--no-auth`/`AO_UI_AUTH=0` disables on purpose | `auth/launch.py:74-79,130`; `tests/auth/test_launch.py::test_config_only_disable_*`, AC-45 tests | ok |
| README, GUIDE s4 | config-only TOTP downgrade warns and is audited | `tests/auth/test_launch.py::test_a_config_only_totp_downgrade_warns_and_audits` | ok |
| README, GUIDE s5 | `required`: unenrolled user needs an operator token even without `--require-totp`; `ao auth status` prints the note | `ao auth status` run under `AO_UI_AUTH_TOTP=required` printed the note; AC-36 tests (`test_totp_service.py`, `test_routes_second_factor.py`) | ok |
| README, GUIDE s5 | `off`: enrolled users still challenged; web enrollment refused | `test_routes_second_factor.py::test_an_enrolled_user_is_still_challenged_when_the_policy_goes_off`; `auth/totp_service.py:437-439` `_require_policy_on` | ok (guide originally omitted the refusal: **fixed**) |
| GUIDE s5 | `optional` allows self-disable (password + code); `required` refuses (`totp_required`) | `auth/totp_service.py:308-319` | ok |
| GUIDE s5 | regenerating recovery codes needs the password and a code | `auth/totp_service.py:329-337` | **fixed** (guide said password only) |
| README, GUIDE s5 | enrollment token valid 60 minutes, single use; printed by `add-user --require-totp`/`reset-2fa` | `auth/constants.py:93` (3600); `ao auth enrollment-token alice` run: "valid 60 minutes" | ok |
| README, GUIDE s5 | remote enrollment over plain HTTP refused (`insecure_transport`) | `auth/totp_service.py:427-435`; AC-37 tests | ok |
| GUIDE s5 | 10 recovery codes, case/dash-insensitive, single use | `auth/constants.py:88`; `test_recovery.py`, `test_e2e_subprocess.py` | ok |
| GUIDE s5 | SHA-1/6 digits/30 s/1 step window; code single-use across realms | `auth/constants.py:81-85`; `test_routes_second_factor.py::test_a_code_used_in_one_realm_is_a_replay_in_the_other` | ok |
| README, GUIDE s2 | `--port 0` refused with auth on (exit 78) | `auth/launch.py:70-73,157`; `test_ui_command_auth.py::test_auth_with_port_zero_exits_78` | ok |
| README, GUIDE s2 | no accounts -> exit 78 | `test_e2e_subprocess.py::test_ui_auth_without_users_exits_78_and_refuses_to_start` | ok |
| README, GUIDE s6 | proxy: keep `Host`, `X-Forwarded-Proto`; `Secure` + `__Host-` cookies | `auth/constants.py:12-13`; `auth/runtime.py:59-60`; HLD s18 #4; AC-16 tests | ok |
| README, GUIDE s6 | `proxy_suspected` warning, shared throttle bucket until configured | `auth/http/routes.py:488-501`; `auth/provider.py:54`; AC-43 tests | ok |
| README, GUIDE s6 | plain-HTTP non-loopback warning and no-auth deprecation notice | `auth/launch.py:56-65` | ok |
| README, GUIDE s6 | local XFF claim under `trusted_proxies=127.0.0.1` | HLD s18 #4 and T-2wE08U accepted residual | ok (residual, documented) |
| README, GUIDE s8 | cookie is `ao_sid_<port>` (`__Host-` prefix over TLS); hub and each dashboard separate realms | `auth/runtime.py:42-60`; `test_cookie_isolation.py` | ok |
| README, GUIDE s1 | API needs `X-AO-Session-Proof`; cookie-only hub index; cookie tossing = forced logout | `auth/constants.py:18`; `auth/http/middleware.py:251-269`; `test_cookie_only_principal.py` | ok |
| README, GUIDE s9 | file browser denies store dir, state dir, `AO_AUTH_*` overrides and `~/.config/ao/service.env` | `auth/paths.py:137-160` `default_denied_paths`; `tests/ui/test_file_browser_denial.py` | ok |
| README, GUIDE s10 | credential dir `~/.config/ao/auth`, state `~/.local/state/ao/auth`; seeds in clear | `ao auth status` run (printed both paths); HLD s18 #7; `auth/paths.py:35-43` | ok |
| GUIDE s10 | audit log rotates at 10 MiB, 5 backups | `auth/constants.py:122-123` | ok |
| README, GUIDE s7 | `RestartPreventExitStatus=78` in the unit; a child exiting 78 is terminal (`stopped`, no backoff) | `service/systemd.py:62`; `service/supervisor.py:576-587`; `tests/service/test_supervisor_auth.py`, `test_systemd.py` | ok |
| README, GUIDE s7 | `ao service list/status` print "login required" on hub 401 | `service/cli.py:274-305`; `tests/service/test_cli_probe_auth.py` | ok |
| README, GUIDE s7 | stale snapshot ignores env-only enablement; `install --auth` fails closed | `service/cli.py:373-384`; `tests/service/test_cli_probe_auth.py::test_install_auth_flag_bakes_auth_into_exec_start` | ok |
| README, GUIDE s11, ADR-0021 | `install.sh --reinstall` | `install.sh:8-9,69-70,236-247`: the flag is `--force` (internally `uv --reinstall`) | **fixed** (design text was wrong) |
| README, GUIDE s2 | `/api/docs` and hub `/docs` unusable under auth; `service.env` never browsable | HLD s19.1; `tests/auth/test_auth_off_regression.py::test_auth_on_drops_exactly_those_two_framework_routes` | ok |
| GUIDE s12 | troubleshooting rows (exit-78 messages, 429, 403 codes) | message constants `auth/launch.py:56-80`; HLD s19.1 rows; AC-3/4/20/37 tests | ok |
| HLD-AB A (OQ-8) | `Principal` shape | `auth/principal.py:34-52`; `auth/sessions.py:352`; `tests/auth/test_principal.py` | ok |
| HLD-AB A (OQ-9) | D25 shipped | `auth/constants.py:18`; `auth/http/middleware.py:251-269`; e2e proof matrix (T-U2ERMo) | ok |
| HLD-AB A | no cut-line taken | T-CsT5gk/T-8NQP8J/T-KQ6ZrY STATUS | ok |
| HLD-AB B | X1-X6 as-built state | `xdg.py:49,78`; `ui/files.py:162-182`; T-U2ERMo STATUS "Cross-epic rows" | ok (X1-X6 open until E-Ag7Pw3 merges) |
| HLD-AB C 4-7 | H1/L-5/L-1/H2/N1 remediation | T-2wE08U STATUS Remediation table; named tests exist (grep) | ok |
| ROADMAP s1/s3.1/s4 | statuses and the follow-up order | ticket Description 4; no proof-header item because D25 shipped (AC-5) | ok |
| ui/README | `qrcode-generator` 2.0.4 exact; lazy chunk 7,548 B; main +6,986 B; only `QrCode.tsx` imports it | T-vCgsU6 STATUS bundle table; `ui/package.json:22`; grep of `lazy(` in `ui/src/auth` | ok |
| `_INIT_TEMPLATE` | commented `ui.auth` block is valid YAML when uncommented and ignored by `ProjectConfig` | ran `yaml.safe_load` + `load_project_config` on the uncommented block; `pytest tests/test_project_config.py` 44 passed | ok |

### Acceptance criteria
- **AC-1** checklist above, complete.
- **AC-2** `grep -rn "no authentication\|UNAUTHENTICATED\|unauthenticated" README.md docs-md meta/ROADMAP.md`:
  the README warning at the old line 327 is gone. Remaining hits are historical or contextual and
  justified: README (describes the default-off state and the stale-install failure mode); GUIDE
  (stale-binary failure mode); `docs-md/dashboard-and-general-instructions-hld.md` s2.7,
  ADR-0010 D7 and the multi-workspace HLD (original posture, each now carries a "superseded /
  hub authentication" pointer); `dashboard-file-preview-hld.md`, `live-activity-and-tabs-hld.md`,
  `scheduler-triggers-hld.md`, ADR-0011/0014, `ai-epics/E-Sc9Rt4`, `human-approval-gates-hld.md`
  and ADR-0020 (pre-existing design records of other epics describing the default-off posture or
  unrelated "unauthenticated audit line" wording; not edited, out of scope); the auth HLD/ADR-0021
  (design text and the as-built record itself).
- **AC-3** ADR-0021 Accepted (2026-10-05) with 10 amendments.
- **AC-4** README residual wording matches D25 as built; HLD "As built" confirms OQ-8/OQ-9, records
  that no cut-line was taken and the X1-X6 state.
- **AC-5** ROADMAP s3.1 follow-ups in the ticket's order; the proof-header item is absent (D25 shipped).
- **AC-6** `.venv/bin/python -m pytest -q tests/test_project_config.py`: 44 passed (after the edit).
- **AC-7** all 23 task STATUS/TASK files read `Done` (T-U2ERMo "Done, with one recorded NOT RUN: AC-34");
  epic EPIC.md and STATUS.md synchronized with `sync_epic.py`; counts 23/23.
- **AC-8** every "(v2.1)" README item present: seeds in clear/encrypted backups; required-policy
  tokens; sticky TOTP under `off`; config-only disable refusal and TOTP pinning; proxy advice;
  `/api/docs` under auth; `service.env` denial; `--port 0`. `grep -n "qrcode-generator" ui/README.md` hits.

### Other checks
- `.venv/bin/ruff check src tests`: clean (the only `src` edit is comments/docstring in `project_config.py`;
  two E501 introduced while editing were fixed). `ruff format --check src/agent_orchestrator/project_config.py`: clean.
- Relative links and anchors of every touched doc resolve (script run over README, GUIDE, HLD, ADR-0021,
  ADR-0010, ROADMAP, ui/README, the two cross-linked HLDs): 0 broken.
- No code change besides the `_INIT_TEMPLATE` comment block and a docstring in `project_config.py`;
  no test edited.

## Completion note (large-feature closure)

**Final scope delivered.** Opt-in dashboard and hub authentication (E-Da5Tn9, ADR-0021): local
accounts with scrypt passwords, optional TOTP (`off|optional|required`) with recovery codes and
operator-issued enrollment tokens, per-realm in-memory sessions with an origin-bound API proof
header, CSRF/Fetch-Metadata checks, deny-by-default per-app policy tables, throttling and lockouts,
a JSONL audit log, `ao auth` (11 commands), `ao ui`/`ao service run --auth`,
`ao service install --auth`, an SPA login/enrollment/account UI with a lazy QR chunk, a static hub
login page, file-browser denial of the credential/state stores and `service.env`, and a provider
seam for later OIDC. 23 tasks, no cut-line taken. With auth off behaviour is byte-identical.

**Key decisions.** Per-realm sessions (cookies scope by host, not port); D25 session proof in the
MVP (not a cut-line); `Principal.roles: list[str]` with keyword-only additive fields (OQ-8);
workspace config may only tighten and is treated as attacker input (A12/M3); enable by CLI flag
baked into ExecStart rather than env alone (OQ-3 reversed after the security review); stdlib-only
crypto, one new frontend dependency (`qrcode-generator`, lazy).

**Validation performed.** Full suite 7471 passed / 13 skipped after remediation; auth coverage
99.28 % (gate 90), UI coverage 94.42 % (gate 80); vitest 697 passed; real-subprocess e2e (3
consecutive runs), scrub sweep, store-busy and perf-informational tests; independent
dev-security audit plus re-audit against real subprocesses (0 CRITICAL; H1, H2, M-code-1, N1 and
cheap L items fixed with failing-first tests) and a reviewer pass; this docs refresh checked
against the code (table above).

**Outstanding follow-ups.** (1) Run AC-34, the real-browser smoke, where Chrome and Playwright
exist (`uv sync --extra browser`; `pytest -q -m browser tests/auth/test_browser_smoke.py`), fix
selectors on first run, add the forced-enrollment/QR, hub and Firefox/WebKit cases and screenshots.
(2) Re-verify cross-epic rows X1-X6 and re-take the auth-off header snapshot when E-Ag7Pw3 (approvals)
merges; one `ui/static` rebuild after both epics merge. (3) Merge order with the sibling epics
E-Rc4Hk8 (result cache) and E-Ag7Pw3 (touch `ui/app.py`, `cli.py`, `service/*`). (4) Bump `dompurify`
past 3.4.12. (5) ROADMAP s3.1 follow-ups: hub-run handoff first, then store-scoped SSO, RBAC, API
tokens, OIDC, fail-closed remote binds, hub child-auth state, seed encryption. (6) Manager: confirm
OQ-11 (agent-lane calendar basis). (7) After merging, re-install the global `ao`
(`bash install.sh --force`) and restart `ao.service`, then verify `ao auth status`.

**Memory-worthy facts.** The epic is Done on branch `ad/dashboard-auth-totp` (not pushed, no PR);
`install.sh --force` is the real reinstall command; `trusted_proxies` is env-only; AC-34 browser
smoke never ran; accepted residuals are listed in HLD "As built" D.


## Risks / Blockers
- None blocking. AC-34 (browser smoke) NOT RUN is an explicit, accepted gap carried as a follow-up.
- Sibling-epic merge conflicts (E-Rc4Hk8, E-Ag7Pw3) are the manager's to sequence; X1-X6 stay open
  until E-Ag7Pw3 merges.

## Next actions
1. manager: merge `ad/dashboard-auth-totp` to the target branch (security gate M4 is satisfied: T-2wE08U
   sign-off), coordinating the order with E-Rc4Hk8 and E-Ag7Pw3.
2. See the completion note's outstanding follow-ups (AC-34, X1-X6, dompurify, OQ-11, ROADMAP s3.1).
