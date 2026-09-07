# STATUS

- ID: `T-Wl2Bq7-workspace-run-lock`
- Updated At: 2026-09-07
- State: Draft
- Owner: unassigned

## This update
- Ticket created on 2026-09-07 by the Phase-2 review-amendment pass, splitting findings out of
  `T-En8Hd4-engine-isolation-wiring` so every task stays within the epic's 3-day cap. Not started;
  no code written.

## Evidence
- Design: [`docs-md/task-isolation-hld.md`](../../../../docs-md/task-isolation-hld.md) (§12.3, §13,
  §11 M5) and [`ADR-0013`](../../../../docs-md/adr/ADR-0013-per-task-git-isolation-and-rebase-integration.md).
- Originating findings and their dispositions: HLD §24 "Review dispositions".

## Risks / Blockers
- See `TASK.md` > Risks. Blocked by the dependencies listed in `TASK.md` > Dependencies —
  in particular the **merged** `engine.py` from `T-En8Hd4`.

## Next actions
1. Read `TASK.md`, then HLD §24's row for each originating finding, then the **merged code of every
   dependency task** (do not re-derive an interface from the design doc alone).
2. Implement, run `uv run pytest -q` plus `ruff check` / `ruff format --check` / `uv run mypy src`,
   and record before/after counts here.
3. Update `TASK.md` Status and this file together, with By/Role/Date attribution.
