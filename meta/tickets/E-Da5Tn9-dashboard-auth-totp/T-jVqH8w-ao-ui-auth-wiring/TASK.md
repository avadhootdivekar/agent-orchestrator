# TASK: T-jVqH8w-ao-ui-auth-wiring

## Metadata
- Task ID: `T-jVqH8w-ao-ui-auth-wiring`
- Epic ID: `E-Da5Tn9-dashboard-auth-totp`
- Owner: `developer` (lane B)
- Created: `2026-10-04`
- Last Updated: `2026-10-05`
- Status: `Draft`
- Estimate: `3 days` · Sprint `S3`

## Requirements Mapping
- Requirement IDs:
  - FR-1, FR-2;
  - FR-16 (uvicorn proxy-header kwargs, incl. the `--reload` path);
  - FR-21 (startup warnings);
  - FR-25 (`child_env` computation, consumed by T-PDGw9p);
  - FR-30 (v2.1: config-only disable refused, config-only TOTP downgrade warned and audited);
  - NFR-1.
- ACs:
  - AC-2 (`prepare_auth` and `ao ui` parts);
  - AC-3 (`ao ui` part, incl. v2.1 port 0 and `--reload` kwargs);
  - AC-25 (`prepare_auth` warnings part);
  - AC-4c: §11.3.4 row 9 (auth disabled by default/CLI/env while accounts exist → the `Note:`
    warning) and **v2.1 rows 14–15 enforcement**, in `test_launch.py`;
  - **AC-45** (`prepare_auth` + `ao ui` part; v2.1).

  The precedence checks through the CLI (AC-5) support AC-1, which is owned by T-PlEROT and
  T-j9dfsw. **v2.1:** the file-browser denial (AC-13 denial part) moved to
  `T-Hd4wQ2-auth-browse-denial-log-scrub`.
- Invariants: S14, S17, S19, S29.
- Design: HLD §11.20 (`launch.py`, v2.1 steps 1b and L4, `--reload` note), §11.3.1 (`ConfigRisk`),
  §11.3.4 (fail-closed table, rows 9, 14, 15), §11.3.5 (adapter rules, incl. `service.env` in
  `denied_paths`), §11.15.2 (startup audit events), §14.8, §16 rows 1e, 4b, 4c, §18 #2 and #11,
  §20.3 #18, §24.3 (merge edges); §28.9 (security M3, M4, L3, L4; design-review M2);
  ADR-0021 D3, D8.

