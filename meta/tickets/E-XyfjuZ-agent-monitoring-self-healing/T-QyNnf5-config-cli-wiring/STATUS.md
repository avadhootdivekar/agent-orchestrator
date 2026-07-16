# STATUS

- ID: `T-QyNnf5-config-cli-wiring`
- Updated At: 2026-07-15
- State: Done
- Owner: dev-epic agent

## This update
- By: dev-epic agent
- Role: developer
- Date: 2026-07-15
- Comment: Implemented `MonitoringConfig` (`project_config.py`) + `ProjectConfig.monitoring`;
  `_resolve_monitoring_settings`/`_build_monitor` in `cli.py`; wired into both `run` and
  `resume` with a new tri-state `--self-heal/--no-self-heal` flag (revised D7 per early-gate
  reviewer feedback — full CLI>env>config>default precedence in both directions, not the
  originally-planned monotonic-enable-only). `ao init` scaffold template updated with a
  commented `monitoring:` section. Discovered and worked around a real constraint:
  `DispatchExecutor` always builds an unconfigurable `FakeExecutor()`, so no "fake" agent
  can be made to fail deterministically through the CLI (confirmed this is a PRE-EXISTING,
  already-known constraint — `tests/test_cli.py::TestRunCommand::test_failed_run_exits_1`'s
  own comment says so). Worked around it for the self-heal CLI e2e tests using the REAL
  `claude_cli` executor with a deterministic `sh -c "...; exit 1"` command_template (no
  `claude` binary/API key needed) — proves the full CLI-to-engine wiring on a genuine
  dispatch failure; the "heals AND succeeds" happy path is proven at the engine-API level
  (`test_monitoring_self_heal.py`), which is an explicit, documented scope boundary.
  New tests: 7 CliRunner e2e tests (`tests/test_e2e_monitoring_cli.py`) + 9 unit tests for
  `_resolve_monitoring_settings`/`_build_monitor` (`tests/test_cli.py`).

## Evidence
- `uv run pytest tests/test_e2e_monitoring_cli.py tests/test_cli.py -q` → 51 passed (7 + 9
  new + 35 pre-existing test_cli.py, zero regressions).
- `uv run pytest -q` (full suite) → 778 passed, 3 skipped (769 prior + 9 new).
- `uv run ruff check .` / `ruff format --check .` clean; `uv run mypy src` → 4 pre-existing
  `_version.py` errors only.
- Manual smoke test: `uv run ao run --help` / `uv run ao resume --help` confirm the tri-state
  `--self-heal/--no-self-heal` flag registers correctly.

## Risks / Blockers
- None. The DispatchExecutor/FakeExecutor constraint is documented, not silently absorbed.

## Next actions
1. Done. `T-Wx8vUq` performs a systematic acceptance-criteria audit across all 6 prior
   tasks (mostly already covered incrementally) plus a final coverage/lint/type check.
