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

## Remediation
- By: developer · Role: developer · Date: 2026-10-05 · Comment: findings of the security + code
  review fixed on branch `ad/dashboard-auth-totp`. Every regression test below was seen to fail
  before its fix. This task is **not** marked Done: re-audit is the manager's call.

| Finding | Fix commit | Regression test id(s) |
|---|---|---|
| H1 M3 bypass via a config-chosen empty `store_dir` (`_detect_config_risks` now probes the config-ignored store as well as the resolved one; unknown = accounts may exist) | `9e62b5a` | `tests/auth/test_settings.py::test_h1_config_chosen_empty_store_cannot_hide_accounts_in_the_default_store`, `::test_h1_the_totp_downgrade_warning_also_sees_the_default_store`, `::test_h1_an_unknown_default_store_count_counts_as_accounts_may_exist`, `::test_h1_config_store_with_accounts_is_still_a_risk`, `::test_h1_the_env_store_is_probed_with_the_config_layer_ignored`, `::test_h1_empty_everywhere_is_no_risk`; `tests/auth/test_launch.py::test_h1_a_hostile_config_store_cannot_start_auth_off_while_accounts_exist` (ConfigRisk raised, exit 78) |
| H2 log redaction broke uvicorn `AccessFormatter` ("Logging error" per request) | `4464169` | `tests/auth/test_scrub.py::TestUvicornAccessRecords::*` (4); `tests/auth/test_e2e_subprocess.py` (`assert_log_is_clean` after every `ao_ui_server`, so the e2e and `test_browser_smoke.py` assert a clean stderr/server log) |
| M-code-1 raw `PermissionError` escaped `prepare_auth` | `b894abd` | `tests/auth/test_paths.py::TestOsErrorsBecomeConfigErrors::*`; `tests/auth/test_ui_command_auth.py::test_h_code_1_a_read_only_store_exits_78_not_a_traceback`; `tests/auth/test_service_run_auth.py::test_h_code_1_a_read_only_store_exits_78_before_any_supervisor` |
| M-1 `ao service install --auth` + stale-snapshot fail-open note (hint text and HLD A6 text) | `e99d687` | `tests/service/test_systemd.py::TestRenderUnit::test_auth_appends_the_flag_to_exec_start_only_when_asked`; `tests/service/test_cli_probe_auth.py::test_install_auth_flag_bakes_auth_into_exec_start`, `::test_install_next_steps_mention_enabling_dashboard_auth` |
| L-1 `destroy_user_sessions` now filters on `record.realm` (HLD 11.10 line 2517: "this realm only"; other realms are revoked by the epoch bump, S15) | `be47773` | `tests/auth/test_sessions.py::test_destroy_user_sessions_leaves_other_realms_alone` |
| L-5 `users.json` / `lockouts.json` read cap `STORE_FILE_MAX_BYTES` (16 MiB); over-cap = `StoreCorruptError` (count probe: unknown; lockouts: fail closed) | `b415f8f` | `tests/auth/test_store.py::TestSnapshot::test_an_oversized_users_file_is_corrupt_and_unknown_not_read`, `::test_a_file_exactly_at_the_cap_is_still_read`; `tests/auth/test_lockouts.py::TestCorruption::test_an_oversized_lockouts_file_fails_closed`; `tests/auth/test_foundation.py` constants pin |
| middleware `Content-Length` `isascii() and isdigit()` | `558632d` | `tests/auth/test_body_cap.py::test_a_unicode_digit_content_length_is_not_a_crash` |
| startup-refusal audit failure logged at WARNING | `558632d` | `tests/auth/test_launch.py::test_a_failing_startup_audit_is_logged_at_warning` |
| `HubLoginRequired` derives from `OrchestratorError`; `NullAuditLog.__init__` calls super | `558632d` | `tests/service/test_cli_probe_auth.py::test_hub_login_required_is_an_orchestrator_error`; `tests/auth/test_audit.py::...::test_null_audit_log_runs_the_base_initializer` |
| `FACTORY_DEFAULT_HOST/PORT` == `UI_DEFAULT_HOST/PORT` | `558632d` | `tests/auth/test_ui_command_auth.py::test_the_factory_defaults_mirror_the_cli_defaults` |
| CI: `permissions: contents: read`; separate `pip-audit` job (`uv export --extra ui --no-dev --no-hashes --no-emit-project` then `uvx pip-audit --no-deps --disable-pip`; run locally: no known vulnerabilities) | `99eaac7` | n/a (CI config; yaml parsed) |

**Accepted residuals (not fixed, by decision):** per-session counter concurrency (sessions.py record
mutators); L-2 hardlink denial; L-4 health version hash; M-2 shared throttle bucket behind an
unconfigured proxy; dompurify bump; DRY header-constant refactor.

**Notes / small known gaps**
- The `disabled_by_config` refusal message still prints the account count of the *resolved* store
  (which a hostile config can point at an empty dir), so it can say "0 account(s)" while the
  refusal is triggered by accounts in the default store. Message-only; the refusal itself is correct.
- A config-only TOTP downgrade pointed at an empty config store records the risk/warning in the
  settings, but startup then refuses for the empty store before the downgrade audit event is
  reached; the audit event is covered at the `prepare_auth` level only for the non-hostile store.
- The stderr-clean assertion is in the real-subprocess e2e and browser smoke helper
  (`ao_ui_server`); the in-process log-scrub sweep is unchanged.

**Verification (2026-10-05):** `pytest -q` 7471 passed / 13 skipped (previous full run 7443 / 13);
`ruff check src tests` clean; `ruff format --check src tests` clean except the generated
`_build_info.py` (excluded); `mypy src` only the 4 pre-existing `_version.py` errors.
