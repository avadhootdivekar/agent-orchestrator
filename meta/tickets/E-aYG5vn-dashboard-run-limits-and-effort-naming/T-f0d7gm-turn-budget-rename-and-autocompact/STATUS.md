# STATUS

- ID: `T-f0d7gm-turn-budget-rename-and-autocompact`
- Updated At: `2026-11-09`
- State: `Done`
- Owner: `unassigned`

## This update
- By: claude-sonnet-5-5 · Role: developer · Date: 2026-11-09 · Comment: Implemented on `ad/enhancements-20oct`.
  `turn_budget` (spec/--turn-budget/AO_TURN_BUDGET/config) with deprecated `effort` alias (warning; conflict
  error); `ao migrate effort-keys [--apply]`; provider-neutral `autocompact` (task > agent > run; claude_cli
  `--autocompact`, extra_args wins; `Executor.supported_settings`; `ao validate` + run-log warning); registry
  `providers` metadata; effective_settings / per-task limits now record turn_budget, max_turns, autocompact.

## Evidence
- `tests/test_turn_budget_autocompact.py` (43 tests: alias matrix, migrate dry-run/apply/idempotent/conflict,
  autocompact precedence + argv + extra_args, validate/run warnings, registry providers).
- Gates: see the unit report `workflows/overseer-runner/.../w02-02-turn-budget-autocompact.md`.
- Cache key doc keeps the legacy name `effort` for `turn_budget` (no KEY_SCHEMA_VERSION bump).

## Risks / Blockers
- Dashboard form still sends legacy `effort` (kept in the allow-list, labelled deprecated) until T-pMpo4O.
- No pre-existing `ao validate` warning for a *missing* `--autocompact` existed in code (only a README grep);
  nothing to "recognise". Not added.

## Next actions
1. T-pMpo4O (form fields) / T-xNFBww (display) consume the new registry entries.
