# TASK: T-PlEROT-auth-settings-layering

## Metadata
- Task ID: `T-PlEROT-auth-settings-layering`
- Epic ID: `E-Da5Tn9-dashboard-auth-totp`
- Owner: `developer` (lane A)
- Created: `2026-10-04`
- Last Updated: `2026-10-05`
- Status: `Done`
- Estimate: `3 days` · Sprint `S1` (v2.1: was 2.5 d; +0.5 d for the `ConfigRisk` detection)

## Requirements Mapping
- Requirement IDs: FR-1, FR-2 (configuration part), FR-30 (tighten-only; v2.1 config-only disable / TOTP downgrade detection), FR-16 (trusted-proxy parsing), NFR-1, NFR-4, NFR-8
- ACs: AC-1 (resolution part), AC-4a (rows 1–5, 10 and 12 of §11.3.4), **AC-4d** (v2.1: the `ConfigRisk` detection of rows 14–15), AC-38, **AC-45** (detection part)
- Design: HLD §11.3 (all subsections; v2.1 step 5 and rows 14–15), §12.5, §12.6, §20.3 #18, §28.9 (security M3); HLD D2, D16, D17; ADR-0021 D8; ADR-0003 (an empty env value is an error)

## Description
Implement `auth/settings.py` (L2): an immutable `AuthSettings` computed from CLI, env, workspace
config and defaults. It records the source of each value, and refuses unsafe or invalid input.

- `UIAuthConfig`:
  - `extra="forbid"` and `hide_input_in_errors=True`;
  - a before-validator that maps YAML `off` (parsed as False) to `"off"` and rejects True;
  - the field bounds of §11.3.1;
  - a `trusted_proxies` field that exists **only** to produce the precise CLI/env-only error.
- `AuthCliOverrides` and the frozen `AuthSettings`, with `state_dir`, `sources`, `config_path` and
  `warnings`. **v2:** `AuthSettings` is pure data. The launch adapters (`child_env`,
  `uvicorn_kwargs`, `denied_paths`) live in `auth/launch.py` (T-jVqH8w; reviewer finding R-3).
- `ConfigProblem(kind: UNPARSEABLE | INVALID, message)` and `load_auth_block(config_path)`. It
  never raises, and never echoes input values (§11.3.3).
- `decide(explicit_enabled, problem, user_count) -> Decision`: the pure, value-only decision for a
  problematic workspace `ui.auth` block (pseudocode step 2). `Decision` is one of `USE_CONFIG`,
  `IGNORE_CONFIG`, `CONTINUE_WITHOUT_CONFIG` or `REFUSE`, with a message.
  `resolve_auth_settings` maps `REFUSE` to `AuthConfigError`, and the warning outcomes to
  `settings.warnings`.
