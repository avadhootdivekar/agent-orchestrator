# STATUS

- ID: `T-mQs7CQ-dashboard-epic-e2e-and-docs`
- Updated At: `2026-11-09`
- State: `Done`
- Owner: `unassigned`

## This update
- By: claude-opus-5-5 · Role: architect · Date: 2026-11-09 · Comment: Task created from epic breakdown (owner-approved design).

- By: claude-sonnet-5-5 · Role: developer · Date: 2026-11-09 · Comment: Done. Added API-level (real `ProcessSupervisor` + real `ao run`/`ao resume` children, fake executor) and CliRunner-level e2e; docs + migration note; epic §5 ticked. T-9ymKja deliberately not executed.

## Evidence
- `tests/ui/test_e2e_run_options_dashboard.py`: 9 passed (New Run, Template Launch, Resume history, allow-list drop, invalid value, CLI run/resume, deprecation+conflict, `ao migrate effort-keys`, CLI/schema parity).
- Full `pytest -q`: 10804 passed, 32 skipped, 2 failed (`tests/bench/test_dev_{core,medium}_suite.py::test_fake_subject_full_suite_run_...`, bench grading solved=0; fails identically on a clean HEAD worktree — pre-existing, unrelated). `ruff` on touched files clean (3 E501 in another unit's untracked `sentinel_driver.py`). UI untouched (no npm gates).
- Docs: `README.md`, `docs-md/run-options-and-limits-guide.md`, template READMEs.

## Risks / Blockers
- None

## Next actions
1. Epic close; T-SnXAcc may reuse the `ws`/`client` fixtures in the new test file.
