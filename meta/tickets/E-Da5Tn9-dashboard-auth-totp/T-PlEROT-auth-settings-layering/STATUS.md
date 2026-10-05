# STATUS

- ID: `T-PlEROT-auth-settings-layering`
- Updated At: `2026-10-05`
- State: `Done`
- Owner: `developer` (lane A)
- Scope: `MVP` · Sprint: `S1` · Estimate: `3 d`

## This update
- By: developer · Role: developer · Date: 2026-10-05 · Comment: Implemented
  `src/agent_orchestrator/auth/settings.py` (L2) and `tests/auth/test_settings.py` (241 test
  cases); all 13 ACs covered, coverage of `settings.py` 100 %. Notes and assumptions:
  - `Decision(action: DecisionAction, message: str)`: `decide()` is value-only, so its message
    carries no path or source; `resolve_auth_settings` prefixes the config path, appends the
    explicit-value source (row 2) and the probe store/count (row 3).
  - `sources["state_dir"]` is `env:AO_AUTH_STATE_DIR`, `default`, or the one extra value
    `derived` (store directory overridden, state follows it as `<store>/state`). `sources` keys
    are config field names (`session_idle_minutes`, not seconds).
  - Added `AuthSettings.store_dir_from_config` (read-only property over `sources`), so `ao auth`
    can apply security M6 (never create/chmod a config-sourced store dir) without re-parsing
    source strings.
  - `load_auth_block` reports YAML syntax errors as `YAML syntax error at line N, column M` (the
    PyYAML text can quote file content); non-UTF-8, non-mapping top level and read errors are
    UNPARSEABLE; an empty file or absent `ui.auth` is `(None, None)`.
  - `UIAuthConfig.store_dir` also has `min_length=1` (an empty string is INVALID).
  - A default issuer from an odd hostname is neutralized (`:`/non-printable become `-`, capped to
    64 chars) rather than failing startup; an explicit issuer is validated strictly.
  - `~` is expanded with `Path.expanduser()` (process HOME); only the XDG variables come from the
    `env` mapping. `auth/__init__.py` lazy exports were not touched (shared file).
  - `ConfigRisk` is recorded, never raised (`prepare_auth` T-jVqH8w enforces; `ao auth status`
    T-j9dfsw reports).
- By: architect · Role: agent · Date: 2026-10-05 · Comment: v2.1 gates folded (HLD §28.9).
  Estimate 2.5 → 3 d.
  - **`ConfigRisk` detection (security M3; HLD §11.3.2 step 5, §11.3.4 rows 14–15):**
    `AuthSettings.config_risks` records `DISABLED_BY_CONFIG` (config-only `enabled: false` with
    accounts or an unknown count) and `TOTP_DOWNGRADED_BY_CONFIG` (auth on, config-only `totp`
    below `required`, accounts or unknown; plus a pin-it warning). `resolve_auth_settings` never
    raises for them; T-jVqH8w enforces and T-j9dfsw reports. New AC 12 (AC-4d / AC-45 detection);
    AC 11 widened (`count_users` also runs in the two step-5 cases).
  - Path helpers use the v2.1 `xdg` signature (`environ=`; design-review M1).
  - Lane A in S1 holds this task plus T-s6sJmB (5.5 dev-days), an overload the HLD accepts
    explicitly (§24.3 capacity note).
- By: architect · Role: agent · Date: 2026-10-05 · Comment: TASK.md aligned with the HLD v2 cross-check (HLD §28.8): AC label aligned to HLD AC-4a (rows 1–5, 10, 12).
- By: architect · Role: agent · Date: 2026-10-05 · Comment: v2 rescope; the estimate rises from
  2 to 2.5 d. Changes:
  - **Hermetic fixture removed.** The root fixture `_hermetic_auth_env` moved to T-kzEzwy, so it
    exists before any auth code (developer finding D-3, tester finding T-1).
  - **Launch adapters removed.** `cli_env_overrides`, `uvicorn_kwargs` and `protected_paths` moved
    to `auth/launch.py` (T-jVqH8w), leaving `AuthSettings` as pure data (reviewer finding R-3).
  - **Added:**
    - tighten-only workspace rules, with `trusted_proxies` accepted from CLI/env only
      (dev-security #10, A12);
    - `state_dir` resolution (D5);
    - the pure `decide()` (reviewer finding R-7);
    - a tri-state user count, where unknown fails closed (dev-security #10).
  - **AC-4 scope:** rows 1–5, 10 and 12 of HLD §11.3.4. Rows 6–8 and 11–13 are tested by their
    owners (T-XchniS, T-jVqH8w).
  - Design and tickets only; **no code written**.
- By: architect · Role: agent · Date: 2026-10-04 · Comment: Task created as part of the E-Da5Tn9
  design package (design and tickets only; **no code written**). v1 note: this task also owned the
  root hermetic test fixture (HLD §16 #10). That ownership is superseded by v2 above.

## Evidence
- `.venv/bin/python -m pytest -q tests/auth/test_settings.py tests/auth/test_import_boundary.py tests/auth/test_foundation.py --cov=agent_orchestrator.auth.settings --cov-report=term-missing`
  -> 424 passed; `settings.py` 355 stmts, 0 missed (100 %).
- `.venv/bin/python -m pytest -q tests/auth` -> 1023 passed, 2 failed before the last test
  additions: both in `tests/auth/test_lockouts.py` (T-CsT5gk's in-progress, uncommitted work;
  unrelated to settings).
- `.venv/bin/ruff check` and `ruff format --check` on `settings.py` and `test_settings.py` -> clean.
- `.venv/bin/mypy src` -> only the 4 pre-existing `_version.py` errors; `settings.py` clean.
- No shared file touched (`project_config.py`, `cli.py`, `auth/__init__.py` unchanged).

## Risks / Blockers
- None.

## Next actions
1. T-jVqH8w (`prepare_auth`): enforce `config_risks`; T-j9dfsw: print sources and flags.