- `resolve_auth_settings(*, cli, env, workspace_root, count_users=count_store_users)` (§11.3.2):
  - the strict env layer;
  - the workspace layer with `decide()`;
  - the per-field merge with sources;
  - **v2 tighten-only rules** (`TIGHTEN_RULES`) and the `trusted_proxies`-in-config refusal
    (dev-security #10);
  - the `store_dir` checks;
  - **v2 `state_dir` resolution**;
  - the trusted-proxy normalization;
  - the issuer default and checks;
  - **v2.1 step 5 — `ConfigRisk` detection** (security M3; HLD §11.3.2 step 5, §11.3.4 rows 14–15).
    A new `class ConfigRisk(StrEnum)` (`DISABLED_BY_CONFIG = "disabled_by_config"`,
    `TOTP_DOWNGRADED_BY_CONFIG = "totp_downgraded_by_config"`) and a new field
    `AuthSettings.config_risks: frozenset[ConfigRisk]`:
    - `DISABLED_BY_CONFIG` when `sources["enabled"]` starts with `config:`, the value is `False`,
      and `count_users(store_dir)` is `> 0` or `None`;
    - `TOTP_DOWNGRADED_BY_CONFIG` when `enabled` resolves to `True`, `sources["totp"]` starts with
      `config:`, the value is not `required`, and the count is `> 0` or `None`; it also appends the
      warning `totp=<value> comes only from <config path>; a repository change can lower it. Pin it
      with AO_UI_AUTH_TOTP or --auth-totp.` to `settings.warnings`;
    - **`resolve_auth_settings` never raises for a `ConfigRisk`.** `ao auth` keeps working;
      `prepare_auth` (T-jVqH8w) enforces (exit 78 for `DISABLED_BY_CONFIG`, audit for both), and
      `ao auth status` (T-j9dfsw) prints both flags.
- `count_users` defaults to `store.count_store_users` (T-8NQP8J), the **tri-state** probe: `None`
  means unknown, which fails closed in row 3 and counts as "accounts may exist" in step 5.
- **Path helpers use the v2.1 `xdg` signature** (`resolve_config_dir(None, "ao/auth", "ao/auth",
  environ=env)` / `resolve_state_dir(..., environ=env)`; design-review M1), always passing the
  `env` mapping.
- `ProjectConfig` is **never** used; `project_config.py` is not modified. Only
  `find_project_config` is reused, read-only.
- **Not here any more:** the root hermetic test fixture moved to T-kzEzwy (developer D-3, tester
  T-1). `TotpPolicy` lives in `model.py` (T-kzEzwy).

## Inputs / Outputs
- **Inputs:**
  - HLD §11.3, §12.5, §12.6;
  - T-kzEzwy (`constants`, `errors`, `model.TotpPolicy`);
  - T-8NQP8J (`xdg.resolve_state_dir` / `resolve_config_dir` with the `environ=` mapping,
    `paths.xdg_default_store_dir`, `store.count_store_users`). Stub them with the HLD signatures if
    they are not merged yet.
- **Outputs:** `src/agent_orchestrator/auth/settings.py` (incl. `ConfigRisk`);
  `tests/auth/test_settings.py`.

## Acceptance Criteria
1. **Precedence (AC-1 part).** Parametrized:
   - for `enabled`, `totp` and `store_dir`: CLI > env > workspace config > default;
   - for every other field: env > config > default.
   - `settings.sources[field]` is `cli`, `env:<VAR>`, `config:<absolute path>` or `default`.
   - With `workspace_root=None` (the hub), a `.ao/config.yaml` in the cwd
     (`monkeypatch.chdir`) is never read.
2. **Env parsing (row 1).**
   - Booleans accept `1/true/yes/on` and `0/false/no/off` in any case, with surrounding spaces.
   - `AO_UI_AUTH=""`, `AO_UI_AUTH=" "` and `AO_UI_AUTH=maybe` raise `AuthConfigError`, naming the
     variable and listing the accepted values.
   - Out-of-range integers raise.
   - `AO_UI_AUTH_TRUSTED_PROXIES="127.0.0.1, ::1"` becomes `("127.0.0.1", "::1")`. `10.0.0.0/8`
     raises an error that mentions CIDR.
   - A 200-character bad value is quoted with at most `MAX_QUOTED_CONFIG_CHARS` characters.
3. **YAML `off`.** `ui: {auth: {totp: off}}` (bare YAML `off`) gives `TotpPolicy.OFF`; `totp: on`
   raises `AuthConfigError`; `totp: "required"` gives `REQUIRED`.
4. **`decide()` (rows 2–5).** Parametrized:
   - (explicit False, any problem, any count) → `IGNORE_CONFIG`, with a warning that names the
     source of the explicit value (row 2);
   - (explicit True, any problem) → `REFUSE` (row 4);
   - (None, UNPARSEABLE, 0) → `CONTINUE_WITHOUT_CONFIG` with a warning (row 3);
   - (None, UNPARSEABLE, 3) → `REFUSE` (row 3);
   - (None, UNPARSEABLE, `None`) → `REFUSE`; an unknown count fails closed (row 3, dev-security
     #10);
   - (None, INVALID, any) → `REFUSE` (row 5);
   - (any, no problem, any) → `USE_CONFIG`.

   `decide()` is called with plain values only. A test also drives each branch end-to-end through
   `resolve_auth_settings`, with a `count_users` stub returning 0, 3 or `None`.
5. **Unknown keys (row 5).**
   - `ui.auth.enable: true` (a typo) raises `AuthConfigError`, and the message contains
     `ui.auth.enable`.
   - A non-mapping `ui` or `ui.auth` is `INVALID`.
   - No error message contains the offending **value** (sentinel check). This is also true for a
     pydantic `ValidationError` from `UIAuthConfig` (`hide_input_in_errors`).
6. **Tighten-only (AC-38, row 12).** For each of the 7 fields in `TIGHTEN_RULES`
   (`session_idle_minutes`, `session_absolute_hours`, `lockout_threshold`, `address_threshold`:
   lower is stricter; `lockout_base_seconds`, `lockout_max_seconds`, `min_password_length`: higher
   is stricter):
   - a loosening config value raises `AuthConfigError` naming `ui.auth.<field>`, the default, and
     "env/CLI";
   - a value equal to the default, and a tightening value, are accepted;
   - the same loosening value supplied through **env** is accepted.

   Also:
   - `trusted_proxies` in config raises `AuthConfigError` naming `AO_UI_AUTH_TRUSTED_PROXIES`, for
     **any** value including `[]`;
   - config `enabled: true` and `totp: required` are accepted.
7. **`store_dir` (row 10).**
   - A config-sourced relative path raises.
   - A config-sourced absolute path inside the workspace, including one reached through a symlink,
     raises.
   - The default store inside a `$HOME` workspace gives a warning, not an error.
   - `AO_AUTH_DIR=~/x` is expanded.
8. **`state_dir` (v2).**
   - `AO_AUTH_STATE_DIR=/st` → `/st`, with source `env:AO_AUTH_STATE_DIR`.
   - A store directory overridden by CLI, env or config, with no `AO_AUTH_STATE_DIR` →
     `store_dir / STATE_SUBDIR`.
   - The default store → `$XDG_STATE_HOME/ao/auth`, taken from the `env` mapping (not
     `os.environ`).
9. **Cross-field.**
   - `lockout_base_seconds > lockout_max_seconds` raises.
   - An issuer containing `:` or non-printable characters raises.
   - The default issuer is `ao@devbox` when `socket.gethostname` is patched to
     `"devbox.example.com"`.
10. **Pure data.**
    - `AuthSettings` is frozen.
    - It has no `uvicorn_kwargs`, `cli_env_overrides` or `protected_paths` attribute (R-3).
    - Resolving the same inputs twice gives equal objects.
11. **No I/O beyond the inputs.** Only `find_project_config`, the config file read and the injected
    `count_users` touch the filesystem. A test with a `count_users` spy shows it is called **only**
    in the UNPARSEABLE branch and in the two v2.1 step-5 cases (a config-only `enabled: false`; a
    config-only `totp` below `required` with auth enabled), and never otherwise.
12. **`ConfigRisk` detection (v2.1, AC-4d and the detection part of AC-45; rows 14–15).**
    Parametrized over the `count_users` stub returning `0`, `3` and `None`:
    - `ui.auth.enabled: false` from config, no CLI/env `enabled`: count 3 or `None` →
      `config_risks == {DISABLED_BY_CONFIG}`; count 0 → empty; **no exception in any case**.
    - The same config with `--no-auth` (CLI) or `AO_UI_AUTH=0` (env) → no risk for any count
      (row 2/9, not row 14).
    - Auth enabled (by config, env or CLI) and `ui.auth.totp: optional` (or `off`) from config, with
      no CLI/env `totp`: count 3 or `None` → `TOTP_DOWNGRADED_BY_CONFIG` plus the warning text
      (`comes only from`, the config path, `AO_UI_AUTH_TOTP`); count 0 → no risk.
    - `totp: required` from config → no risk; `AO_UI_AUTH_TOTP=optional` from env → no risk.
    - Auth disabled and `totp: optional` from config → no TOTP risk.
    - No warning or error message contains a config value beyond the policy name (sentinel).
13. ruff and mypy are clean. Coverage of `settings.py` is ≥ 95 %.

**Other rows of HLD §11.3.4** are implemented and tested by their owners:
- rows 6, 7, 8, 13 (store readiness and permissions): `check_ready`, T-XchniS;
- row 9 (the accounts-exist notice): `prepare_auth`, T-jVqH8w;
- row 11 (`totp=off` with enrolled users): `startup_warnings`, T-XchniS;
- **rows 14–15 (v2.1):** detection here (AC 12); enforcement (exit 78, audit events) in
  `prepare_auth`, T-jVqH8w; the `ao auth status` flags in T-j9dfsw.

## Risks
- **Over-strict validation breaking existing configs.** The `ui.auth` block is parsed separately
  from `ProjectConfig`, so configs without it are unaffected (AC-1, and the existing suites).
- **Tighten-only surprises an operator who wants looser settings.** The error names the env or CLI
  alternative (AC-6).
- **Env-mapping injection.** Every path helper must honour the passed `env` mapping, or tests
  become machine-dependent (AC-8).
- **A `ConfigRisk` must never raise here (v2.1).** Raising would break `ao auth status --workspace
  W`, which is exactly where the operator needs to see the flag. Enforcement belongs to
  `prepare_auth`.

## Dependencies
- **Upstream:** T-kzEzwy; T-8NQP8J (`xdg`, `paths`, `count_store_users`).
- **Downstream:**
  - T-XchniS (settings feed the provider and runtime);
  - T-j9dfsw (`ao auth status` prints the sources and, v2.1, the `config_risks` flags);
  - T-jVqH8w (`prepare_auth`; v2.1: enforces `config_risks`);
  - T-PDGw9p (`ao service run`).

## Pseudocode / Algorithm
HLD §11.3.2 (`resolve_auth_settings`, including step 3b, tighten-only, and step 4, `state_dir`) and
§11.3.3 (`load_auth_block`). `decide()` is step 2 of §11.3.2 expressed as a pure function:

```text
decide(explicit_enabled, problem, user_count):
  IF problem IS None:                  RETURN USE_CONFIG
  IF explicit_enabled IS False:        RETURN IGNORE_CONFIG(warning="ignoring invalid ui.auth ... explicitly disabled by <source>")
  IF explicit_enabled IS True:         RETURN REFUSE("invalid ui.auth configuration in <path>: <message>")
  IF problem.kind == UNPARSEABLE:
     IF user_count IS None OR user_count > 0: RETURN REFUSE("<path> cannot be parsed ... refusing to guess ...")
     RETURN CONTINUE_WITHOUT_CONFIG(warning="<path> cannot be parsed; continuing without workspace auth settings")
  RETURN REFUSE("invalid ui.auth configuration in <path>: <message>")      # INVALID
```

Messages are composed by `resolve_auth_settings`, which knows the path and the source.
`count_users` is evaluated lazily, **only** for UNPARSEABLE with no explicit value, and (v2.1) for
step 5:

```text
step 5 (after the merge and the derived checks; HLD §11.3.2):
  weak_enabled = source("enabled").startswith("config:") AND value("enabled") IS False
  weak_totp    = source("totp").startswith("config:") AND value("totp") != REQUIRED AND value("enabled") IS True
  IF weak_enabled OR weak_totp:
     n = count_users(store_dir)                       # once
     IF n IS None OR n > 0:
        IF weak_enabled: risks.add(DISABLED_BY_CONFIG)
        IF weak_totp:    risks.add(TOTP_DOWNGRADED_BY_CONFIG); warnings += <pin-it warning>
  RETURN AuthSettings(..., config_risks=frozenset(risks))   # never raises for a risk
```

## Schemas / Interface Notes
- Config YAML, env names and defaults: HLD §12.5. `trusted_proxies` is env/CLI only.
- `sources` values: `cli`, `env:<VAR>`, `config:<abs path>`, `default`.

## Handoff Boundary
- **Upstream:** the HLD, T-kzEzwy and T-8NQP8J.
- **Downstream:** one immutable `AuthSettings` (incl. `config_risks`), used by every server and CLI
  entry point through `prepare_auth` and `ao auth`.

## Verification

```
python -m pytest -q tests/auth/test_settings.py tests/auth/test_import_boundary.py
python -m pytest -q tests/auth --cov=agent_orchestrator.auth.settings --cov-report=term-missing
ruff check src/agent_orchestrator/auth tests/auth && mypy src/agent_orchestrator/auth
```

## Artifacts
- `meta/tickets/E-Da5Tn9-dashboard-auth-totp/T-PlEROT-auth-settings-layering/`
