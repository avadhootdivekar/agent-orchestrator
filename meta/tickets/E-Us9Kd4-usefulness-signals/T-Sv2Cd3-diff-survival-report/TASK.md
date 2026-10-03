# TASK: T-Sv2Cd3-diff-survival-report

## Metadata
- Task ID: `T-Sv2Cd3-diff-survival-report`
- Epic ID: `E-Us9Kd4-usefulness-signals`
- Owner: dev-epic (delegated to developer subagent)
- Created: 2026-10-02
- Last Updated: 2026-10-02
- Status: `Done`
- Estimate: `< 3 days`

## Requirements Mapping
- Requirement IDs: `FR-3,FR-4,NFR-2`

## Description
Part 2: git-only diff-survival attribution + ao report-survival (+ git head recording). Contract and algorithms: `docs-md/usage-signals-hld.md`.

## Acceptance Criteria
See the epic EPIC.md traceability table and the HLD section for this part; each criterion has a pytest check.
- [x] Recording is backward compatible: `RunState.git_repos/git_start_heads` (once, new runs only), `TaskRunState.start_heads/end_heads`, `TaskIntegrationState.landed_ranges` — `tests/test_survival.py::TestRecording`, `tests/test_e2e_cli_survival.py::TestEngineRecording`.
- [x] Attribution: isolation (landed ranges > squash sha, retries summed), serial (non-overlapping), ambiguous (overlap), time-window (low confidence, run level, never flagged) — `TestAttribution`.
- [x] Metric: full / overwritten / partial / revert / rename / delete / binary / caps / trivial-line filter — `TestMetric`, `TestParseAddedLines`.
- [x] Never raises without git / outside a repo; `unavailable` reason — `test_no_git_workspace_*`, `test_missing_workspace_dir_never_raises`.
- [x] `ao report-survival` table + `--json`, `--ref` validated (option injection rejected, must resolve) — `TestReportSurvivalCli`.

## Handoff Boundary
- Upstream / Downstream: see EPIC.md task order.
