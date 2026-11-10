# STATUS

- ID: `T-Hq3vXd-docs-refresh`
- Updated At: 2026-11-11
- State: Partial (docs synced to landed code; epic NOT closeable)
- Owner: TBD (assigned at sprint start)

## This update
- 2026-11-11 — By: developer (unit `w20-08-a2-docs-adr-sync`) · Role: developer · Date: 2026-11-11 · Comment: PARTIAL, honest. Done: HLD status line + §4 line refs refreshed + new §26 (As built, deviations, unfinished); ADR-0025 status + "Implementation outcome" (kept Proposed, not Accepted); epic narrative evidence log; `run-options-and-limits-guide.md` (cost cap section, marked flag-not-committed); `guide-sentinel.md` (cap interaction, kill under-count); all task STATUS + epic rollup.
  NOT done (with reason): (a) `scheduler-triggers-hld.md` cross-links - file has another unit's uncommitted edits, not touched; (b) `meta/ROADMAP.md` rows - outside this unit's brief; (c) AC3 "Recurring jobs with limits" walkthrough with captured output - the CLI is uncommitted, writing it would describe unlanded behaviour; (d) AC1 full grep of every name - done for events, constants, flags and routes cited in §26 (greps over `src/`), not exhaustively over HLD §1-§24, which remains design text (§26 supersedes it); (e) `record_trip` docstring - needs `src/`, out of scope. Re-run this task after the carry-over units commit.
- Ticket created by the `architect` agent as part of the E-Qp4Zm8 design package. Not started.
- Scope: Post-implementation reconciliation of docs-md, ADR-0025, E-Sc9Rt4 HLD cross-links, run-options guide, sentinel guide, ROADMAP.

## Evidence
- None yet. No implementation has begun. Design basis:
  `docs-md/scheduled-run-governance-hld.md` and `docs-md/adr/ADR-0025-scheduled-run-governance-quotas-and-cost-caps.md`.

## Risks / Blockers
- Blocked until all other tasks (incl. `T-lTW88y` final gate) are done.

## Next actions
1. Read the upstream tasks' **merged code** (including E-Sc9Rt4's), not only the HLD, before starting.
2. Implement against the acceptance criteria in `TASK.md`, in AC order; report the pytest delta, ruff/format, mypy, coverage.
3. Grep every documented public name against `src/` before marking done.
