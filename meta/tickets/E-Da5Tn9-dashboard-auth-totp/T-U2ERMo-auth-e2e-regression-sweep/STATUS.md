# STATUS

- ID: `T-U2ERMo-auth-e2e-regression-sweep`
- Updated At: `2026-10-05`
- State: `Done, with one recorded NOT RUN: AC-34 (browser smoke; Playwright is not installed here)`
- Owner: `tester` (lane Q)
- Scope: `MVP` · Sprint: `S3` · Estimate: `3 d`

## This update
- By: developer (rework of the rejected tester close-out) · Role: developer · Date: 2026-10-05 ·
  Comment: The earlier close-out ("Done", with the subprocess e2e, store-busy test, scrub sweep and
  NFR-5 numbers "deferred") was rejected. This update writes the real tests and records what ran.
  - **Real tests added** (all under `tests/auth/`): `test_e2e_subprocess.py` (3 runs of the scenario
    + exit-78 + auth-off), `test_cli_e2e_busy.py`, `test_log_scrub_sweep.py` (both modes + negative
    controls), `test_perf_informational.py` (numbers below), `test_browser_smoke.py` (skips here).
  - **Hardening** (item 6): `SessionManager.lookup` now ignores a record whose `realm` differs from
    its own (`src/agent_orchestrator/auth/sessions.py`), with two tests in `test_sessions.py`.
  - **Defects found by the sweep: none.** No sentinel leaked in either mode. The sweep was
    mutation-checked: a temporary `_log.debug("... %s", password)` in `local_provider.authenticate`
    made it fail (then reverted). No production change besides the realm check.
  - **Not done / not run** (details below): AC-34 browser (no Playwright), forced-enrollment and hub
    parts of the browser smoke (not written: nothing to verify them against), the `ao service run`
    hub e2e variant (not required by HLD 20.3; see below), `npm ci` (existing `node_modules`
    reused).
  - `grep -rniE "placeholder|assert True" tests/auth` returns nothing.
- By: architect · Role: agent · Date: 2026-10-05 · Comment: v2.1 gates folded (HLD §28.9).
  Estimate 2.5 d → 3 d (HLD §24.2). Moved in the store-busy multiprocess CLI test from T-j9dfsw
  (`test_cli_e2e_busy.py`) and the informational NFR-5 p95 measurements from T-G7qByZ (`-m slow`,
  recorded, never gating). Upstream adds T-Hd4wQ2. Sweep scope adds `transport.proxy_suspected` and
  the two `auth.startup.*_by_config` audit events. Regression covers AC-1..AC-46 and S27–S30; X1–X6
  recorded if the approvals epic has merged. New ACs 8–10.
- By: architect · Role: agent · Date: 2026-10-05 · Comment: v2 rescope after the Phase-4
  consultations (HLD §28): new sentinels (enrollment tokens, session proofs), `session_proof` in the
  allowed locations, redaction mode restores the LogRecord factory; subprocess e2e asserts the proof
  and uses recovery codes for later logins; browser smoke gains the cross-port proof check, a CSP
  negative control and Firefox/WebKit when installed; CI gains the frontend rebuild-diff step.
- By: architect · Role: agent · Date: 2026-10-04 · Comment: Task created as part of the E-Da5Tn9
  design package (design and tickets only).

## Evidence

All commands run from the worktree root with `.venv/bin/...`. Counts are from this session.

