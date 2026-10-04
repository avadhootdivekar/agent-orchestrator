# STATUS

- ID: `T-FJH6LI-cache-contracts`
- Updated At: `2026-10-05`
- State: `Draft`
- Owner: `developer` (Dev A)

## This update
- Rev 2 ticket: scope now covers `safeio.py`, the total parse boundary, the split `CacheStore` / `CacheAdmin`
  ABCs, `ResultCacheHook`, the lookup contracts, `canonical_json`, the AST guard and the hostile corpus.
  Estimate 16 h.

## Evidence
- None yet (not started). Design evidence: HLD Rev 2 and ADR-0019 Rev 2.

## Risks / Blockers
- No blockers.
- Risk: renaming a frozen contract after consumers start; record any change in each consumer's `STATUS.md`.

## Next actions
1. Commit 1: `cache/__init__.py` + `cache/constants.py` (≤ 3 h); notify T-28J9oR.
2. Commit 2: `cache/safeio.py` + `test_safeio.py` (≤ 5 h); notify T-8tr1H4.
3. Commit 3: `types.py`, fakes, contract suite, corpus, AST guard; run ruff, mypy and pytest.

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: Status initialized (Draft).
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2 re-plan after the Phase-4
  consultation. State stays `Draft` (Sprint 1, Wave 1); this file, `TASK.md`, `HANDOFF.md` (when present)
  and the epic `STATUS.md` rollup agree.
