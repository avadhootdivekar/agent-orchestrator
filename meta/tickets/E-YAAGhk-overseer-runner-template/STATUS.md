# STATUS

- ID: `E-YAAGhk-overseer-runner-template`
- Updated At: 2026-09-26
- State: Draft (design complete; implementation not started)
- Owner: architect → dev-epic

## This update
- The architecture package is complete (Rev 2): `docs-md/overseer-runner-hld.md` (sections 1–25),
  ADR-0016 (D1–D9), and 15 task tickets. 1 is Done (design); 14 are Draft (13 MVP + 1 MVP-Should
  below the cut line).
- Phase-4 consultations were done with all six roles. The record is in the design doc §23.3.
- Rollup: MVP tasks 0/13 done · MVP-Should 0/1 · design 1/1.

By: architect · Role: architect · Date: 2026-09-26 · Comment: The design stays within existing engine
primitives (recursive `emit_tasks` waves, pre/post hooks, breakers, `state.json`), with **one**
separately scoped engine correctness fix. That fix is G5 (T-pYt478): emissions are lost when a
breaker trips or the process crashes at an emitter's settle. It was reproduced empirically, it is
unavoidable at template level, and it also fixes a latent `routed-runner` exposure. It should merge
to `main` as its own PR first.

The consultations changed the design materially:
- the tool reads `state.json` rather than `status.json`
- `instruction` is pinned per kind
- a $0 per-unit budget gate was added, because breakers latch
- hash-chained ledger events make the hold and override tamper-evident
- FR-16 and FR-17 were promoted to MVP
- the checker and e2e were split
- the team was re-planned to 4 developers × 2 sprints

A final self-review added FR-19 (`request-closeout`) after finding that a plain resume following a
budget trip cannot reach close-out on its own.

## Consultation summary (details in design doc §23.3)
| Role | Top finding | Resolution |
|---|---|---|
| manager | FR-16/FR-17 are needed for MVP; the checker and e2e were under-estimated | Promoted; split into T-HPJcc6/T-tAKBBB and T-WruPiv/T-vmI0jI |
| developer | BLOCKER: `status.json` has no per-task timestamps | The tool reads `state.json` (field-contract test) |
| reviewer | Attempt-cap rename dodge; unjustified `deferred` | OV-R13c; `deferred_reason` + evidence under OV-R12 |
| tester | Missing cancel, parallel, and idempotency e2e; coverage of a path-loaded file | Scenarios (g)/(h) and the (d) ledger assertion; `--cov=<tools path>` |
| dev-security | CRITICAL: unit `instruction` not pinned | OV-R6 exact `kind_map` match |
| dev-critic | Breaker latch means plain resume has no wall after G5 | FR-18 unit gate (+ FR-19) |

## Evidence
- Design: `docs-md/overseer-runner-hld.md`; ADR: `docs-md/adr/ADR-0016-overseer-runner-cadence-and-budget-governance.md`
- G5 repro: `output/E-YAAGhk-overseer-runner-template/repro_emit_lost_on_breaker_trip.py`

## Risks / Blockers
- No blockers.
- Top risks: G5 test blast radius (named suites); an LLM overseer ignoring stages (mitigated
  mechanically); holds rendering as `failed` (NFR-X6); governance being tamper-evident only
  (NFR-X11). The full list is in design doc §23.

## Next actions
1. dev-epic: confirm the decomposition; start `T-pYt478` (G5) and the S1 parallel tasks (`T-ABDjSj`, `T-eGXqXH`, then `T-C6uQJW`, `T-ltBLUY`, `T-5ZzAZp`).
2. Merge G5 to `main` as its own PR before the template PR.
3. After S2: `T-23yMMB` smoke evidence → `T-gbccdr` docs reconciliation → epic closure.
