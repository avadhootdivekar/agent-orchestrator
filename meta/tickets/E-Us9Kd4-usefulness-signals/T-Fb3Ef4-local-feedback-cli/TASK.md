# TASK: T-Fb3Ef4-local-feedback-cli

## Metadata
- Task ID: `T-Fb3Ef4-local-feedback-cli`
- Epic ID: `E-Us9Kd4-usefulness-signals`
- Owner: dev-epic (delegated to developer subagent)
- Created: 2026-10-02
- Last Updated: 2026-10-02
- Status: `Done`
- Estimate: `< 3 days`

## Requirements Mapping
- Requirement IDs: `FR-5,FR-6,NFR-3`

## Description
Part 3a: feedback.json store + ao rate + implicit signals module. Contract and algorithms: `docs-md/usage-signals-hld.md`.

## Acceptance Criteria
See the epic EPIC.md traceability table and the HLD section for this part; each criterion has a pytest check.

## Handoff Boundary
- Upstream / Downstream: see EPIC.md task order.

## Acceptance Criteria Result (By: developer | Role: developer | Date: 2026-10-02)
- [x] `feedback.py`: single validator/writer `add_feedback` (strict pydantic, bounds, dedupe, task/run scope rules, task id checked against state.json, entry cap -> `FeedbackCapError`), flock+thread-locked atomic fsync'd tmp+`os.replace`, corrupt/unknown-schema file refused, injectable clock. Tests: `tests/test_feedback.py` (incl. crash-simulation, threads, subprocesses).
- [x] `effective_ratings` / `effective_for_task(..., explicit_only=False)` / `summarize` (`FeedbackSummary`).
- [x] `implicit_signals.py`: `landed`, `followup_commits` (+`followup_confidence`), `run_status`, breaker counts, `reverted_commits=None` placeholder; never raises without git. Tests: `tests/test_implicit_signals.py`. Re-run-of-similar-prompt intentionally not implemented (Non-MVP).
- [x] CLI `ao rate <run> good|ok|bad [--task] [--reason]... [--note]` and `--show [--json]`; exit 1 on errors. Tests: `tests/test_e2e_cli_rate.py`.
- Limitation: followup/landed need isolation heads (`RunState.integration.heads/base_heads`); non-isolated runs report unknown with a reason.
