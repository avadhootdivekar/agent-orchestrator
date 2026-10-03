# TASK: T-Vd1Ab2-generic-verdict-capture

## Metadata
- Task ID: `T-Vd1Ab2-generic-verdict-capture`
- Epic ID: `E-Us9Kd4-usefulness-signals`
- Owner: dev-epic (delegated to developer subagent)
- Created: 2026-10-02
- Last Updated: 2026-10-02
- Status: `Done`
- Estimate: `< 3 days`

## Requirements Mapping
- Requirement IDs: `FR-1,FR-2,NFR-1`

## Description
Part 1: generic verdict capture incl. overseer checkpoint/final-verify. Contract and algorithms: `docs-md/usage-signals-hld.md`.

## Acceptance Criteria
See the epic EPIC.md traceability table and the HLD section for this part. All met (tests in `tests/test_usage_verdicts.py`):
- [x] `TaskSpec.verdict_path` / `TaskRunState.verdict_path` added; old state JSON loads; `review_verdict_path` still populated.
- [x] `usage.verdict_path_for` precedence: explicit > declared `verdict.json`/`*-verdict.json` > sibling table (review.md, verify.md); recorded at dispatch.
- [x] `parse_verdict` kinds review/checkpoint/final_verify; never raises on missing/malformed/oversized; unknown-shaped `*verdict.json` not counted.
- [x] `UsageReport.outcomes` (`RunOutcome`); review kind still attributed to producers; `verdicts found x/y` covers all kinds.
- [x] `ao report-usage` "Outcome vs charter" section + `outcomes` in `--json`.
- [x] Overseer `40-final-verify.md` + contract describe optional, undeclared `verify-verdict.json`; R10 outputs stay exactly `[verify.md]` (pinned by test).
- [x] `verdict_path` validated workspace-relative in `spec.validate_isolation` (no abs / `..`).

## Handoff Boundary
- Upstream / Downstream: see EPIC.md task order.
