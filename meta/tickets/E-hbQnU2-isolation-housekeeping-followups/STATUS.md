# STATUS

- ID: `E-hbQnU2-isolation-housekeeping-followups`
- Updated At: 2026-09-21
- State: Draft (backlog — not started)
- Owner: unassigned

## This update
Epic created as a spin-off from Epic C's (`E-DOiDqE-workflow-authoring-skill`) C0
reliability/failure-mode audit. Four small, previously-unticketed isolation-subsystem
follow-ups (worktree-porcelain `-z` parsing, `_pid_alive`/`_read_os_boot_id` triplication,
a possible T2-resolver dispatch-window race, `GitRepo.diff_patch()`) were found explicitly
flagged as "no ticket filed"/"no ticket named" in `docs-md/task-isolation-hld.md`. Filed here
so they are visible to planning instead of staying buried in prose. Not implemented as part of
Epic C (out of that epic's scope per its own change-boundary rules).

**Reconciliation (2026-09-21):** this ticket was found already written but uncommitted/untracked
on disk (a prior interrupted Epic C attempt, IDs `E-lBessP-workflow-authoring-skill`/
`T-V66ejq-reliability-audit`, neither of which exists on disk). Content verified accurate and
adopted; back-references corrected to point at this run's actual ticket IDs.

By: dev-epic · Role: developer · Date: 2026-09-21 · Comment: Found during Epic C's C0 audit
while surveying `meta/learnings.md`, `meta/ROADMAP.md` §4, and past ticket REVIEW/STATUS notes
for DAG-authoring failure patterns; these four are unrelated engine/isolation code debt, not
skill-authoring content, so spun off per Epic C's "do NOT fold unrelated fixes into this
epic's scope" instruction rather than fixed inline.

## Evidence
- `docs-md/task-isolation-hld.md`, "Deferred with rationale, discovered during implementation"
  section: 3 explicit "no ticket filed"/"no ticket named" items (worktree porcelain parsing,
  `_pid_alive`/boot-id triplication, T2 resolver race).
- `meta/tickets/E-Wk9Tz3-task-isolation/T-Lr6Ka3-llm-resolver-and-rerun/STATUS.md` lines ~89,
  ~176: `GitRepo.diff_patch()` "filed as a follow-up suggestion" in prose only, no ticket.
- No pre-existing ticket under `meta/tickets/` covers any of these four items (checked via
  `grep -rn "diff_patch"` and manual read of `E-Wk9Tz3`'s task list before filing this epic).

## Risks / Blockers
- None blocking — all four items are independently small, low-risk, non-behavior-changing
  cleanups. Not urgent; filed for visibility, not because of any live incident.

## Next actions
1. Prioritize against other backlog epics — none of these are gating anything today.
2. When picked up: `T-9oDDDU-isolation-followups-cleanup` implements FR-1..FR-4 from EPIC.md,
   each with its own regression test.