| Gate | Command | Result |
|---|---|---|
| Full suite | `.venv/bin/python -m pytest -q` | **7443 passed, 13 skipped** (712 s). Baseline 5159p/8s; last earlier full run 7429p/10s. The 13 skips are the 10 earlier ones + `test_perf_informational` (opt-in) + 2 in `test_browser_smoke` (no Playwright). |
| Hermetic auth env | `AO_UI_AUTH=1 AO_AUTH_DIR=/nonexistent .venv/bin/python -m pytest -q` | **7443 passed, 13 skipped** (737 s) |
| e2e marker | `.venv/bin/python -m pytest -q -m e2e` | 5 passed (3 scenario runs + exit-78 + auth-off); only these tests carry the marker |
| Subprocess e2e, 3 consecutive invocations | `.venv/bin/python -m pytest -q -m e2e tests/auth/test_e2e_subprocess.py` ×3 | 5 passed (9.6 s), 5 passed (8.6 s), 5 passed (8.7 s). Each invocation also runs the scenario 3× (`[1]`,`[2]`,`[3]`) on fresh dirs and ports, so 9 scenario passes in a row. |
| Store busy | `pytest -q tests/auth/test_cli_e2e_busy.py` | 2 passed, 0.67 s total (the busy command itself < 2 s, asserted) |
| Scrub sweep | `pytest -q tests/auth/test_log_scrub_sweep.py` | 5 passed: the sweep in modes `redacted` and `raw`, plus 3 detector controls |
| NFR-5 informational | `pytest -q -m slow tests/auth/test_perf_informational.py -s` | 1 passed (numbers below); without `-m slow` it skips |
| Browser (AC-34) | `pytest -q -m browser tests/auth/test_browser_smoke.py -rs` | **NOT RUN**: 2 skipped, "playwright not installed (uv sync --extra browser)". `/usr/bin/google-chrome` exists. |
| Auth coverage (gate 90 %) | `pytest tests/auth -q --cov=agent_orchestrator.auth --cov-report=term --cov-fail-under=90` | **99.28 %** (2145 passed, 3 skipped); gate met |
| UI coverage (gate 80 %) | `pytest tests/ui tests/test_general_instructions.py tests/test_e2e_cli_prompt_and_instructions.py -q --cov=agent_orchestrator.ui --cov-report=term --cov-fail-under=80` (the CI command) | **94.42 %** (674 passed, 2 skipped); gate met |
| ruff | `.venv/bin/ruff check .` / `ruff format --check .` | Clean on every file this task touched. Repo-wide: 1 pre-existing failure each, in the tracked scratch file `output/E-YAAGhk-overseer-runner-template/repro_emit_lost_on_breaker_trip.py` (I001 + format); not mine, not changed. `ruff check tests/auth` and `ruff format` on `tests/auth`: clean. |
| mypy | `.venv/bin/mypy src` | 4 pre-existing errors, all in the generated `src/agent_orchestrator/_version.py`; none in touched code. (`mypy` is not configured for `tests/`.) |
| vitest | `cd ui && npm run test` | 48 files, **697 passed** |
| typecheck | `cd ui && npm run typecheck` | clean |
| build | `cd ui && npm run build` | built; hub assets emitted (`hub-auth.js` 6.91 kB, `hub-auth.css` 1.87 kB) |
| Rebuild diff (AC-30 CI part) | `git diff --exit-code -- src/agent_orchestrator/ui/static src/agent_orchestrator/auth/assets` after the build | exit 0 (no diff). `npm ci` was NOT run; the existing `node_modules` was used. |
| npm audit | `cd ui && npm audit --omit=dev --audit-level=high` | exit 0; 1 moderate advisory (dompurify, GHSA-55q2-fjhq-7xh7), below the gate |
| CI | `.github/workflows/ci.yml` lines 25-26 (auth coverage) and the frontend rebuild-diff step | present (from the earlier close-out; verified, unchanged) |

### NFR-5 numbers (informational, no threshold asserted; this box, loopback, one run)
| Metric | Samples | p50 | p95 | max |
|---|---|---|---|---|
| per-request overhead: `classify` + `sessions.lookup` + proof match + `revalidate` | 1000 | 14.1 µs | **14.7 µs** | 20.0 µs |
| authenticated TestClient round trip, `GET /api/workspace` (context) | 200 | 3.23 ms | **3.67 ms** | 4.25 ms |
| login with real `BoundedScryptHasher` at `CURRENT_PARAMS` (ln=15, r=8, p=3) | 20 | 152.0 ms | **155.3 ms** | 155.8 ms |

