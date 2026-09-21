# STATUS

- ID: `T-17PhBS-validate-injected-depends-on`
- Updated At: 2026-09-21
- State: Draft (not started)
- Owner: unassigned

## This update
Task filed alongside its epic (`E-Grpp0X-injected-task-dag-validation-gap`), spun off from
Epic C's C0 audit. Not started; no code changes made in this pass (C0 is a time-boxed scoping
input — implementation is explicitly out of Epic C's scope).

By: dev-epic · Role: developer · Date: 2026-09-21 · Comment: Filed for visibility; the
underlying finding was independently re-verified against current `engine.py`/`dag.py`/`spec.py`
(see epic STATUS.md Evidence) rather than taken on the originating 2026-07-04 learning's word
alone.

## Evidence
- None yet — no implementation work done.

## Risks / Blockers
- None blocking. See `EPIC.md` Risks and Dependencies.

## Next actions
1. Reproduce the current failure mode with a test (FR-2).
2. Implement the fix (FR-1, stretch FR-3) per `TASK.md`.
