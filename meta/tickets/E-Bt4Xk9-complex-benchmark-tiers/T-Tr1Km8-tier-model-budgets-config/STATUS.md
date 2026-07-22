# STATUS

- ID: `T-Tr1Km8-tier-model-budgets-config`
- Updated At: 2026-07-22
- State: **Done**
- Owner: developer agent

## This update
- By: developer agent
- Role: developer
- Date: 2026-07-22
- Comment: Implemented the tier model end-to-end per TASK.md: optional `tier` enum
  (default `small`) on the suite schema + `BenchSuite`, committed `benchmarks/tiers.json`
  (small/medium/large/xlarge caps exactly as specified), and a new `bench/tiers.py`
  loader (`load_tier_config`, `resolve_effective`, `TierConfig`). `dev-core/suite.json`
  is byte-unchanged (`git diff --stat` empty) and still validates with `tier=="small"`.

## Evidence (AC-by-AC)
1. **AC1 (dev-core unedited, defaults to small)**: `git diff --stat benchmarks/suites/dev-core` → empty. `uv run ao-bench validate --suite benchmarks/suites/dev-core/suite.json` → `suite 'dev-core': 6 task(s)` / `OK`. New test
   `tests/bench/test_spec.py::test_committed_dev_core_suite_has_no_tier_field_and_defaults_to_small`
   asserts the raw JSON has no `tier` key and `load_suite(...).tier == "small"`.
2. **AC2 (tier enum + rejection)**: `test_load_suite_tier_medium_accepted`,
   `test_load_suite_every_known_tier_accepted` (parametrized over all 4 known tiers),
   `test_load_suite_unknown_tier_rejected` (asserts `SpecValidationError` message names
   `"gigantic"` and lists all 4 known tiers — caught at the JSON-schema `enum` layer,
   with a second defense-in-depth `KNOWN_TIERS` check added in `spec.py::load_suite`
   mirroring the existing `_ID_PATTERN` precedent).
3. **AC3 (tiers.json contents)**: `tests/bench/test_tiers.py::test_load_tier_config_{small,medium,large}_defaults`
   and `test_load_tier_config_xlarge_disabled` assert the exact committed values
   (large: `cost_budget_usd_per_run=800`, `cost_budget_usd_per_subject=100`,
   `enabled=True`; xlarge: `enabled=False`, `8000`/`1000`).
4. **AC4 (malformed/missing tiers.json → structured error)**: 6 tests cover missing
   file, malformed JSON, missing `tiers` key, non-object top level, unknown tier name,
   and an invalid tier entry (bad field type) — every case raises a typed
   `SpecValidationError` naming the file, never a raw traceback.
5. **AC5 (resolve_effective precedence)**: `test_resolve_effective_cli_overrides_win`,
   `test_resolve_effective_falls_back_to_tier_defaults_when_no_cli`,
   `test_resolve_effective_falls_back_to_builtin_when_tc_none`, and
   `test_resolve_effective_cli_wins_even_over_builtin_fallback` (guards the
   falsy-`0`-vs-`None` footgun) cover all three precedence branches (CLI > tier default >
   documented `_BUILTIN_FALLBACK`).
6. **SI-1**: `import agent_orchestrator.cli` then checked `sys.modules` for any
   `agent_orchestrator.bench*` entry → none found; `grep -rn "import.*bench" src/agent_orchestrator/*.py` →
   no matches.

### Commands run (actual output summarized)
- `uv run pytest tests/bench -q` → **267 passed, 1 skipped** (pre-existing skip, unrelated).
- `uv run pytest -q -m "not real_llm"` → **1124 passed, 4 deselected** (baseline was
  1089 passed / 4 deselected — zero regressions, delta is this task's new tests).
- `uv run ruff check src/agent_orchestrator/bench tests/bench` → All checks passed.
- `uv run ruff format --check src/agent_orchestrator/bench tests/bench` → 28 files
  already formatted.
- `uv run mypy src/agent_orchestrator/bench` → Success: no issues found in 12 source
  files.
- `git diff --stat benchmarks/suites/dev-core` → empty.
- `uv run ao-bench validate --suite benchmarks/suites/dev-core/suite.json` → OK.

## Deviations / Assumptions
- `resolve_effective`'s pseudocode types its first arg as `tc: TierConfig` (non-optional),
  but AC5 explicitly requires the builtin fallback branch to be independently
  unit-testable. Implemented `tc: TierConfig | None` (default fallback to
  `_BUILTIN_FALLBACK` when `None`) so all three precedence branches are exercised
  directly through `resolve_effective` itself, rather than requiring callers to
  pre-select between `load_tier_config`'s result and `_BUILTIN_FALLBACK` before calling
  it. `TierConfig`, `_BUILTIN_FALLBACK`, `TIERS_FILE` are exported for downstream reuse.
- Used `is not None` checks for all three CLI overrides in `resolve_effective` (not the
  pseudocode's `cli_max_parallel or ...` shorthand) to avoid a falsy-`0` footgun (e.g. an
  explicit `--cost-budget-usd 0` must win over the tier default, not be treated as
  "not given"). Behavior-compatible for all realistic (truthy) CLI values.
- No other files touched beyond the File ownership list + new test files (conftest.py's
  existing `extra_top_level` kwarg was sufficient to inject `tier` into test suites
  without editing it).

## Risks / Blockers
- None outstanding. Dependencies: none (Wave A).

## Forward notes for downstream tasks
- `T-Bg2Wq4` (USD budget enforcement): consume `TierConfig.cost_budget_usd_per_run` /
  `.cost_budget_usd_per_subject` directly, or call `tiers.resolve_effective(tc,
  cli_cost_budget=...)["cost_budget_usd"]` for the per-subject cap with CLI-override
  precedence already handled.
- `T-Pl3Rx7` (parallel runner): `TierConfig.default_max_parallel` /
  `resolve_effective(...)["max_parallel"]` for the default worker count.
- `T-Cm9Tb4` (campaign): `TierConfig.cost_budget_usd_per_run` for the whole-run cap and
  `TierConfig.enabled` for the xlarge gate (`--enable-xlarge` must still be required even
  when `enabled=False`, per epic FR-9 — not wired here, per Handoff Boundary).
- `T-Wp4Nz5-workspace-provider-seam` / `T-Sg6Jf2` / `T-Sw5Hd9` / `T-Md7Vc3`: this task's
  `tier` field/`KNOWN_TIERS` in `spec.py` and the schema's `tier` property are in place;
  sequence any further `spec.py`/schema edits after this task per the epic's Wave-A
  exclusivity note.

## Next actions
- None — task complete, ready for downstream Wave-B consumption.