### AC-33 (subprocess e2e) detail
`create_user_with_totp` runs `ao auth add-user` and `ao auth enable-2fa` (stdin-driven: secret parsed
from stdout, current-step code sent) as real subprocesses in a tmp HOME/XDG/`--auth-dir`, with every
`AO_*` variable stripped. A real `ao ui --auth` serves on a free port. Asserted over HTTP, in order:
status `anonymous` (+ `transport`); wrong password 401, no cookie; login → `second_factor_required`;
partial confinement 401; verify with the step+1 code → `authenticated`, pair rotated, old pair 401;
`/api/runs` with proof 200 / no proof 401 `not_authenticated` / wrong proof 401 / proof again 200;
status without proof `anonymous`; logout 200 + `Clear-Site-Data: "cache"` + `Max-Age=0`; old pair 401;
second login with a **recovery code** (`recovery_codes_remaining` 9), replay of that code 401
`invalid_code`, a lower-case dash-less second code works (8 left); SIGTERM stops the server (exit 0
or -SIGTERM, uvicorn re-raises). Plus: `ao ui --auth` with no users exits **78**, stderr names
`ao auth add-user`, nothing listens; auth-off serves the disabled status body and no auth headers.
**HLD 20.3 does not require an `ao service run` hub variant** (hub auth is covered in-process by
`tests/service/test_hub_auth.py` and `tests/auth/test_service_run_auth.py`); none was added.

### AC-24 (scrub sweep) detail
Flows: every HTTP endpoint E1-E10 with failing variants (wrong password, unknown user, wrong/replayed
code, bad/used/expired/missing token, missing/wrong proof, bad Origin, unknown body key, policy
violation, lockout 429, `insecure_transport` 403, corrupt store 503 with a sentinel seed in a field
the schema rejects) and `transport.proxy_suspected` status; every `ao auth` command (incl. corrupt
store, `status --workspace` with both `*_by_config` flags); `prepare_auth` with both
`auth.startup.*_by_config` events (with a sentinel in `AO_AUTH_PASSWORD`). Mode (a): LogRecord
factory + `auth_logger` filters; mode (b): default factory, every `SecretRedactingFilter` removed,
`launch.install_log_redaction` stubbed. DEBUG on every logger, `caplog` handler also on
non-propagating loggers. Checked: log message/args/`exc_text`/formatted traceback/all record fields,
stdout/stderr, every `audit.jsonl`, every request URL, response headers and bodies field by field
against `ALLOWED_SECRET_LOCATIONS`, CLI output against `CLI_ALLOWED`. One harness note: `CliRunner`
echoes the typed `enable-2fa` code into stdout (as a terminal shows typing), so that code kind is
allowed in `enable-2fa` output only. Controls: a planted secret is detected in mode (b) and redacted
in mode (a); the response check flags a secret in a wrong field, a wrong endpoint, a URL or a header
and allows the issuing `Set-Cookie`.

## Acceptance-criteria table (AC-1..AC-46)
Test files are under `tests/auth/` unless a path is given. Node names are the `test_...` function
names (a `*` marks a prefix). Status is "covered" = the named tests exist and pass in the full run
above. Frontend ACs name the vitest file.

