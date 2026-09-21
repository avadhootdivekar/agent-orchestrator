# STATUS

- ID: `T-Te3Qw8-integration-e2e-suite`
- Updated At: 2026-09-06
- State: Draft
- Owner: TBD (assigned at sprint start)

## This update
- Ticket created by the `architect` agent as part of the E-Sc9Rt4 design package. Not started.
- Scope: the integration tier (real engine, stubbed processes, single-stepped) and the `CliRunner` e2e tier, plus the two cross-cutting proofs no single task can own: NFR-1 backward compatibility and NFR-2 daemon isolation.

## Evidence
- None yet — no implementation has begun. Design basis:
  `docs-md/scheduler-triggers-hld.md` and `docs-md/adr/ADR-0014-service-owned-scheduler-and-triggers.md`.

## Risks / Blockers
- Blocked until: every implementation task through `T-Ap1Xs3` has landed; the per-trigger e2e ACs are gated individually on `T-Fw8Gp4` / `T-Wh9Kv1` / `T-Un0Lm6`.

## Next actions
1. Read the upstream task's **merged code**, not only the HLD, before starting (the interface-drift
   rule this epic inherits from E-GIytcL).
2. Implement against the acceptance criteria in `TASK.md`, in AC order.
3. Report hard numbers (suite delta, per-module coverage, ruff/mypy) and an explicit list of what is NOT exercised; propose — never apply — any production change a test needs.
