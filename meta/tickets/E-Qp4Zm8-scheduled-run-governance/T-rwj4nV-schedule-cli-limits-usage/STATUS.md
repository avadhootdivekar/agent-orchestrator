# STATUS

- ID: `T-rwj4nV-schedule-cli-limits-usage`
- Updated At: 2026-11-10
- State: Draft
- Owner: TBD (assigned at sprint start)

## This update
- Ticket created by the `architect` agent as part of the E-Qp4Zm8 design package. Not started.
- Scope: `ao schedule add` limit/prompt flags, `set-limits`, `pin-prompt`, `usage`, `resume`; list/history/next columns; shared `usage_view` builder.

## Evidence
- None yet. No implementation has begun. Design basis:
  `docs-md/scheduled-run-governance-hld.md` and `docs-md/adr/ADR-0025-scheduled-run-governance-quotas-and-cost-caps.md`.

## Risks / Blockers
- Blocked until E-Sc9Rt4 `T-Cl6Jn9`, `T-Uz0Uol`, `T-PpE5lf` (and `T-nI4DBf` for `resume`) have merged.

## Next actions
1. Read the upstream tasks' **merged code** (including E-Sc9Rt4's), not only the HLD, before starting.
2. Implement against the acceptance criteria in `TASK.md`, in AC order; report the pytest delta, ruff/format, mypy, coverage.
3. Hand `build_usage_doc` to `T-8knqVe`.
