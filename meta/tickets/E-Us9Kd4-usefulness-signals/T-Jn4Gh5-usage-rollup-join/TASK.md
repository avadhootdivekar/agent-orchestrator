# TASK: T-Jn4Gh5-usage-rollup-join

## Metadata
- Task ID: `T-Jn4Gh5-usage-rollup-join`
- Epic ID: `E-Us9Kd4-usefulness-signals`
- Owner: dev-epic (delegated to developer subagent)
- Created: 2026-10-02
- Last Updated: 2026-10-02
- Status: `Done`
- Estimate: `< 3 days`

## Requirements Mapping
- Requirement IDs: `FR-7,FR-8`

## Description
Part 3b: join feedback + survival + reviewer-error into report-usage. Contract and algorithms: `docs-md/usage-signals-hld.md`.

## Acceptance Criteria
- [x] AC1 `GroupUsage`/`UsageReport` additive feedback, reviewer-candidate, survival fields + coverage (tests/test_usage_join.py)
- [x] AC2 reviewer-disagreement candidates use explicit task-scope ratings only; run-level rating creates none
- [x] AC3 survival joins only isolation/serial, non-low-confidence rows; degrades with a reason when git/ref unavailable
- [x] AC4 flags only above `MIN_SAMPLE_FOR_FLAG` (user-bad, reviewer disagrees, low survival)
- [x] AC5 shared `usage.build_usage_report` / `usage_report_payload` (CLI + dashboard); corrupt run/feedback skipped + counted; run-id cap
- [x] AC6 `ao report-usage --with-survival/--ref`, new columns, coverage line, FLAG lines, `run_signals` in --json (tests/test_e2e_cli_usage_join.py)
- [x] AC7 `reverted_commits` filled via `implicit_signals.apply_survival`

## Handoff Boundary
- Upstream / Downstream: see EPIC.md task order.
