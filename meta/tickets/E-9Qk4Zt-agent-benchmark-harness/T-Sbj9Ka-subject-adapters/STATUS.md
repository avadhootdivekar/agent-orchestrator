# STATUS

- ID: `T-Sbj9Ka-subject-adapters`
- Updated At: 2026-07-22
- State: Draft
- Owner: developer agent

## This update
- By: architect · Role: architect · Date: 2026-07-22 · Comment: Task specified from design §4.2. Tentpole of Sprint 1 (subprocess subjects + reuse of core cost/usage helpers).

## Evidence
- Design: `docs-md/benchmarking-framework-hld.md` §4.2, §6; reuse targets in `executors/claude_cli.py` + `models.py`.

## Risks / Blockers
- R1 permission-mode, R2 cost attribution (A4) — mitigations in TASK.md.

## Next actions
1. `Subject` ABC + registry + workspace materialization (path-guarded).
2. `FakeSubject` first (unblocks T-Run5Tz + all CI); then `ClaudeCliSubject`, `AoWorkflowSubject`.
3. `[real_llm]` smoke of cost/token plumbing at haiku.
