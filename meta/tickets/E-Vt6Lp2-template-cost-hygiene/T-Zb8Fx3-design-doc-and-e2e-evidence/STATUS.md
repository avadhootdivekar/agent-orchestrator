# STATUS

- ID: `T-Zb8Fx3-design-doc-and-e2e-evidence`
- Updated At: 2026-09-21
- State: Done
- Owner: dev-epic

## This update
- By: dev-epic · Role: developer · Date: 2026-09-21
- Comment: `docs-md/template-cost-hygiene-hld.md` written (commit `244ca49`), covering the D1
  mechanism choice + alternatives considered, the `--autocompact=200000` default justified
  quantitatively from the real growth-curve numbers (with dollar magnitude confirmed via the
  `claude-api` skill's live pricing table: ~$0.20/MTok cache-read on Sonnet 5, so 42.5M tokens
  ≈ $8.50 on the one real trajectory), the early-gate architect+reviewer findings with recorded
  reasoning for where I diverged, and real late-gate `ao new` e2e evidence. While running the
  FULL suite for this task, found and fixed a pre-existing, undeclared NFR-2 gate gap unrelated
  to this epic's own changes (§7 of the design doc) — full suite is now green.

## Evidence
- `.venv/bin/python -m pytest -q -m "not real_llm and not swebench"` (full repo suite, first
  run): **1 failed, 3976 passed, 1 skipped, 7 deselected** — the 1 failure investigated and
  confirmed pre-existing (predates this epic, inherited from `b0cb467`), fixed via a declared
  `_EPIC_MODIFIED_PRE_EPIC_TESTS` exception entry.
- Same command, second run (after the fix): **3977 passed, 1 skipped, 7 deselected, 0 failed.**
- `tests/test_nfr2_regression_gate.py` alone: 7 passed (was 1 failed / 6 passed before the fix).
- Real e2e: `tests/test_e2e_builtin_routed_runner.py::TestRoutedRunnerE2E::test_agents_recommended_json_scaffolds_and_keep_existing_holds`
  — two real `ao new routed-runner` invocations via `CliRunner` into a `tmp_path` scratch
  workspace, hand-edit between them, `keep_existing` proven end-to-end. Passed.
- `ruff check`/`ruff format --check` on all 3 Python files this epic touched
  (`tests/test_builtin_routed_runner_assets.py`, `tests/test_e2e_builtin_routed_runner.py`,
  `tests/test_nfr2_regression_gate.py`): clean. `mypy` on the same 3 files: 33 errors, confirmed
  via a clean pre/post content comparison (`git show <pre-epic commit>:<path>` swapped in,
  mypy re-run, reverted) to be the IDENTICAL pre-existing set (19 in
  `test_nfr2_regression_gate.py` alone, unchanged; 14 in the other two, unchanged, confirmed
  earlier in `T-Hn4Rq8`'s evidence) — 0 new errors from this epic's changes.
- `git log --oneline 7114e37..HEAD` shows exactly 5 commits, all with the required
  Co-Authored-By/Claude-Session trailers; `git diff --stat 7114e37..HEAD -- '*.py'` confirms
  this epic's entire Python footprint is exactly the 3 test files above — no engine/core/spec
  code touched, per the epic's change-scope boundary.

## Risks / Blockers
- None outstanding.

## Next actions
1. None — epic complete pending final human review of the `--autocompact` default choice
   (disclosed as an informed estimate, not a measured optimum) and the NFR-2 gate exception
   entry (a pre-existing gap this epic found and closed, not one it introduced).
