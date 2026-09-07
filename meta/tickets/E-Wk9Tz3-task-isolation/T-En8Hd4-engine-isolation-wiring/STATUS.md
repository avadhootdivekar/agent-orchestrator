# STATUS

- ID: `T-En8Hd4-engine-isolation-wiring`
- Updated At: 2026-09-07
- State: Draft
- Owner: unassigned

## This update
- Ticket created by the architect as part of the `E-Wk9Tz3-task-isolation` design package. Not
  started; no code written.
- **2026-09-07 — Phase-2 review amendment applied.** Folded in: R-19 (now AC-15 and the **first** thing to implement — without it isolated tasks still run in the shared checkout), R-2, R-3 (branch 2 of `should_skip` named explicitly), R-5 (the wave-fill call site, previously owned by no ticket), R-23, S-7. R-1/R-21 moved out to `T-Ac6Vd9`; R-4/R-12 moved out to `T-Wl2Bq7`, so `budget.py` and the sync lock are no longer this ticket's files. Estimate unchanged at 3 days.
- Per-finding dispositions: HLD §24 "Review dispositions".

## Evidence
- Design: [`docs-md/task-isolation-hld.md`](../../../../docs-md/task-isolation-hld.md) (see the
  module section named in `TASK.md`) and
  [`ADR-0013`](../../../../docs-md/adr/ADR-0013-per-task-git-isolation-and-rebase-integration.md).

## Risks / Blockers
- See `TASK.md` > Risks. Blocked only by the dependencies listed in `TASK.md` > Dependencies.

## Next actions
1. Read `TASK.md`, then the HLD section it names, then the **merged code of every dependency task**
   (do not re-derive an interface from the design doc alone).
2. Implement, run `uv run pytest -q` plus `ruff check` / `ruff format --check` / `uv run mypy src`,
   and record before/after counts in this file.
3. Update `TASK.md` Status and this file together, with By/Role/Date attribution.
