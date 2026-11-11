# STATUS

- ID: `T-Ri7Dz2-run-id-flag`
- Updated At: 2026-11-11
- State: Done
- Owner: TBD (assigned at sprint start)

## Reconciliation 2026-11-11 (By: developer · Role: developer · unit `w24-07-a1-task-status-sync` · docs only)
- Checked against branch `ad/enhancements-10nov` at HEAD `1d5c6f9`. Any older line below that says "not started", "uncommitted" or "in the working tree" is superseded by this block where they disagree.
- State: **Done**
- All ACs met (`tests/test_run_id_flag.py`); AC6 re-verified by the T-Se4Bk5 gate (probe `p6_run_id.py`: traversal/symlink refused, 2 procs x 20 trials = one winner). AC4 wording was replaced by the design review (explicit `launch_run(run_id=)` kwarg); `cost_cap_usd` was later added to the registry-derived `ALLOWED_OPTIONS` by E-Qp4Zm8 (deliberate; `run_id` is not forwardable). No unmet ACs.
- The `Not started` lines in Evidence/Next actions below are template residue from 2026-09-06; ignore them.

## This update
- Ticket created by the `architect` agent as part of the E-Sc9Rt4 design package. Not started.
- Scope: `ao run --run-id` with a path-safe pattern guard and a duplicate-directory refusal, plumbed through `ProcessSupervisor`'s existing options allow-list so scheduled launches never poll for their own run id.

## Evidence
- None yet — no implementation has begun. Design basis:
  `docs-md/scheduler-triggers-hld.md` and `docs-md/adr/ADR-0014-service-owned-scheduler-and-triggers.md`.

## Risks / Blockers
- Blocked until: nothing — independent, can start any time in Sprint 1. `T-Lp4Wt6` needs it before its zero-latency-attribution AC can pass.

## Next actions
1. Read the upstream task's **merged code**, not only the HLD, before starting (the interface-drift
   rule this epic inherits from E-GIytcL).
2. Implement against the acceptance criteria in `TASK.md`, in AC order.
3. Hand the option name and the `ALLOWED_OPTIONS` entry to `T-Lp4Wt6`; do NOT edit `meta/ROADMAP.md` (owned by `T-Dc6Zr2`).
