# TASK: T-vmI0jI-e2e-failure-scenarios

## Metadata
- Task ID: `T-vmI0jI-e2e-failure-scenarios`
- Epic ID: `E-YAAGhk-overseer-runner-template`
- Owner: tester
- Created: 2026-09-26
- Last Updated: 2026-09-26
- Status: Draft
- Estimate: 3 days (24 h)

## Requirements Mapping
- Requirement IDs: FR-6, FR-10, FR-13, FR-16, FR-18, FR-19 (end to end), NFR-3
- Design: `docs-md/overseer-runner-hld.md` §18 (E2E failure), §8.5, §12.3; ADR-0016 D7/D9

## Description
Build scenarios (e)–(h) in `tests/test_e2e_overseer_runner_failures.py`, reusing the T-WruPiv
harness.

- **(e) Backstop + G5 + unit gate.** Set `run_budget_usd=50`. Script `ck-01`'s own cost so that
  cumulative spend crosses 50 **at `ck-01`'s settle**. Expect:
  - `run-budget-backstop` trips, the run is `failed`
  - the `w02-*` and `ck-02` entries are in `state.injected_tasks` with status `pending` (G5)
  - a plain `ao resume` (without `--extend-breaker`) makes every `w02-*` unit fail its `ov-unit-gate`
    pre-hook with a `BUDGET:` error at $0, and the run halts again
  - `ck-02` is never reached while `w02-*` are pending, because the checkpoint depends on them

  - **(e1) close-out path (FR-19)**: `overseer_tool.py request-closeout --reason test` →
    `ao resume` → every pending `w02-*` unit is `skipped` ($0, no executor call) → `ck-02` digest
    has `must_close` with reason `forced_closeout` → the tail runs → `outputs/final/closeout.md`
    exists → the run is `succeeded`.
  - **(e2) continue path (FR-16)**, in a fresh workspace with the same trip: writing
    `budget-override.json` (100) *without* extending still gives `BUDGET:` refusals, and
    `ckpt-prep` records the override as refused. Then `ao resume --extend-breaker
    run-budget-backstop` → the units run → `ck-02` computes its stage from B = 100 → the run
    eventually closes out.

  Note (design §8.5, Rev 2): a plain resume does NOT reach close-out on its own. The checkpoint
  depends on the pending units, and the gate fails them. The test pins this behavior.
- **(f) Signal response.** Script the ledger into `[review:fail, fix:pass, review:fail, fix:pass]` on
  one work item across waves, so `period_repeat` fires. Variant 1: the verdict omits a response, so
  `ck-*` fails with `OV-R12`. Variant 2: the verdict responds `redirect` and the next brief carries
  `approach_change`, so it passes.
- **(g) Cancel.** Create `control/halt.flag` while wave 1 is mid-way (a scripted side effect of unit
  `w01-01`). Expect a halt at the next boundary. Remove the flag and run `ao resume`, then check
  that settled units are not re-run (the executor invocation counter) and the run completes.
- **(h) Parallel.** With `max_parallel=2`, wave 1 has two independent units. Both settle before
  `ck-01` dispatches: the executor call log shows `ck-01` strictly after both. The ledger has two
  unit lines.

## Acceptance Criteria
1. All four scenarios pass deterministically (three consecutive runs).
2. (e) asserts, in order:
   - the trip at `ck-01` settle
   - the injected ids persisted as pending
   - the `BUDGET:` refusal at $0 on plain resume (executor never called for that unit)
   - (e1) skipped units, the `forced_closeout` event, and a `closeout.md` from a forced-closeout
     checkpoint
   - (e2) the override refused without extension, success after extend + override, and the ledger
     `budget_override` event present exactly once
3. (f) asserts the exact `OV-R12` rule id in variant 1 and success in variant 2.
4. (g) asserts no double execution of settled units.
5. (h) asserts the ordering from the executor call log. It doesn't use timestamps.
6. Any deviation from design §8.5/§12.3 found here is recorded in STATUS.md and handed to
   T-gbccdr. Pass/fail counts and a `reviewer`-agent review are recorded in STATUS.md.

## Risks
- (e) depends on T-pYt478 being merged first. Without it, the injected tasks are lost and the
  scenario fails by design (a useful regression signal).
- `max_parallel` configuration path: verify whether it is a CLI flag or `.ao/config.yaml` (memory:
  E-IasNXu) before writing (h).

## Dependencies
- T-WruPiv (harness), T-pYt478 (G5), T-ABDjSj (unit gate), T-tAKBBB (R12).

## Pseudocode / Algorithm
```text
Reuse tests/overseer_runner_harness.py; each scenario = script dict + a sequence of CLI invocations
+ state/ledger/digest assertions.
```

## Schemas / Interface Notes
- None new.

## Handoff Boundary
- Upstream: T-WruPiv, T-pYt478.
- Downstream: T-gbccdr (deviations), T-23yMMB.

## Artifacts
- `tests/test_e2e_overseer_runner_failures.py`