## Description
1. **NEW `src/agent_orchestrator/auth/launch.py`** (L3, framework-free; reviewer R-3). It contains
   `AuthLaunch`, `prepare_auth(...)` and `exit_code_for(exc) -> int` (`EXIT_CONFIG` for any
   `AuthConfigError`), exactly as in HLD §11.20 (v2.1), in this order:
   1. `settings = resolve_auth_settings(...)`. An `AuthConfigError` propagates.
   2. **(v2.1, security M3) step 1b:** if `ConfigRisk.DISABLED_BY_CONFIG in settings.config_risks`:
      write a **best-effort** `auth.startup.disabled_by_config` audit event through
      `AuditLog.for_state_dir(settings.state_dir)` (any exception is swallowed and logged at
      DEBUG), then raise `AuthConfigError` with the §11.3.4 row-14 message: it names the config
      file, the account count (or "an unknown number of"), the store, and `--no-auth` /
      `AO_UI_AUTH=0` for an intentional disable.
   3. **Enabled:**
      - **(v2.1, security L4)** `port == EPHEMERAL_PORT` (0) → `AuthConfigError("--port 0 cannot be
        used with dashboard authentication: the session cookie is named after the listening port;
        choose a fixed port")`.
      - `runtime = build_auth_runtime(settings, Realm(realm_kind, port, workspace_root), clock=clock)`
        → `runtime.provider.check_ready()`.
      - On `AuthNotReadyError`, write a **best-effort** `auth.startup.refused` audit event to the
        state directory. Any exception from that write is swallowed and logged at DEBUG. Then
        re-raise the **original** error.
      - Then add `provider.startup_warnings()` (incl. the v2.1 parent-directory notices, L6).
      - **(v2.1, security M3)** if `ConfigRisk.TOTP_DOWNGRADED_BY_CONFIG in settings.config_risks`:
        a best-effort `auth.startup.totp_downgraded_by_config` audit event (the warning text is
        already in `settings.warnings`).
      - If `not is_loopback_bind(bind_host)` and there are no `settings.trusted_proxies`, add
        `PLAIN_HTTP_WARNING`.
      - Call `install_log_redaction()`.
   4. **Disabled** (by default, CLI or env):
      - a non-loopback bind adds `DEPRECATION_NOTICE`;
      - if `count_store_users(settings.store_dir)` is not in `(0, None)`, add the §11.3.4 row-9
        `Note: …` text.
   5. **Adapters** (§11.3.5):
      - `uvicorn_kwargs`: `{}` when off; `{"proxy_headers": False}` when on without proxies;
        `{"proxy_headers": True, "forwarded_allow_ips": "<csv>"}` with proxies.
      - `child_env`: only the **CLI-sourced** `enabled`, `totp` and `store_dir`, as `AO_UI_AUTH`
        (`"1"`/`"0"`), `AO_UI_AUTH_TOTP` and `AO_AUTH_DIR`.
      - `denied_paths`: `paths.default_denied_paths(env)` (appended to `paths.py` by T-Hd4wQ2: the
        XDG store and state defaults, `$AO_AUTH_DIR` / `$AO_AUTH_STATE_DIR` when set, and
        `service.env`, security L7) plus the effective store and state directories; resolved and
        de-duplicated.
   - **Warning order:** `settings.warnings`, then provider, then transport (or deprecation and
     accounts notes).
   - **`is_loopback_bind(host)`** is local to `launch.py`: `"localhost"` → True; otherwise
     `ipaddress.ip_address(host).is_loopback`; unparseable → False. It does **not** import
     `ui.security` (R2).
   - **Message constants** in `launch.py`:
     - `PLAIN_HTTP_WARNING` must contain the phrase `not encrypted`, name the host, and point to
       `AO_UI_AUTH_TRUSTED_PROXIES` / a TLS proxy.
     - `DEPRECATION_NOTICE` must contain `deprecated`.
