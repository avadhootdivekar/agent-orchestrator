# STATUS

- ID: `E-5I8azA-nfr2-gate-stale-exception`
- Updated At: 2026-09-21
- State: Draft (backlog — not started)
- Owner: unassigned

## This update
Epic filed after Epic C's (`E-DOiDqE-workflow-authoring-skill`) pre-change full-suite baseline
run surfaced one failing test unrelated to any of Epic C's own (not-yet-made) changes:
`TestPreEpicTestsUnedited::test_every_pre_epic_test_file_is_byte_identical_to_its_pre_epic_content`.
Root-caused to a pure `ruff format` cosmetic reformat of `tests/test_e2e_builtin_routed_runner.py`
sometime after the gate's reference merge-base, never registered as an exception. Not implemented
here (out of Epic C's scope).

By: dev-epic · Role: developer · Date: 2026-09-21 · Comment: Confirmed via
`git diff b849b7c HEAD -- tests/test_e2e_builtin_routed_runner.py` — every hunk is whitespace/
line-wrap only, zero semantic change. Baseline pytest run recorded 1 failed (this one), 3968
passed, 1 skipped, 7 deselected — pre-existing, not introduced by Epic C.

## Evidence
- Baseline log: `.tmp/pytest_baseline.log` (this session's run, `ad/cost-perf-hooks-skills`,
  2026-09-21) — `1 failed, 3968 passed, 1 skipped, 7 deselected in 172.54s`.
- `git diff b849b7c HEAD -- tests/test_e2e_builtin_routed_runner.py` — reformat-only diff.
- `tests/test_nfr2_regression_gate.py` lines ~136+ — `_EPIC_MODIFIED_PRE_EPIC_TESTS` registry
  where the exception belongs.

## Risks / Blockers
- None blocking. Low priority, mechanical fix.

## Next actions
1. Prioritize against other backlog epics.
2. When picked up: `T-zqhY3v-register-formatting-exception` adds the exception entry (or
   reverts the formatting) and re-runs the gate to confirm green.
