# STATUS

- ID: `T-FJH6LI-cache-contracts`
- Updated At: `2026-10-05`
- State: `Draft`
- Owner: `developer` (Dev A)

## This update
- Rev 3 ticket: no dependency on T-28J9oR (type-only reference); fixed commit numbering; new
  constants (`DEFAULT_TASK_CACHE_POLICY`, AgentSpec field sets, inline byte bound,
  `GIT_OPTIONAL_LOCKS`); `CacheUnsafePathError`; read-only `verify()`. Estimate unchanged (16 h).

## Evidence
- None yet (not started). Design evidence: HLD Rev 3 and ADR-0019 Rev 3.

## Risks / Blockers
- No blockers.
- Risk: renaming a frozen contract after consumers start; record any change in each consumer's `STATUS.md`.

## Next actions
1. Commit 1: `cache/__init__.py` + `cache/constants.py` (≤ 3 h).
2. Commit 2: `cache/safeio.py` + `test_safeio.py` (≤ 5 h).
3. Commit 3: `types.py`, fakes, contract suite, corpus, AST guard; run ruff, mypy and pytest.

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: Status initialized (Draft).
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2 re-plan after the Phase-4
  consultation.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 3 re-plan after the
  early-gate review and the manager's scope decisions (HLD §23.5). State stays `Draft`
  (core set, first); this file, `TASK.md`, `HANDOFF.md` (when present) and the epic `STATUS.md` rollup
  agree.
