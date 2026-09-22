# STATUS

- ID: `T-Hn4Rq8-agents-recommended-asset`
- Updated At: 2026-09-21
- State: Done
- Owner: dev-epic

## This update
- By: dev-epic · Role: developer · Date: 2026-09-21
- Comment: Implemented after the early-gate architect+reviewer pass (see EPIC.md). Shipped
  `src/agent_orchestrator/templates/builtin/routed-runner/agents.recommended.json.tmpl`,
  `template.yaml`'s new `assets:` entry, and the README's "Recommended agent command_template
  hygiene" section (commit `f08816f`).

## Evidence
- `.venv/bin/python -m pytest -q tests/test_builtin_routed_runner_assets.py`: 39 passed (was 33
  before this task's additions — 6 new tests: seed JSON shape, 9-role coverage,
  `extra_args`-not-`command_template`, threshold-matches-design-doc, per-entry
  `agents.schema.json` field validity, README section presence).
- `.venv/bin/python -m pytest -q tests/test_templates.py tests/test_e2e_builtin_routed_runner.py tests/test_builtin_routed_runner_assets.py tests/test_e2e_cli_templates.py -m "not real_llm and not swebench"`:
  **122 passed, 0 failed** (baseline before this epic: 115 passed) — no regressions.
  `test_e2e_builtin_routed_runner.py::TestRoutedRunnerE2E::test_agents_recommended_json_scaffolds_and_keep_existing_holds`
  drives two real `ao new` invocations via `CliRunner` into a `tmp_path` scratch workspace,
  hand-edits the seeded file between them, and asserts byte-identical survival + a `skipped`
  (not `created`) CLI report on the second call — real `keep_existing` end-to-end evidence, not
  just a templates-module unit assertion.
- `ruff check`/`ruff format --check` on both touched test files: clean. `mypy` on both: 14
  errors, identical set (confirmed via `git stash` diff) to the pre-existing baseline — 0 new.

## Risks / Blockers
- None outstanding. One finding surfaced while running the FULL suite (not just the 4 target
  suites): `tests/test_nfr2_regression_gate.py`'s `TestPreEpicTestsUnedited` gate failed on
  `tests/test_e2e_builtin_routed_runner.py` — investigated and confirmed the bulk of that file's
  divergence from the `ad/multi-workspace-service` base is **pre-existing ruff-format
  reformatting inherited from `b0cb467`** (the merge this whole A/B/C/D epic thread branched
  from, predating Epic D entirely) that was never declared as a gate exception at the time; this
  epic's own addition on top is purely additive. Fixed by adding a declared, justified entry to
  `_EPIC_MODIFIED_PRE_EPIC_TESTS` (see `T-Zb8Fx3` evidence) — not a regression this epic caused,
  but a pre-existing bookkeeping gap this epic's full-suite run caught and closed.

## Next actions
1. None — task complete; `T-Zb8Fx3-design-doc-and-e2e-evidence` records the epic-level design
   doc that documents this mechanism's full justification.
