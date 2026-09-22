# TASK: T-zqhY3v-register-formatting-exception

## Metadata
- Task ID: `T-zqhY3v-register-formatting-exception`
- Epic ID: `E-5I8azA-nfr2-gate-stale-exception`
- Owner: unassigned
- Created: 2026-09-21
- Last Updated: 2026-09-21
- Status: Draft (not started)
- Estimate: < 1 hour

## Requirements Mapping
- Requirement IDs: FR-1, NFR-1 (see `EPIC.md`)

## Description
`tests/test_e2e_builtin_routed_runner.py` differs from its content at
`git merge-base HEAD ad/multi-workspace-service` by a pure `ruff format` reformat (verified: every
diff hunk is a line-wrap/whitespace change, zero literal or logic touched). This trips
`TestPreEpicTestsUnedited` in `tests/test_nfr2_regression_gate.py`. Either:
(a) add an entry to `_EPIC_MODIFIED_PRE_EPIC_TESTS` (that dict, ~line 136) documenting this as a
cosmetic-reformat-only exception, matching the justification style of existing entries; or
(b) revert just the formatting on that one file to restore byte-identity with the merge-base
(check first whether that would fight the repo's own `ruff format .` pre-commit/CI expectation —
if so, prefer (a)).

## Acceptance Criteria
1. `tests/test_nfr2_regression_gate.py::TestPreEpicTestsUnedited::test_every_pre_epic_test_file_is_byte_identical_to_its_pre_epic_content`
   passes.
2. `tests/test_nfr2_regression_gate.py::TestPreEpicTestsUnedited::test_no_stale_exception_entries`
   (if that's its exact name — confirm) still passes, i.e. the new exception entry is real and
   used.
3. `tests/test_e2e_builtin_routed_runner.py`'s own test logic is unchanged (only formatting, or
   only a registry-file addition elsewhere).
4. Full suite re-run, pass/fail counts reported; no new failures introduced.

## Risks
- None significant.

## Dependencies
- None.

## Pseudocode / Algorithm
```text
N/A — one-line registry addition or a formatting revert.
```

## Schemas / Interface Notes
- Interface / API: N/A
- Spec / data schema: N/A
- Triggers / events: N/A
- Artifacts (inputs/outputs by path): `tests/test_nfr2_regression_gate.py` (registry) or
  `tests/test_e2e_builtin_routed_runner.py` (formatting revert).

## Handoff Boundary
- Upstream: Epic C's baseline pytest run (`meta/tickets/E-DOiDqE-workflow-authoring-skill/STATUS.md`).
- Downstream: none — leaf fix.

## Artifacts
- Docs/comments: `meta/tickets/E-5I8azA-nfr2-gate-stale-exception/T-zqhY3v-register-formatting-exception/`
- Large outputs: none expected.
