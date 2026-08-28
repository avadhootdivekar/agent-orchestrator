# STATUS

- ID: `T-Hb3x7q-hub-systemd-cli`
- Updated At: 2026-08-28
- State: Done
- Owner: developer agent

## This update
- Implemented the hub HTTP server, systemd unit generation, and the `ao service` CLI
  sub-app against `T-Gr8s2a`'s actual merged `service/registry.py`/`service/ports.py`/
  `service/paths.py` and `T-Sv9d4k`'s actual merged `service/supervisor.py`/
  `service/boot_resume.py` (read, not guessed from the HLD): `service/hub.py`
  (`build_hub_app`), `service/systemd.py` (`resolve_ao_executable`, `render_unit`,
  `install_unit`, `systemctl_available`, `start_via_systemctl`/`stop_via_systemctl`),
  `service/cli.py` (`add`/`remove`/`list`/`status`/`install`/`start`/`stop`/`run` +
  `build_status_provider`).
- All three early-gate corrections (AC14/16/17) implemented as core requirements, each
  independently tested, not folded into a mega-test:
  - **AC14 (hub `SecurityMiddleware` actually mounted)**: `build_hub_app` calls
    `app.add_middleware(SecurityMiddleware, allowed_hosts=resolve_allowed_hosts())` —
    identical pattern to `ui/app.py::create_app`. Tested by a real `TestClient` request
    carrying a disallowed `Host` header and asserting HTTP 421 comes back
    (`TestSecurityMiddlewareMounted::test_disallowed_host_header_is_rejected_with_421`),
    not by inspecting import statements. Also asserted the JSON status endpoint gets the
    same 421 treatment, and that security response headers (`X-Content-Type-Options`
    etc.) land on a normal response.
  - **AC16 (systemd hardening lines)**: `render_unit` gained `RestartSec=5`,
    `StartLimitIntervalSec=120`, `StartLimitBurst=5`, `TimeoutStopSec=30`,
    `EnvironmentFile=-%h/.config/ao/service.env`, and an explicit `--hub-port <N>` on
    `ExecStart` (never left to `service run`'s own default — `install_unit` always
    renders the value it was actually called with, default `DEFAULT_HUB_PORT=8770`). Each
    of the six is its own text-assertion test in `test_systemd.py::TestRenderUnit`,
    alongside the pre-existing `KillMode=process` check (ADR-0012 D2 — the single most
    safety-critical line; never omitted or defaulted).
  - **AC17 (`resolve_ao_executable` rejects a relative `sys.argv[0]`)**:
    `Path(sys.argv[0]).is_absolute()` is checked alongside the basename-`ao` check, so a
    relative `argv[0]` (e.g. `./ao`) falls through to `shutil.which("ao")` instead of
    being accepted just because its basename matches — systemd requires an absolute
    `ExecStart`. Tested as its own case
    (`test_relative_argv0_named_ao_is_rejected_even_though_basename_matches`), independent
    of the two originally-specified branches (absolute-argv0-hit, shutil.which-fallback,
    neither-available-raises).
- Remaining early-gate items also implemented: AC15 (`build_status_provider` in
  `service/cli.py` wraps `Supervisor.status_snapshot()` with a per-workspace `run_summary`
  via `RunRepository.aggregate()`, behind a clock-injectable `HUB_STATUS_CACHE_TTL_SECONDS`
  =5s cache — `hub.py` itself never imports `RunRepository`, keeping it a thin adapter per
  the ADR-0010 `ui/app.py`-vs-`ui/service.py` split; tested independently in
  `TestBuildStatusProvider` for enrichment, TTL-hit, TTL-expiry, and a bad-workspace not
  breaking the whole payload); AC18 (`install`'s non-`--print` guidance mentions
  `~/.config/ao/service.env` and the stale-global-`ao`-snapshot risk with
  `install.sh --force` as the fix, asserted by output-contains checks in
  `test_cli_e2e.py::TestInstall::test_install_writes_the_unit_and_prints_next_step_guidance`);
  AC19 (`list`/`status` read the hub port to probe from
  `<state_dir>/supervisor.json.hub_port` via `_persisted_hub_port`, never from the
  invoking CLI's own `--hub-port` flag/default, falling back to the module constant
  `DEFAULT_HUB_PORT` purely as a last-resort guess and saying so explicitly in the
  "not confirmed running" message when no snapshot exists).