2. **`cli.py::ui_cmd`** (§16 rows 4b and 4c).
   - **New options:** `--auth/--no-auth` (`bool | None`, default `None`), `--auth-totp`
     (`off|optional|required`, default `None`), `--auth-dir PATH`.
   - **Flow:**
     1. Make one call:
        `launch = prepare_auth(cli=AuthCliOverrides(...), env=os.environ, workspace_root=Path(ws), realm_kind="ui", port=port, bind_host=host)`.
        On `AuthConfigError`, print `ERROR: <msg>` to stderr and `raise typer.Exit(EXIT_CONFIG)`.
     2. Print every `launch.warnings` line to stderr.
     3. Print the existing UNAUTHENTICATED warning **only when `launch.runtime is None`**, with its
        text unchanged.
     4. Build the service as
        `DashboardService(ws, denied_paths=[str(p) for p in launch.denied_paths])` (the
        `denied_paths` parameter itself is T-Hd4wQ2's).
   - **Non-reload:** `create_app(service, allowed_hosts=..., auth=launch.runtime)`, then
     `uvicorn.run(app, host=host, port=port, **launch.uvicorn_kwargs)`.
   - **Reload (v2.1, security L3):** keep the existing `AO_UI_WORKSPACE` / `AO_UI_BOUND_HOST` relay,
     then `os.environ[AO_UI_BOUND_PORT_ENV] = str(port)` and `os.environ.update(launch.child_env)`,
     then `uvicorn.run("…create_app_from_env", host=…, port=…, reload=True, factory=True,
     **launch.uvicorn_kwargs)` — the **same** kwargs as the non-reload path, so the reloaded server
     never falls back to uvicorn's default `proxy_headers=True` / `FORWARDED_ALLOW_IPS=127.0.0.1`.
   - **Help and docstring (row 4c):** the `--host` help text says "unauthenticated unless --auth /
     AO_UI_AUTH", and the module docstring lists `ao auth`.
3. **`ui/app.py::create_app_from_env`** (§16 row 1e; about 8 lines; after T-G7qByZ's edits to the
   same file):
   - `prepare_auth(cli=AuthCliOverrides(), env=os.environ, workspace_root=Path(AO_UI_WORKSPACE or cwd), realm_kind="ui", port=<AO_UI_BOUND_PORT or UI_DEFAULT_PORT>, bind_host=AO_UI_BOUND_HOST or loopback)`.
   - If `launch.runtime is not None` and `AO_UI_BOUND_PORT` was absent or invalid → raise
     `AuthConfigError` naming `AO_UI_BOUND_PORT`. A guessed port would mis-name the realm cookie.
   - Pass `denied_paths` and `auth=launch.runtime`. `AuthConfigError` re-raises (§11.20).
   - **Auth off without `AO_UI_BOUND_PORT`:** behaviour is unchanged.
4. **`AO_UI_BOUND_PORT_ENV = "AO_UI_BOUND_PORT"`** and **`EPHEMERAL_PORT = 0`** are listed in HLD
   §12.6, so T-kzEzwy's constants pin covers them. Use the constants; no string or numeric literal
   (NFR-7).
5. **Moved out (v2.1, HLD §24.2):** the file-browser denial (`ui/files.py`, `ui/service.py`,
   `tests/ui/test_file_browser_denial.py`, §16 rows 2–3, the old AC 8–9) is
   `T-Hd4wQ2-auth-browse-denial-log-scrub`'s, landed early in S1 as the cross-epic `denied_paths`
   mechanism (HLD §16 X2).

The root hermetic fixture is T-kzEzwy's (§16 row 10). Do not duplicate it.

## Inputs / Outputs
- **Inputs:**
  - T-PlEROT: `resolve_auth_settings` (incl. `config_risks`, `ConfigRisk`), `count_store_users`.
  - T-XchniS: `build_auth_runtime`, `Realm`, `check_ready`, `startup_warnings`.
  - T-CsT5gk: `AuditLog.for_state_dir`. T-Hd4wQ2: `install_log_redaction` (`scrub.py`),
    `paths.default_denied_paths`, `DashboardService(denied_paths=)`.
  - T-G7qByZ: `create_app(auth=)`.
  - T-rpKCjP: `install_auth_routes`.
- **Outputs:**
  - `src/agent_orchestrator/auth/launch.py`
  - edits to `src/agent_orchestrator/cli.py` (rows 4b, 4c) and `ui/app.py` (row 1e)
  - tests: `tests/auth/test_launch.py`, `tests/auth/test_ui_command_auth.py`

## Acceptance Criteria
1. **Unchanged suites:**
   - `tests/ui/test_ui_command.py`, `tests/ui/test_files.py` and `tests/ui/test_service.py` pass
     **unmodified**.
   - With no auth settings, the `fake_uvicorn.calls[0]` kwargs are exactly `{"host", "port"}`.
   - `--host 0.0.0.0` prints the existing UNAUTHENTICATED warning byte-identically, plus a
     `deprecated` line.
2. **`prepare_auth`** (`test_launch.py`; AC-2, AC-25):
   - **(a) No settings, loopback:** `runtime is None`, `uvicorn_kwargs == {}`, `child_env == {}`,
     `warnings == ()`, and `denied_paths` contains the XDG store and state defaults and
     `<home>/.config/ao/service.env`.
   - **(b) Enabled, empty store:** raises `AuthNotReadyError` whose text contains
     `ao auth add-user`, and `--auth-dir <dir>` for a non-default store. The state-dir `audit.jsonl`
     holds one `auth.startup.refused`. With `AuditLog.record` patched to raise, the **same**
     `AuthNotReadyError` still propagates.
   - **(c) Enabled with one user, loopback:** `runtime.realm.id == "ui:" + 12 hex`;
     `uvicorn_kwargs == {"proxy_headers": False}`; no `not encrypted` text;
     `install_log_redaction` called once (spy). It is not called when disabled.
   - **(d) Enabled, bind `0.0.0.0`, no proxies:** a warning contains `not encrypted`.
   - **(e) `AO_UI_AUTH_TRUSTED_PROXIES=127.0.0.1`:** `{"proxy_headers": True, "forwarded_allow_ips": "127.0.0.1"}`
     and no transport warning.
   - **(f) Disabled, bind `0.0.0.0`:** the `deprecated` notice.
   - **(g) Disabled (default) with one user:** the `Note:` text with the account count.
   - **(h) `child_env`:**
     - CLI `enabled=True, totp=required, store_dir=D` → exactly
       `{"AO_UI_AUTH": "1", "AO_UI_AUTH_TOTP": "required", "AO_AUTH_DIR": D}`;
     - env-only `AO_UI_AUTH=1` → `{}`;
     - defaults → `{}`.
   - **(i) `denied_paths`** contains the custom store dir, its derived `<store>/state`, and
     `$AO_AUTH_STATE_DIR` when set. All entries are resolved, with no duplicates.
   - **(j)** `exit_code_for(AuthConfigError("x")) == EXIT_CONFIG == 78`.
   - **(k) v2.1, security L4:** enabled with `port=0` → `AuthConfigError` mentioning `--port 0`;
     `build_auth_runtime` is not called. Disabled with `port=0` → no error (unchanged behaviour).
3. **Config risks** (AC-4c rows 14–15, AC-45 `prepare_auth` part; security M3), with settings
   whose `config_risks` are set (built through `resolve_auth_settings` on a workspace config):
   - `DISABLED_BY_CONFIG` → `AuthConfigError` whose message names the config path,
     `--no-auth` and `AO_UI_AUTH=0`; one `auth.startup.disabled_by_config` line in the state-dir
     `audit.jsonl`; with `AuditLog.record` patched to raise, the same `AuthConfigError` still
     propagates.
   - `TOTP_DOWNGRADED_BY_CONFIG` with auth on and one user → no error; `launch.warnings` contains
     the `comes only from` warning; one `auth.startup.totp_downgraded_by_config` line.
   - Neither risk → neither event.
4. **Refuse to start** (AC-3 `ao ui` part; S14):
   - `ao ui --auth --auth-dir <empty>` → exit 78; stderr contains `ao auth add-user`;
     `fake_uvicorn.calls == []`.
   - The same through `AO_UI_AUTH=1`, and through `ui.auth.enabled: true` in `W/.ao/config.yaml`.
   - **v2.1 (L4):** `ao ui --auth --port 0` (one user) → exit 78; `fake_uvicorn.calls == []`.
5. **Enabled path:**
   - One user → exit 0. `uvicorn.run` receives an app with `runtime_of(app)` not `None`, and
     `proxy_headers=False`.
   - `AO_UI_AUTH_TRUSTED_PROXIES=127.0.0.1` → `proxy_headers=True` and
     `forwarded_allow_ips="127.0.0.1"`.
   - `--host 0.0.0.0` → stderr contains `not encrypted` and does **not** contain the
     UNAUTHENTICATED warning.
6. **Precedence through the CLI:**
   - `--no-auth` beats `AO_UI_AUTH=1`, and `AO_UI_AUTH=0` beats `ui.auth.enabled: true`. Both start
     unauthenticated.
   - Disabled by CLI/env with users present → the `Note:` line is printed.
7. **Reload** (v2.1, security L3):
   - `--reload --auth --auth-totp required --auth-dir D` (one user) → `uvicorn.run` is called with
     the factory import string, `reload=True`, `factory=True` and `proxy_headers=False`.
   - The same with `AO_UI_AUTH_TRUSTED_PROXIES=127.0.0.1` → `proxy_headers=True` and
     `forwarded_allow_ips="127.0.0.1"` on the reload call too; the reload and non-reload kwargs are
     equal for the same settings.
   - `os.environ` (monkeypatched) holds `AO_UI_AUTH=1`, `AO_UI_AUTH_TOTP=required`,
     `AO_AUTH_DIR=D` and `AO_UI_BOUND_PORT=<port>`.
   - `create_app_from_env()` then builds an app where an anonymous `GET /api/runs` → 401 and the
     realm cookie name is `ao_sid_<port>`.
   - `AO_UI_AUTH=1` without `AO_UI_BOUND_PORT` → `AuthConfigError` naming `AO_UI_BOUND_PORT`.
   - Auth off without it → the app builds, and `runtime_of(app) is None`.
8. **Invalid config through `ao ui`** (each must exit 78 unless stated otherwise):
   - `ui: {auth: {enable: true}}` → 78, naming `ui.auth.enable`;
   - `ui.auth.trusted_proxies: [...]` → 78, naming `AO_UI_AUTH_TRUSTED_PROXIES`;
   - `ui.auth.session_idle_minutes: 600` → 78 with `would weaken`;
   - unparseable YAML with users → 78;
   - unparseable YAML with no users → exit 0 plus a warning.
9. **Config flip through `ao ui`** (AC-45, S29; security test gate 4; HLD §20.3 #18), one account in
   the store:
   - `W/.ao/config.yaml` with `ui.auth.enabled: true` → starts; then the file is rewritten to
     `enabled: false` (the `git pull` case) → `ao ui -w W` exits 78 with the row-14 message;
     `fake_uvicorn.calls == []`; one `auth.startup.disabled_by_config` audit line;
   - the same with `--no-auth`, or with `AO_UI_AUTH=0` → exit 0 with the row-9 `Note:`;
   - zero accounts → exit 0, unauthenticated;
   - a corrupt `users.json` (unknown count) → exit 78;
   - `ui.auth: {enabled: true, totp: optional}` with one account → exit 0, the `comes only from`
     warning on stderr and one `auth.startup.totp_downgraded_by_config` line;
   - **env-block isolation:** a config `env: {AO_UI_AUTH: "0"}` (top-level `env:` block) together
     with `ui.auth.enabled: true` and one account → `ao ui` still runs **with** auth (the block never
     reaches the auth env layer; `apply_project_config_env` is not called on this path).
10. ruff and mypy are clean. `python -m pytest -q` (full suite) is green. Coverage of
    `auth/launch.py` is ≥ 90 %.

## Risks
- **Regressing `ao ui` for users without auth.** AC-1 and T-G7qByZ's header snapshot are the gates.
- **Double `prepare_auth` under `--reload`.** The parent and the reloader child each build a
  runtime; the parent's exists only to fail fast before uvicorn starts. This is documented, and
  sessions live in the child. Both write a startup audit event in the risk cases; accepted
  (best-effort, informational).
- **Merge order (v2.1, security M4).** This task may be built as soon as its inputs land, but it
  merges only after T-QJ1vyQ and T-KQ6ZrY and rebases its tests onto them. Budget ~0.25 d for that
  rebase within the 3 days.

## Dependencies
- **Upstream:**
  - direct (HLD §24.2 #13): T-PlEROT, T-XchniS, T-G7qByZ, T-rpKCjP, **T-Hd4wQ2** (v2.1:
    `denied_paths`, `default_denied_paths`, `scrub.py`);
  - **merge edges (v2.1, security M4):** merges to the integration branch only after **T-QJ1vyQ**
    (CSRF, Fetch Metadata, proof enforcement) and **T-KQ6ZrY** (TOTP routes), so no intermediate
    state offers `ao ui --auth` without them. The epic merges to `main` only after T-2wE08U;
  - transitive: T-kzEzwy, T-8NQP8J, T-CsT5gk.
- **Downstream:**
  - T-PDGw9p: reuses `prepare_auth` for the hub.
  - T-U2ERMo: e2e and scrub sweep.

## Pseudocode / Algorithm
- `prepare_auth`: HLD §11.20 (v2.1) plus §11.3.5, in the order given in Description 1.
- The fail-closed outcomes: HLD §11.3.4 (rows 9, 14, 15 here).
- The `ui_cmd` flow: Description 2 (row 4b order).

## Schemas / Interface Notes
- `AuthLaunch` and `prepare_auth` signatures: HLD §11.20. Field names are frozen, because T-PDGw9p
  consumes them.
- CLI flags and env: HLD §12.5. Exit code: root `errors.EXIT_CONFIG` (78).

## Handoff Boundary
- **Upstream:** settings, runtime, the HTTP layer and the denial mechanism.
- **Downstream:** a shippable `ao ui --auth` (after its merge edges), plus the shared
  `prepare_auth` for `ao service run`.

## Verification

```
python -m pytest -q tests/auth/test_launch.py tests/auth/test_ui_command_auth.py tests/ui
python -m pytest -q tests/auth/test_launch.py --cov=agent_orchestrator.auth.launch --cov-report=term-missing
python -m pytest -q
ruff check src tests && ruff format --check src tests && mypy src
```

## Artifacts
- `meta/tickets/E-Da5Tn9-dashboard-auth-totp/T-jVqH8w-ao-ui-auth-wiring/`