| AC | Concrete tests | Status |
|---|---|---|
| 1 | `test_settings.py::test_enabled_precedence_cli_env_config_default`, `test_totp_precedence`, `test_store_dir_precedence`, `test_hub_never_reads_cwd_config`; `test_cli_e2e.py::test_status_source_labels`, `test_status_prints_sources_permissions_counts_and_notes` | covered |
| 2 | `test_auth_off_regression.py::test_status_is_the_disabled_body_byte_for_byte`, `test_response_header_names_are_unchanged`, `test_openapi_differs_by_exactly_the_status_route`, `test_framework_doc_routes_still_exist`; `test_launch.py::test_defaults_are_off_and_empty`; `test_ui_command_auth.py::test_auth_off_uvicorn_kwargs_are_exactly_host_and_port`; `test_e2e_subprocess.py::test_auth_off_is_unchanged_over_a_real_server` | covered |
| 3 | `test_ui_command_auth.py::test_auth_with_an_empty_store_exits_78_before_serving`, `test_auth_with_port_zero_exits_78`, `test_reload_passes_the_same_kwargs_and_relays_the_cli_flags`, `test_reload_and_plain_kwargs_are_equal_with_trusted_proxies`; `test_service_run_auth.py::test_auth_with_an_empty_store_exits_78_before_any_supervisor`, `test_auth_with_port_zero_exits_78_and_spawns_nothing`; `test_launch.py::test_port_zero_is_refused_before_the_runtime_is_built`; `test_e2e_subprocess.py::test_ui_auth_without_users_exits_78_and_refuses_to_start` | covered |
| 4 | 4a `test_settings.py::test_decide_*`, `test_e2e_*`; 4b `test_local_provider.py::test_check_ready_*`, `test_warns_about_*`; 4c `test_launch.py::test_config_only_disable_*`, `test_a_config_only_totp_downgrade_warns_and_audits`; 4d `test_settings.py` ConfigRisk cases | covered |
| 5 | `test_passwords.py::test_round_trip_and_format`, `test_real_params_hash_and_verify`, `test_parse_bounds_rejects`, `test_verify_is_nfkc_equivalent`, `test_verify_malformed_logs_exactly_once`; `test_store.py::test_rehash_applies_without_an_epoch_bump`, `test_stale_cas_after_set_password_keeps_the_new_hash` | covered |
| 6 | `test_totp.py::test_hotp_rfc4226_vectors`, `test_totp_rfc6238_sha1_vectors`, `test_match_accepts_window_and_returns_step`, `test_match_rejects_outside_window` | covered |
| 7 | `test_store.py::test_valid_code_is_accepted_and_the_step_recorded`, `test_same_code_again_is_replayed_and_nothing_is_written`, `test_two_processes_consuming_the_same_code_yield_exactly_one_ok`; `test_routes_second_factor.py::test_a_code_used_in_one_realm_is_a_replay_in_the_other` | covered |
| 8 | `test_recovery.py::test_generate_ten_distinct_well_formed_codes`, `test_normalize_maps_case_separators_and_aliases`, `test_find_unused_match_never_matches_used_and_picks_first_unused`; `test_routes_second_factor.py::test_recovery_code_login`, `test_regenerate_replaces_the_codes_and_rotates`; `test_cli_e2e.py::test_lifecycle_and_audit_trail` (no plaintext in users.json); `test_e2e_subprocess.py` (single use over HTTP) | covered |
| 9 | `test_totp_service.py::test_a_full_session_with_the_right_password_gets_a_challenge`; `test_routes_second_factor.py::test_an_enrolled_user_is_still_challenged_when_the_policy_goes_off`, `test_forced_enrollment_state_confines_the_session`, `test_disable_is_refused_for_a_totp_required_user`; vitest `enroll-screen.test.tsx`, `hub-auth.test.ts` | covered |
| 10 | `test_provider_seam.py::test_a_redirect_provider_signs_in_across_sites`, `test_the_seam_modules_never_import_the_local_provider` | covered |
| 11 | `test_principal.py::*` (field order, kw-only, hash, roles lists); `test_middleware.py::test_full_session_with_proof_yields_the_principal_contract`, `test_two_requests_on_one_session_never_share_the_roles_list`; `test_auth_off_regression.py::test_a_probe_sees_no_principal_and_auth_disabled`; `test_sessions.py::test_principal_for_*` | covered |
| 12 | `test_partial_confinement.py::test_partial_session_is_confined_to_its_own_step`, `test_partial_confinement_holds_for_a_stale_state_after_revocation` | covered |
| 13 | `test_paths.py::test_loose_dir_raises_unsafe_permissions_with_chmod`, `test_group_writable_euid_parent_is_returned_as_a_warning`; `tests/test_fsutil.py::*test_file_is_0600_under_any_umask*`, fchmod spy; `test_store.py::test_twenty_spawn_processes_adding_users_lose_nothing`, `test_stale_temp_files_are_removed_while_the_lock_is_held`, `test_write_failure_is_store_unavailable_and_keeps_the_original`; `tests/ui/test_file_browser_denial.py::*` | covered |
| 14 | `test_sessions.py::test_issue_returns_distinct_43_char_base64url_secrets_of_32_bytes`, `test_record_stores_sha256_digests`, `test_rotation_retires_the_old_token_and_proof`; `test_routes_core.py::test_password_change_rotates_and_revokes_the_others`, `test_logout_with_the_proof_kills_the_session`; `test_e2e_subprocess.py` (rotation on 2nd factor, old pair 401) | covered |
| 15 | `test_sessions.py::test_full_session_idle_expiry_boundary`, `test_absolute_deadline_always_applies`; `test_middleware.py::test_get_polling_never_slides_the_session`, `test_keepalive_and_mutations_with_the_proof_slide`, `test_flagged_navigation_slides_only_when_browser_attested`, `test_navigation_to_an_unflagged_page_never_slides` | covered |
| 16 | `test_cookie_isolation.py::*` (8 tests: realm cookies, tossing, new instance, attributes, https prefix, realm id, duplicate) | covered |
| 17 | `test_route_enumeration_dashboard.py::*`, `_hub.py::*`, `_real_routes.py::*`, `_full_config.py::*`; `test_routes_registry.py::test_the_flat_check_names_a_route_added_through_include_router`; `test_middleware.py::test_middleware_order_without_and_with_auth`, `test_partial_match_picks_the_first_sorted_method_with_an_entry`; `test_auth_off_regression.py::test_auth_on_drops_exactly_those_two_framework_routes` | covered |
| 18 | `tests/service/test_cli_probe_auth.py::*` (401 → `HubLoginRequired`, other failures `None`, list/status messages and exit 0) | covered |
| 19 | `tests/service/test_hub_auth.py::test_anonymous_index_redirects_html_and_401s_json`, `test_login_page_is_static_and_script_safe`, `test_assets_are_served_with_their_exact_content_type`, `test_cookie_only_index_renders_the_signed_in_page`, `test_auth_off_index_html_equals_the_pre_change_golden` | covered |
| 20 | `test_throttle.py::*`; `test_guard.py::test_active_address_throttle_refuses_before_verify_and_writes_nothing`; `test_local_provider.py::test_address_throttle_refuses_without_hashing`, `test_two_provider_instances_share_the_account_lockout`; `test_lockouts.py::test_backoff_table_from_the_hld_worked_example`; `test_cli_e2e.py` (unlock) | covered |
| 21 | `test_local_provider.py::test_unknown_and_wrong_password_are_indistinguishable_and_cost_the_same`, `test_phantom_lockout_key_is_the_keyed_digest`, `test_malformed_names_share_one_bucket_and_still_make_one_write`; `test_lockouts.py::test_fourth_phantom_evicts_the_oldest`, `test_digest_is_hmac_sha256_of_the_name_under_the_key`; `test_guard.py::test_phantom_failure_is_identified_by_a_keyed_digest_prefix_never_an_unkeyed_hash` | covered |
| 22 | `test_local_provider.py::test_a_wrong_current_password_is_counted_and_audited`; `test_totp_service.py::test_a_full_session_with_a_wrong_password_is_counted_once`, `test_disable_with_a_wrong_password_burns_no_code`; `test_routes_core.py::test_password_change_wrong_current_counts_a_lockout_failure`; `test_routes_second_factor.py::test_voluntary_enrollment_requires_the_current_password`, `test_a_forced_session_may_not_use_the_password_field` | covered |
| 23 | `test_csrf.py::*` (12); `test_middleware.py::test_security_headers_on_deny_and_pass_through_responses`; `test_auth_off_regression.py::test_response_header_names_are_unchanged` | covered |
| 24 | `test_log_scrub_sweep.py::test_the_scrub_sweep_finds_no_secret_anywhere[redacted]` and `[raw]` (+ `test_scrub.py::*` unit) | covered (new, real) |
| 25 | `test_launch.py::test_enabled_non_loopback_without_proxies_warns_plain_http`, `test_disabled_non_loopback_prints_the_deprecation_notice`; `test_client_info.py::test_one_insecure_login_warning_per_process`, `test_no_insecure_login_warning_from_loopback` | covered |
| 26 | `test_cli_e2e.py::test_lifecycle_and_audit_trail`, `test_no_command_has_a_secret_option`, `test_remove_last_user_is_allowed_when_auth_is_off`, `test_enrollment_token_*`, `test_list_users_*`; **store busy:** `test_cli_e2e_busy.py::test_set_password_reports_busy_when_another_process_holds_the_lock` (spawn process holds the real flock; exit 1, "busy" + `users.lock`, elapsed < 2 s, store unchanged) | covered (busy part new, real) |
| 27 | `test_import_boundary.py::test_ao_auth_runs_with_frameworks_blocked`, `test_non_http_modules_import_with_frameworks_blocked`, `test_real_tree_respects_layers_and_framework_boundary` | covered |
| 28 | `test_audit.py::test_a_full_event_is_one_schema_valid_line`, `test_files_shift_up_to_the_backup_count_and_every_line_parses`, `test_permission_error_is_one_error_naming_the_event_only`, `test_strict_raises_for_every_kind_of_malformed_event`, `test_failures_above_the_cap_are_counted_and_summarised_next_minute` | covered |
| 29 | vitest: all 12 files of HLD 17.10 exist (`auth-reducer`, `auth-gate`, `auth-api`, `login-screen`, `totp-step`, `enroll-screen`, `qr-code`, `account-menu`, `keepalive`, `proof`, `fetch-mode-ban`, `hub-auth`, plus `auth-client-contract`); `npm run test` 697 passed | covered |
| 30 | build + `git diff --exit-code` over `ui/static` and `auth/assets` after `npm run build`: exit 0; `npm audit --omit=dev --audit-level=high`: exit 0; `tests/service/test_hub_auth.py::test_wheel_artifacts_include_auth_assets`. Bundle-size budgets (§17.11) are recorded by T-vCgsU6, not re-measured here. `npm ci` not re-run. | covered, with `npm ci` not run |
| 31 | `tests/service/test_supervisor_auth.py::*` (child env, exit-78 terminal state, other codes restart); `tests/service/test_systemd.py` (`RestartPreventExitStatus`) | covered |
| 32 | auth coverage 99.28 % (gate 90) and UI coverage 94.42 % (gate 80); both CI steps present | covered |
| 33 | `test_e2e_subprocess.py::test_login_totp_proof_logout_and_recovery_code_over_a_real_server[1|2|3]`; 3 invocations of the file, 5 passed each | covered (new, real) |
| 34 | `test_browser_smoke.py` (CSP detector negative control; login screen + password login + cross-port proof check) | **NOT RUN** (Playwright missing). Written but never executed, so treat it as unverified code. Forced-enrollment/QR, hub page, Firefox/WebKit and the other three screenshots are not written. |
| 35 | `test_proof.py::*` (8); `test_routes_core.py::test_status_needs_the_proof_to_report_a_session`, `test_logout_without_a_proof_clears_the_cookie_but_keeps_the_session`; `test_e2e_subprocess.py` (proof matrix over a real server) | covered |
| 36 | `test_store.py::test_*enrollment_token*` (issue/consume/expiry/replace); `test_totp_service.py::test_a_bad_enrollment_token_is_one_counted_failure`, `test_a_valid_token_gives_a_challenge_and_is_consumed_by_begin`; `test_routes_second_factor.py::test_a_bad_enrollment_token_is_a_uniform_counted_failure`, `test_a_valid_token_begins_enrollment_exactly_once`; `test_cli_e2e.py::test_lifecycle_and_audit_trail` | covered |
| 37 | `test_totp_service.py::test_begin_over_remote_http_is_refused_before_any_work`, `test_begin_is_allowed_over_loopback_http_and_remote_https`; `test_routes_second_factor.py::test_e4_e5_e7_refuse_a_remote_client_over_plain_http`, `test_a_remote_client_may_still_log_in_over_plain_http`; CLI `enable-2fa` has no transport check (`test_cli_e2e.py::test_lifecycle_and_audit_trail`) | covered |
| 38 | `test_settings.py::test_tighten_rules_cover_exactly_the_seven_fields`, `test_loosening_config_is_r*` and the following tighten cases | covered |
| 39 | `test_revocation.py::*` (4); `test_local_provider.py::test_a_rehash_losing_the_race_to_a_password_change_is_skipped`, `test_revival_with_the_same_name_is_revoked`; `test_store.py::test_stale_after_remove_and_readd` | covered |
| 40 | `test_event_loop.py::*` (7) | covered |
| 41 | `test_responses.py::*` (11); `test_origin.py::test_parity_on_agreeing_cases`, `test_documented_differences` | covered |
| 42 | `test_store.py::test_unknown_fields_are_accepted_and_survive_a_rewrite_byte_for_byte`, `test_unknown_required_feature_says_upgrade_ao`, `test_newer_schema_version_says_upgrade_ao` | covered |
| 43 | `test_client_info.py::*` (13); `test_routes_second_factor.py::test_a_loopback_peer_behind_a_proxy_counts_as_remote`; `test_log_scrub_sweep.py` asserts `proxy_suspected` true on a loopback `X-Forwarded-For` | covered |
| 44 | `test_cookie_only_principal.py::*` (6); `test_middleware.py::test_public_routes_with_a_cookie_but_no_proof_get_no_principal_and_no_slide`, `test_construction_rejects_api_or_non_get_cookie_only_keys`; `tests/service/test_hub_auth.py::test_every_other_hub_route_sees_no_principal_and_never_slides` | covered |
| 45 | `test_ui_command_auth.py::test_a_config_flip_to_disabled_is_refused_and_audited`, `test_an_explicit_disable_after_the_flip_starts_with_the_note`, `test_a_config_disable_with_no_accounts_starts_unauthenticated`, `test_a_corrupt_store_with_a_config_disable_is_refused`, `test_a_config_only_totp_downgrade_warns_and_audits`, `test_the_config_env_block_cannot_disable_auth`; `test_launch.py::test_a_config_only_totp_downgrade_warns_and_audits`; `test_cli_e2e.py::test_status_flags_disabled_by_config`, `test_status_flags_totp_downgraded_by_config` | covered |
| 46 | `test_cli_e2e.py::test_config_store_dir_missing_is_not_created`, `test_config_store_dir_with_loose_mode_is_not_fixed`, `test_config_store_dir_private_is_used_without_chmod`, `test_explicit_auth_dir_restores_create_and_fix` | covered |