- `[ui]`-extra optionality (AC1, Risk #1): `hub.py` imports `fastapi`/`fastapi.responses`
  only inside `build_hub_app`'s body (a `TYPE_CHECKING`-guarded import supplies the
  `-> FastAPI` return annotation for mypy without an eager runtime import — same pattern
  `cli.py` already uses for `ArtifactStore`/`Executor`/etc.); `service/cli.py` imports
  `uvicorn` only inside `run`'s body. Verified not just by "tests passed" but by an
  explicit runtime check: `test_hub_module_imports_cleanly_even_when_fastapi_is_unavailable`
  monkeypatches `builtins.__import__` to raise on any `fastapi` import, reimports
  `service.hub` fresh, and confirms the module import succeeds while only *calling*
  `build_hub_app` raises. `list`/`status`'s hub-reachability probe (`_probe_hub_status`)
  is deliberately stdlib-only (`urllib.request`, not `httpx`) for the same reason.
- `ao service --help` lists exactly the seven AC8 subcommands (`add remove list status
  install start stop run`) — confirmed both by manual invocation and by
  `test_cli_e2e.py::TestHelp::test_lists_all_seven_subcommands`. No test invokes
  `ao service run` itself (AC12) — its parts (`Supervisor.start/tick/shutdown`, already
  tested by `T-Sv9d4k`; `hub.build_hub_app`, tested here) are each independently covered.

## Evidence
- `git diff -- src/agent_orchestrator/cli.py`: exactly two added lines (`from
  .service.cli import app as service_app` + `app.add_typer(service_app,
  name="service")`), no reformatting, no other edits — the hard boundary another epic
  relies on staying minimal held.
- `uv run pytest -q tests/service/`: **116 passed, 0 failed** (66 pre-existing from
  `T-Gr8s2a`/`T-Sv9d4k` + **50 new**: 11 `test_hub.py` + 20 `test_systemd.py` + 19
  `test_cli_e2e.py`).
- `uv run pytest -q` (full suite): **1933 passed, 7 skipped, 0 failed** — baseline after
  `T-Sv9d4k` was 1883 passed / 7 skipped / 0 failed. Delta is exactly +50 (matches the new
  `tests/service/` count precisely, confirming no other suite regressed or gained tests
  concurrently at the moment of this run).
- `uv run ruff check src/agent_orchestrator/service/ src/agent_orchestrator/cli.py`: clean
  (one auto-fix round for `UP037` — quoted forward-ref annotations were redundant under
  this module's `from __future__ import annotations`; re-verified clean after).
- `uv run ruff format --check src/agent_orchestrator/service/
  src/agent_orchestrator/cli.py`: clean.
- `uv run mypy src`: **4 errors**, all pre-existing in `src/agent_orchestrator/_version.py`
  (identical baseline `T-Sv9d4k` reported — zero new errors introduced by this task's
  files).

## Risks / Blockers
- None blocking. Carried risk (unchanged from epic-level `STATUS.md`): `KillMode=process`
  has no automated test (locked "no systemd in tests" decision) — a manual verification
  against a real `systemctl --user` unit is attempted at `T-Rv5m1t`'s late gate if a
  systemd user session is available in that execution environment, else the gap is
  recorded, not silently closed (see ADR-0012 "Early-gate corrections", final paragraph).
- `bind(0)` TOCTOU race for P3 port picks (carried limitation from `T-Gr8s2a`, unaffected
  by this task) still applies to the hub's own defaults — not newly introduced here.

## Next actions
1. Hand off to `T-Rv5m1t-test-review-e2e` for full-suite baseline/after counts across the
   whole epic, the late-gate e2e exercise (including, if feasible, the manual
   `KillMode=process` verification), a `reviewer` pass, and `meta/ROADMAP.md`/HLD sync.
