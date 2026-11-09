# STATUS

- ID: `T-Sn2Set-sentinel-settings-plumbing`
- Updated At: `2026-11-09`
- State: `Done`
- Owner: `unassigned`

## This update
- By: claude-opus-5-5 · Role: architect · Date: 2026-11-09 · Comment: Task created from epic breakdown (owner-approved design).

- By: claude-sonnet-5-5 · Role: developer · Date: 2026-11-09 · Comment: **Implemented.** `models.SentinelSpec` (+ `WorkflowSpec.sentinel`, `ProjectConfig.sentinel` + commented sample, `specs/workflow.schema.json`), registry options generated from the model (`run_options.sentinel_options`, env `AO_SENTINEL_<DOTTED_UPPER>`, new `float` type, `workflow` source), `sentinel_settings.py` (layered merge, `--sentinel-set` parsing, validation), CLI flags on run/resume/`new --run` via one resolver, template params in both builtins via `raw.sentinel_member` fragment, `Orchestrator(sentinel=…)` stores only. Decisions: env for the master switch is `AO_SENTINEL_ENABLED`; `recovery.force` is the floor escape; bad CLI/env value exits 2; `ao new --run` forwards only the sentinel flags (not all run options).

## Evidence
- `tests/test_sentinel_settings.py` (28 tests: precedence, sources in `state.effective_settings`, bad `--sentinel-set` exit 2, validate, template render/byte-identity, `new --run` pass-through); `tests/fixtures/run_options_schema.json` regenerated. Full gate results: see the W4 unit report.

## Risks / Blockers
- None

## Next actions
1. Pick up after: E-aYG5vn T-zP28k9 (run-options registry) and T-Sn1Adr
