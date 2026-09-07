# STATUS

- ID: `T-Wk3Nv6-worktree-lifecycle`
- Updated At: 2026-09-07
- State: Draft
- Owner: unassigned

## This update
- Ticket created by the architect as part of the `E-Wk9Tz3-task-isolation` design package. Not
  started; no code written.
- **2026-09-07 — Phase-2 review amendment applied.** Folded in: S-4 (per-task `IsolatedArtifactView` + the task-A-cannot-reach-task-B test), R-6 (all prune sites use `prune_worktrees_scoped`), R-11 (shared `xdg.resolve_state_dir`, migrating `service/paths.py`), R-8 (`declared_outputs` on `TaskIsolation`), S-9 (0700 dirs). New files: `xdg.py`, `isolation/view.py`. **Re-estimated 2.5 -> 3 days.**
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
