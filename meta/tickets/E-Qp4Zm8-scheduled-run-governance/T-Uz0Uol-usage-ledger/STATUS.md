# STATUS

- ID: `T-Uz0Uol-usage-ledger`
- Updated At: 2026-11-10
- State: Draft
- Owner: TBD (assigned at sprint start)

## This update
- Ticket created by the `architect` agent as part of the E-Qp4Zm8 design package. Not started.
- Scope: Derived usage ledger: `FireGovernance` record, `charge()`, per-window usage, high-water mark, cost confidence, index + mtime cache; fire-store additive methods.

## Evidence
- None yet. No implementation has begun. Design basis:
  `docs-md/scheduled-run-governance-hld.md` and `docs-md/adr/ADR-0025-scheduled-run-governance-quotas-and-cost-caps.md`.

## Risks / Blockers
- Blocked until E-Sc9Rt4 `T-Fr2Nx8`, `T-tPlK1T` and `T-lJu9oZ` have merged.

## Next actions
1. Read the upstream tasks' **merged code** (including E-Sc9Rt4's), not only the HLD, before starting.
2. Implement against the acceptance criteria in `TASK.md`, in AC order; report the pytest delta, ruff/format, mypy, coverage.
3. Hand `usage()`/`charge()` to `T-PpE5lf`, `T-rwj4nV`, `T-8knqVe`.
