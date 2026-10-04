# TASK: T-XpF1pF-cache-engine-integration

## Metadata
- Task ID: `T-XpF1pF-cache-engine-integration`
- Epic ID: `E-Rc4Hk8-cross-run-result-cache`
- Owner: `developer` (Dev A)
- Created: `2026-10-04`
- Last Updated: `2026-10-05` (Rev 3)
- Status: `Draft`
- Estimate: `16 focus hours (2 days)` · Sprint 2 → 3

## Requirements Mapping
- Requirement IDs: FR-2, FR-5, FR-6, FR-9, FR-10, NFR-1, NFR-2, NFR-8
- HLD: §8.7.1 (exact seams), §8.7.2 (ordering), §8.7.3 (behaviour), §8.7.4 (hit vs
  `should_skip`), §8.7.5 (no-op claim and evidence)
- ADR-0019: D9, D11, D12, D13

## Description
Wire the result cache into `engine.py` exactly as in HLD §8.7.1 (copy-ready). **Only `engine.py`
changes.**

- **(a)** Constructor keyword `result_cache: ResultCacheHook | None = None` after `run_prompt`,
  with its docstring entry; `ResultCacheHook` and `PendingStore` imported **only under
  `TYPE_CHECKING`**.
- **(b)** `_RunContext.result_cache_pending: dict[str, PendingStore]`.
- **(c)** The prepare call site (5 lines) immediately before `_estimate = 0`, with the seam comment
  that approval and human gates run BEFORE it.
- **(c')** **Extract the existing stale-charge reversal block** (engine.py ~1215–1243) **verbatim**
  into `_reverse_stale_charge(tid, ts, state, run_log)`, and replace it in the budget gate with
  one call. Behaviour is identical (the existing budget resume tests cover it); the event
  `budget.resume_reverse` keeps its fields.
- **(d)** The settle call site (2 lines) at the top of `if ts.status == "succeeded":`.
- **(e)** The private methods `_result_cache_lookup` (the engine-owned hit settle; it calls
  `_reverse_stale_charge` on a hit) and `_result_cache_store`.

**Budget (measured with `ruff format`):** net ≤ +110 formatted lines, ≤ 12 added lines inside
existing functions, every line ≤ 100 columns. **Static-audit rule:** no new text in `engine.py`,
comments included, may match `open(` or `.read(`.

## File scope (exclusive)
- `src/agent_orchestrator/engine.py`
- `tests/cache/test_engine_result_cache.py` (new)

## Inputs / Outputs
- **Inputs:** T-gDNjN2 (`ResultCache`, used through the protocol); **T-JCOAsq Part 1** (the I-1
  and I-2 test code and the base golden); `budget.cycle_key`, `BudgetManager.reverse_estimate`.
- **Outputs:** an engine that consults an injected `ResultCacheHook` and behaves identically
  when `None` is injected.

## Acceptance Criteria
1. **Seams.** The diff matches HLD §8.7.1 (a)–(e); `git diff --stat` and a reviewer count confirm
   the budget above; `ruff check` and `ruff format --check` pass.
2. **NFR-1 evidence, all green:** I-1 (poisoned imports; loaded cache modules ⊆ {`cache`,
   `cache.constants`}); I-2 (serial and `max_parallel=3`); U-AST-E; the existing `engine.py`
   static audits.
3. **I-3.** Miss, then store, then delete the outputs, then hit: 0 executor dispatches on the hit
   run (spy on `FakeExecutor.execute`); identical bytes; `succeeded`; record `hit: true` bound by
   `ended_at`; `cache.hit` and `task.end` (`cached: true`) logged. A test-local
   `CostlyFakeExecutor(FakeExecutor)` supplies `cost_usd` and tokens, because `FakeExecutor`
   reports no cost; the hit record's `saved_cost_usd` and `saved_tokens` are non-zero.
4. **I-4.** In chain `a → b`, both hit on the second run.
5. **I-5.** `max_parallel=4` with mixed hits and misses: hits never reach a worker; final statuses
   and bytes equal the serial run.
6. **I-6 / I-6b (budget).** A hit never calls `gate`, `charge_estimate` or `reconcile` (spy).
   Charge, crash before the worker, resume, hit: the stale `charged_estimate` entry is gone, the
   consumed counters are restored, and `budget.resume_reverse` is logged with the stale cycle.
7. **I-7.** A hit is never evaluated by breakers; `cumulative_*` stay 0 for a first-pass hit.
8. **I-8.** After resume, the hit task stays `succeeded`, is in the initial `done` set, and is
   never looked up again (spy).
9. **I-18.** The hit keeps its incremented cycle; `ui.activity.locate_attempt_dirs` returns `[]`
   for it; a later real dispatch writes to `cycle-2/`.
10. **I-21 / I-22.** A `not_taken` task under `join: any` and a task with a missing required
    input never reach the lookup and get no record.
11. **I-27.** A first-pass hit has `attempts == 0`; a task with `cache: false` under
    `defaults.cache: true` gets no lookup and no record.
12. **Budget-gate regression.** The existing budget resume tests pass unedited after the
    extraction.
13. **Hygiene.** The full suite meets the baseline plus the new tests; `tests/conftest.py` is
    unedited and the NFR-2 gate passes; ruff and mypy are clean.

## Test requirements
- `tests/cache/test_engine_result_cache.py`: AC-3..AC-11 and U-AST-E, with `Orchestrator`, a
  test-local `CountingExecutor`/`CostlyFakeExecutor`, a fixed clock and temp workspaces.
- I-1 and I-2 come from T-JCOAsq Part 1 and must pass before merge.

## Risks
- **Conflicts with E-Ag7Pw3 in `_prepare_and_maybe_dispatch`.** Mitigation: the seam comment and
  the merge note; approval gates go **before** call site (c).
- **A future success side effect skips hits silently.** Mitigation: the rule in HLD §8.7.4/§24.2.

## Dependencies
- T-gDNjN2; **T-JCOAsq Part 1** (base-code only, so it can be done at any earlier point).

## Pseudocode / Algorithm
```text
HLD §8.7.1 code blocks (a)–(e) verbatim.
```

## Schemas / Interface Notes
- **Interface:** `Orchestrator(..., result_cache: ResultCacheHook | None = None)`.
- **Events:** `task.end` with `cached: true` on a hit; `budget.resume_reverse` on a stale-charge
  reversal (gate or hit).

## Handoff Boundary
- **Upstream:** T-gDNjN2, T-JCOAsq Part 1.
- **Downstream:** T-o95l1M, T-JCOAsq Part 2.

## Artifacts
- **Docs/comments:** `meta/tickets/E-Rc4Hk8-cross-run-result-cache/T-XpF1pF-cache-engine-integration/`
- **Large outputs:** N/A

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: Engine seams plus integration tests.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2: engine-owned hit settle,
  kept cycle increment, stale budget reversal; I-1/I-2/U-AST-E as gates.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 3 (early-gate A1b, C, D):
  depends on T-JCOAsq Part 1 only; the stale-charge block is extracted into
  `_reverse_stale_charge` (shared, emits `budget.resume_reverse`); honest budget (net ≤ +110,
  ≤ 12 inside existing functions, ≤ 100 columns); I-27 and the cost-reporting test executor.
