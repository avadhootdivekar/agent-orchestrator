# STATUS

- ID: `T-PDGw9p-service-cli-supervisor-auth`
- Updated At: `2026-10-05`
- State: `Done`
- Owner: `developer` (lane A; v2.1, was lane Q)
- Scope: `MVP` · Sprint: `S3` · Estimate: `2.5 d`

## This update
- By: developer · Role: developer · Date: 2026-10-05 · Comment: Implemented end to end (branch
  `ad/dashboard-auth-totp`). `service/cli.py`: `HubLoginRequired`; `_probe_hub_status(port)` keeps
  its one-argument signature and raises it on HTTP 401 (`HTTPError` branch precedes the broad
  except; other codes -> `None`); `list`/`status` print the HLD §15 row 1 fallbacks (exit 0);
  `run` gains `--auth/--no-auth`, `--auth-totp`, `--auth-dir`, calls `prepare_auth` (realm `hub`,
  `workspace_root=None`) before `Supervisor(...)` is built, maps `AuthConfigError` to
  `ERROR: <msg>` + exit `EXIT_CONFIG`, prints warnings, and wires `child_env`, `build_hub_app(auth=)`
  and `uvicorn_kwargs`; `install` prints the `AO_UI_AUTH=1` / `ao auth add-user` hint; `--hub-host`
  help updated. `supervisor.py`: `Supervisor(child_env=)` -> `default_child_spawner(extra_env=)`
  (no `env` kwarg when empty/None); a child exiting `EXIT_CONFIG` is terminal (state `stopped`, no
  backoff, no port reassignment). `systemd.py`: `RestartPreventExitStatus={exit_config}` after
  `Restart=on-failure`, rendered from `EXIT_CONFIG`. No ambiguity found; nothing BLOCKED.
- By: architect · Role: agent · Date: 2026-10-05 · Comment: v2.1 gates folded (HLD §28.9).
  Estimate and sprint unchanged (2.5 d, S3); **lane Q → A** (HLD §24.3 rebalance, design-review
  minor 3: lane Q's S3 now holds only T-U2ERMo). New AC 1b: `ao service run --auth` with port 0 →
  exit 78 before anything is spawned (security L4, implemented in `prepare_auth`). Otherwise the
  task consumes the v2.1 `prepare_auth` unchanged; the workspace-config risks (security M3) never
  apply to the hub.
- By: architect · Role: agent · Date: 2026-10-05 · Comment: Task created in the v2 re-baseline
  (design and tickets only; **no code written**). It takes over the service-side half of v1
  `T-KOv2qD-hub-service-auth`, because the v1 task exceeded the 3-day cap (developer D-4).
  - **`ao service run`** calls the shared `prepare_auth` **before** constructing the `Supervisor`
    (developer D-9 / reviewer R-3).
  - **Probe:** a 401 from the hub becomes `HubLoginRequired`, and `list`/`status` fall back to the
    persisted state.
  - **Supervisor:** `child_env` is CLI-sourced only, and a child exiting `EXIT_CONFIG` is terminal.
    `EXIT_CONFIG` lives in root `errors.py` (reviewer R-10).
  - **systemd:** the unit gains `RestartPreventExitStatus` rendered from `EXIT_CONFIG`.

## Evidence
- `.venv/bin/python -m pytest -q tests/auth tests/service tests/ui tests/test_cli*.py` ->
  3074 passed, 2 skipped (new: `tests/auth/test_service_run_auth.py` 8,
  `tests/service/test_cli_probe_auth.py` 10, `tests/service/test_supervisor_auth.py` 8,
  `tests/service/test_systemd.py` +2). `tests/service/test_cli_e2e.py` and
  `tests/service/test_supervisor.py` unmodified and green.
- `.venv/bin/ruff check src tests` -> All checks passed. `ruff format --check src tests` -> only
  the pre-existing generated `_build_info.py` is flagged (not in scope). `.venv/bin/mypy src` ->
  only the 4 pre-existing `_version.py` errors.
- AC-5(c) gate: child `exit 78` + 5 ticks -> spawner called once, `restart_count == 0`,
  `fast_fail_count == 0`, port unchanged, state `stopped`, `last_error` contains
  `configuration error`; `supervisor.json` lists no children (live children only).
- Ticket AC-5 snapshot note: `supervisor.json` carries pid/port only (no `last_error`), so
  "snapshot shows the same" is asserted as the terminal child being absent from `children`.

## Risks / Blockers
- None (done). Original note: Implement against `launch.child_env` / `launch.uvicorn_kwargs` (HLD §11.20; §16 row 6 uses
  these names). On the critical path (HLD §24.3).

## Next actions
1. None. (was: implement once T-jVqH8w (`prepare_auth`) and T-KOv2qD (`build_hub_app(auth=)`) land.
   Run the verification and record the results here.
