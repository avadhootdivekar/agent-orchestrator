# STATUS

- ID: `T-Sc4Hm2-suite-subject-schemas`
- Updated At: 2026-07-22
- State: Draft
- Owner: developer agent

## This update
- By: architect · Role: architect · Date: 2026-07-22 · Comment: Task specified from design §4.1/§5/§6. Foundation task, no deps — first in Sprint 1.

## Evidence
- Design: `docs-md/benchmarking-framework-hld.md` §4.1, §5, §6.

## Risks / Blockers
- None yet.

## Next actions
1. Author both JSON schemas (`additionalProperties:false`, versioned).
2. Add pydantic models + `load_suite`/`load_subject` + `ao-bench validate`.
3. Unit + CliRunner tests for the reject/accept matrix (AC 1–4).
