# STATUS

- ID: `T-zP28k9-run-options-registry-and-settings-snapshot`
- Updated At: `2026-11-09`
- State: `Done`
- Owner: `developer`

## This update
- By: claude-opus-5-5 · Role: architect · Date: 2026-11-09 · Comment: Task created from epic breakdown (owner-approved design).

- By: developer (ao overseer unit w01-02) · Role: developer · Date: 2026-11-09 · Comment: Implemented `run_options.py` registry; cli resolvers (`_resolve_run_settings`, `_resolve_thinking_effort`, `_resolve_monitoring_settings`, `_resolve_record_git_heads`) read it; `ui/processes.py` allow-lists derived; `GET /api/schema/run-options`; `RunState.effective_settings` + `effective_settings_history`, `TaskRunState.effective_limits`.

## Evidence
- `tests/test_run_options.py` (legacy-chain oracle over CLI/env/config combos, allow-list, argv-injection, schema snapshot `tests/fixtures/run_options_schema.json`, start/resume snapshot e2e via CliRunner, old-state load) + endpoint test in `tests/ui/test_api_integration.py`.
- Full `pytest -q`: 10669 passed, 32 skipped, 2 failed (`tests/bench/test_dev_{core,medium}_suite.py::test_fake_subject_full_suite_run...` — fail identically with changes stashed; pre-existing). ruff check/format clean on src+tests. mypy clean on touched files (pre-existing errors only in `_version.py`, `overseer_tool.py`).
- Known gaps: `turn_budget`, `autocompact`, `task_cost_cap_usd` limits are recorded as None until T-f0d7gm / later tasks add engine controls.

## Risks / Blockers
- None

## Next actions
1. Downstream unblocked: T-f0d7gm, T-pMpo4O, T-xNFBww; E-Sn7tL2 T-Sn2Set.
