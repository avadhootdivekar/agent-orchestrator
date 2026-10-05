# STATUS

- ID: `T-jVqH8w-ao-ui-auth-wiring`
- Updated At: `2026-10-05`
- State: `Done`
- Owner: `developer` (lane B)
- Scope: `MVP` · Sprint: `S3` · Estimate: `3 d`

## This update
- By: developer · Role: developer · Date: 2026-10-05 · Comment: Implemented and verified (see
  Evidence). Delivered: `auth/launch.py` (`AuthLaunch`, `prepare_auth`, `exit_code_for`,
  `is_loopback_bind`, message constants); `ao ui` wiring (`--auth/--no-auth`, `--auth-totp`,
  `--auth-dir`; `--reload` gets `**launch.uvicorn_kwargs`, `AO_UI_BOUND_PORT` and the CLI relay
  env; the UNAUTHENTICATED warning only when `launch.runtime is None`; `denied_paths` passed to
  `DashboardService`); `create_app_from_env` (requires a numeric `AO_UI_BOUND_PORT` when auth is
  on). Decisions: (1) `AuthLaunch.uvicorn_kwargs` is typed `dict[str, Any]` (not `dict[str,
  object]`) so `**launch.uvicorn_kwargs` type-checks against `uvicorn.run`; field names are
  unchanged. (2) `ui/app.py` mirrors `cli.UI_DEFAULT_HOST/PORT` as `FACTORY_DEFAULT_HOST/PORT`
  (it cannot import `cli`; same precedent as `service/supervisor.py`). (3) Startup audit events
  carry `realm` and `details.reason` only. (4) Row-9 `Note:` stays silent for an unknown store
  count (it is a refusal on the config path, row 14). (5) The module docstring of `cli.py`
  already listed `ao auth` (aa0bc23).
- By: architect · Role: agent · Date: 2026-10-05 · Comment: v2.1 gates folded (HLD §28.9).
  Estimate, lane and sprint unchanged (3 d, lane B, S3). Changes:
  - **Moved out:** the file-browser denial (`ui/files.py`, `ui/service.py`, old AC 8–9) →
    `T-Hd4wQ2-auth-browse-denial-log-scrub` (lands in S1 for the approvals epic, HLD §16 X2). This
    task still computes and passes `AuthLaunch.denied_paths` (now incl. `service.env`, L7).
  - **Security M3:** `prepare_auth` enforces `ConfigRisk.DISABLED_BY_CONFIG` (exit 78 + audit
    `auth.startup.disabled_by_config`) and audits `TOTP_DOWNGRADED_BY_CONFIG`; new AC 3 and AC 9
    (= AC-45 incl. the config `env:` block isolation test).
  - **Security L3 / L4:** the `--reload` call gets `**launch.uvicorn_kwargs` (tested with and
    without trusted proxies); port 0 with auth on → exit 78.
  - **Security M4:** merge edges — merges only after T-QJ1vyQ and T-KQ6ZrY; new upstream T-Hd4wQ2.
- By: architect · Role: agent · Date: 2026-10-05 · Comment: TASK.md aligned with the HLD v2 cross-check (HLD §28.8): added AC-4c (row 9 via `prepare_auth`); `AO_UI_BOUND_PORT_ENV` is now an HLD §12.6 constant.
- By: architect · Role: agent · Date: 2026-10-05 · Comment: v2 rescope after the Phase-4
  consultations (HLD §28).
  - **Estimate:** 2 d → 3 d.
  - **`auth/launch.py`.** The task now owns `prepare_auth` / `AuthLaunch` / `exit_code_for`
    (reviewer R-3). It is the one startup sequence shared by `ao ui`, `create_app_from_env` and
    `ao service run` (T-PDGw9p). The adapter methods that v1 had on `AuthSettings`
    (`uvicorn_kwargs()`, `cli_env_overrides()`, `protected_paths()`) are gone, and the CLI reads
    `launch.*` instead.
  - **Trusted proxies** come from env or CLI only; a workspace config value is refused with exit 78
    (dev-security #10).
  - **Denial** now also covers the state directory (D5).
  - **`create_app_from_env`** requires `AO_UI_BOUND_PORT` only when auth is enabled.
  - **Hermetic fixture:** it moved from T-PlEROT to T-kzEzwy (developer D-3, tester T-1).
- By: architect · Role: agent · Date: 2026-10-04 · Comment: Task created as part of the
  E-Da5Tn9 design package (design and tickets only; **no code written**). The store-directory denial
  applies with auth on **and** off. It is the one deliberate behaviour change with auth off (NFR-1).

## Evidence
- By: developer · Role: developer · Date: 2026-10-05 · Comment: commands run in the worktree:
  - `.venv/bin/python -m pytest -q tests/auth tests/ui tests/test_cli*.py tests/service` ->
    3046 passed, 2 skipped (includes `tests/ui/test_ui_command.py`, `test_files.py`,
    `test_service.py` unmodified).
  - `.venv/bin/python -m pytest -q --ignore=tests/auth --ignore=tests/ui --ignore=tests/service`
    -> 4432 passed, 8 skipped (rest of the suite; with the line above, the whole suite is green).
  - `.venv/bin/python -m pytest -q tests/auth/test_launch.py --cov=agent_orchestrator.auth.launch`
    -> 37 passed, `auth/launch.py` 100 % (112 stmts, 0 missed).
  - `tests/auth/test_ui_command_auth.py` -> 26 passed. Auth-off proof:
    `test_auth_off_uvicorn_kwargs_are_exactly_host_and_port` asserts
    `kwargs == {"host": "127.0.0.1", "port": 8765}`; the existing stub-uvicorn tests in
    `tests/ui/test_ui_command.py` still pass.
  - `.venv/bin/ruff check src tests` clean; `ruff format --check` clean on every touched path
    (only the pre-existing, untracked-generated `_build_info.py` would reformat);
    `.venv/bin/mypy src` -> only the 4 pre-existing `_version.py` errors.

## Risks / Blockers
- None. `AuthLaunch` field names are frozen once T-PDGw9p starts.
- Merge gate (v2.1, security M4): do not merge before T-QJ1vyQ and T-KQ6ZrY have merged. On the
  critical path, with T-KQ6ZrY co-critical (HLD §24.3).

## Next actions
1. developer: implement once T-XchniS, T-G7qByZ, T-rpKCjP and T-Hd4wQ2 land; rebase onto
   T-QJ1vyQ and T-KQ6ZrY before merging. Run the verification and the full suite, and record the
   results and the `launch.py` coverage here.
