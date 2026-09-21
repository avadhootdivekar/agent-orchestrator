# EPIC: E-5I8azA-nfr2-gate-stale-exception

## Metadata
- Epic ID: `E-5I8azA-nfr2-gate-stale-exception`
- Title: NFR-2 pre-epic-test-suite gate is red at baseline — one file's cosmetic reformat was never registered as an exception
- Owner: dev-epic (spun off during Epic C, not implemented here)
- Created: 2026-09-21
- Last Updated: 2026-09-21
- Status: Draft (backlog)

## Summary
- Goal: Track a real, currently-failing baseline test on `ad/cost-perf-hooks-skills`:
  `tests/test_nfr2_regression_gate.py::TestPreEpicTestsUnedited::test_every_pre_epic_test_file_is_byte_identical_to_its_pre_epic_content`
  fails because `tests/test_e2e_builtin_routed_runner.py` differs from its content at
  `git merge-base HEAD ad/multi-workspace-service` — but the actual diff (verified, see
  Evidence) is a **pure `ruff format` cosmetic reformat** (line-wrapping only; zero semantic
  change), not a real edit. It was never added to the gate's `_EPIC_MODIFIED_PRE_EPIC_TESTS`
  exception registry, so the gate has been red since whichever commit reformatted it landed.
- Found while: running Epic C's (`E-DOiDqE-workflow-authoring-skill`) full-suite baseline pass
  (`.venv/bin/python -m pytest -q -m "not real_llm and not swebench"`) before touching anything —
  confirmed via `git diff b849b7c HEAD -- tests/test_e2e_builtin_routed_runner.py` that every
  hunk is a formatting-only change (e.g. multi-line function calls collapsed/expanded, no
  literal/logic touched). This predates Epic C's own changes entirely (Epic C has not modified
  any test file) and is unrelated to skill-authoring, so it is spun off here per Epic C's "do
  NOT fold unrelated fixes into this epic's scope" instruction.
- Scope In (MVP, if picked up): add a one-line entry to `_EPIC_MODIFIED_PRE_EPIC_TESTS` in
  `tests/test_nfr2_regression_gate.py` for `tests/test_e2e_builtin_routed_runner.py`, documented
  as a cosmetic-reformat-only exception (mirrors the file's own documented convention — see its
  module docstring for the expected justification style); or, if preferred, revert the
  formatting-only diff on that one file to restore byte-identity instead of declaring an
  exception (implementer's choice, whichever the gate's own convention prefers — check
  `test_no_stale_exception_entries` and existing exception-entry precedent first).
- Scope Out: any other gate/test-infra work; re-running `ruff format` repo-wide; investigating
  which specific commit introduced the reformat (not necessary to fix it, though noting it would
  be a nice-to-have for the fix's own commit message).

## Requirements

### Functional
- FR-1: The NFR-2 pre-epic-test-suite gate (`TestPreEpicTestsUnedited`) passes again on this
  branch, either via a documented exception entry or by reverting the cosmetic diff.

### Non-functional
- NFR-1: The fix itself must not touch the *logic* of
  `tests/test_e2e_builtin_routed_runner.py` — this is a hygiene fix for the gate's own
  bookkeeping (or a pure formatting revert), not a test-behavior change.

## Task List
- [ ] `T-zqhY3v-register-formatting-exception` — Register the exception (or revert the
  formatting-only diff) and confirm the gate passes.

## Risks and Dependencies
- None significant — a one-file, mechanical fix. Low priority: it is a hygiene/gate-bookkeeping
  gap, not a functional regression (the underlying test's assertions are semantically unchanged
  and still pass; only the byte-identity meta-check fails).

## Links
- Found via: Epic C's pre-change baseline run,
  `meta/tickets/E-DOiDqE-workflow-authoring-skill/STATUS.md`.
- Evidence: `git diff b849b7c HEAD -- tests/test_e2e_builtin_routed_runner.py` (pure
  reformatting, verified by inspection — every hunk is whitespace/line-wrap only).
- Related: `tests/test_nfr2_regression_gate.py` (the gate itself, `E-Wk9Tz3` T-Ee3Mn8 AC-1).
- Output artifacts (if any): none yet — not implemented in this pass.