## §15 caller matrix (rows 1-19), re-verified against the merged tree
| # | Verdict | Evidence |
|---|---|---|
| 1 `_probe_hub_status` | as designed | `service/cli.py` `HubLoginRequired`; `tests/service/test_cli_probe_auth.py` |
| 2 `Supervisor.tick` | as designed (unchanged) | no HTTP; supervisor suite green |
| 3 `default_child_spawner` | as designed | `extra_env` layered over `os.environ` only when set; `tests/service/test_supervisor_auth.py` |
| 4 boot-resume | as designed (unchanged) | `git diff main...HEAD` empty for `service/boot_resume.py` |
| 5 hub status provider | as designed | unchanged; only authenticated requests reach it (`test_hub_auth.py`) |
| 6 hub index links | as designed | links unchanged; each dashboard is its own realm (`test_cookie_isolation.py`) |
| 7 SPA `api.ts` | as designed | proof on every call, 401 handler, keepalive: vitest `auth-api`, `proof`, `keepalive`, `auth-client-contract` |
| 8 Vite dev proxy | as designed | `ui/vite.config.ts` `configure` hook sets `origin` to the proxy target |
| 9 existing e2e suites | as designed | pass unmodified under the hermetic fixture (`tests/conftest.py::_hermetic_auth_env`) and under `AO_UI_AUTH=1 AO_AUTH_DIR=/nonexistent` |
| 10 `test_ui_command.py` | as designed | passes unmodified; new cases in `test_ui_command_auth.py` |
| 11 in-process `create_app(service)` suites | as designed | pass unmodified |
| 12 `test_hub.py` | as designed | passes unmodified; new `test_hub_auth.py` |
| 13 `scripts/helper/epics/E-iafh2F/shoot.py` | as designed | file unchanged (`git diff main...HEAD -- scripts` empty); not executed here |
| 14 `e2e-subprocess-test.sh` | as designed | unchanged; not executed here (needs an installed `ao` and a hub) |
| 15 opt-in browser smoke | **deviation**: `test_browser_smoke.py` exists but covers only CSP control, login, cross-port check, and was NOT RUN (no Playwright). Forced-enrollment/TOTP/hub pages are not covered. Follow-up for T-2wE08U / a machine with Playwright. |
| 16 external finplan skills | doc-only, as designed | out of repo; hub status 401 behaviour covered by `test_hub_auth.py` |
| 17 `ui/processes.py` | as designed (accepted) | file unchanged (`git diff main...HEAD` empty) |
| 18 approvals epic | n/a here | E-Ag7Pw3 is not merged on this branch (see X1-X6) |
| 19 cross-run cache epic | n/a here | none expected |

