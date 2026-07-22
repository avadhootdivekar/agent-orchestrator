# STATUS

- ID: `T-Pl3Rx7-parallel-bench-runner`
- Updated At: 2026-07-22
- State: Draft (designed; not started)
- Owner: TBD (assigned at sprint start)

## This update
- Bounded ThreadPoolExecutor (--max-parallel, default 1) with lock-guarded budget/state.

## Evidence
- Design + acceptance criteria specified in this folder's TASK.md; part of epic E-Bt4Xk9.

## Risks / Blockers
- Dependencies: T-Bg2Wq4 (same runner.py; must be budget-under-concurrency correct)

## Next actions
1. Add thread-pool path + lock; keep serial default byte-identical; deterministic id-sorted persist.