### Cross-epic rows X1-X6 (as-merged note)
The approvals epic E-Ag7Pw3 is **not merged** on `ad/dashboard-auth-totp`, so X1-X6 cannot be
recorded as merged. State here: X1 `xdg.resolve_config_dir` exists once (`src/agent_orchestrator/xdg.py`);
X2 the auth denial lives in the epic-neutral `FileBrowser._is_denied` (`ui/files.py`), ready for the
approvals predicate; X3 `ui/app.py` carries only this epic's changes and X4 `ui/security.py` is unchanged by it (the
auth-off header snapshot in `test_auth_off_regression.py` is taken against this baseline and must be
re-taken when the approvals `X-Frame-Options` line merges); X5 the bundle is regenerated, not
hand-merged (rebuild-diff clean); X6 `project_config.py` is unchanged by this epic. Re-verify all six
when E-Ag7Pw3 merges.

## Findings for T-2wE08U (security review)
1. `SessionManager.lookup` realm check: **added and tested here** (cheap, safe: one comparison; a
   foreign-realm record is neither honoured nor deleted).
2. `SessionManager.destroy_user_sessions` docstring says "(this realm)" but the code revokes the
   user's sessions in every realm of a shared `SessionStore`. Harmless today (one in-memory store per
   process); decide the intended semantics before a shared store (SSO seam) lands. Not changed here.
3. `auth_logger()` is defined and tested but no auth module uses it; redaction in production rests on
   `install_log_redaction()` (installed by `prepare_auth` when auth is on). The sweep found no raw
   leak with both removed, so this is defence in depth only.
4. Pre-existing repo hygiene (not this epic): the ruff failure in
   `output/E-YAAGhk-overseer-runner-template/repro_emit_lost_on_breaker_trip.py` and the generated
   `_version.py` mypy errors.

## Risks / Blockers
- AC-34 is NOT RUN; install the `browser` extra and run `-m browser` on a machine with Chrome to close it.
- `test_browser_smoke.py` has never executed; expect selector/timing fixes on first real run.
- The e2e uses real scrypt (about 0.15 s per hash) and real ports; it allows 45 s boot and 15 s stop.

## Next actions
1. Someone with Playwright installed: `uv sync --extra browser`, run `pytest -q -m browser tests/auth/test_browser_smoke.py`, fix, and add the forced-enrollment, hub and Firefox/WebKit cases and the screenshots.
2. T-2wE08U: take findings 1-4 above.
3. When E-Ag7Pw3 merges: re-verify X1-X6 and re-take the auth-off header snapshot.
