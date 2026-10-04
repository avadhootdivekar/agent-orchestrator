# TASK: T-XpF1pF-cache-engine-integration

## Metadata
- Task ID: `T-XpF1pF-cache-engine-integration`
- Epic ID: `E-Rc4Hk8-cross-run-result-cache`
- Owner: `developer` (Dev A)
- Created: `2026-10-04`
- Last Updated: `2026-10-05` (Rev 2)
- Status: `Draft`
- Estimate: `16 focus hours (2 days)` · Sprint 2

## Requirements Mapping
- Requirement IDs: FR-5, FR-6, FR-9, FR-10, NFR-1, NFR-2, NFR-8
- HLD: §8.7.1 (exact seams a–e), §8.7.2 (ordering), §8.7.3 (behaviour), §8.7.4 (hit vs
  `should_skip`), §8.7.5 (no-op claim and evidence)
- ADR-0019: D9, D11, D12, D13

## Description
Wire the result cache into `engine.py` exactly as in HLD §8.7.1. **Only `engine.py` changes.**

- **(a) Constructor.** Add the keyword `result_cache: ResultCacheHook | None = None` after
  `run_prompt`, plus its docstring entry. Import `ResultCacheHook` and `PendingStore` **only under
  `TYPE_CHECKING`**.
- **(b) `_RunContext`.** Add `result_cache_pending: dict[str, PendingStore]`.
- **(c) Prepare call site.** Four formatted lines, immediately before `_estimate = 0`, after the
  dynamic-input collection. Include the seam comment that approval and human gates run BEFORE it.
- **(d) Settle call site.** Two lines at the top of `if ts.status == "succeeded":` in
  `_settle_completed_task`.
- **(e) `_result_cache_lookup`.** A private method that, in order:
  1. imports `LookupRequest` lazily;
  2. pops the pending entry;
  3. calls `lookup`;
  4. writes the record;
  5. on a non-hit, stores the pending token and returns False;
  6. on a hit (the engine-owned settle):
     - sets `status=succeeded` and `outputs_present`;
     - sets `started_at` if it is None, and `ended_at`;
     - binds `record.ended_at`;
     - reverses a **stale** budget charge with `reverse_estimate` at cycle
       `ts.dispatch_cycle - 1`;
     - logs `task.end` with `cached: True`;
     - adds the task to `done`, saves, and returns True.

  **`dispatch_cycle` keeps its increment.**
- **(e) `_result_cache_store`.** A private method that pops the pending entry, calls
  `store_success`, and updates `stored` / `store_reason` on the record.

**Static-audit rule.** No new text in `engine.py`, including comments, may match `open(` or
`.read(`.

**Line budget (NFR-8).** At most 80 formatted lines in total, of which at most 8 are inside
existing functions.

## File scope (exclusive)
- `src/agent_orchestrator/engine.py`
- `tests/cache/test_engine_result_cache.py` (new)

## Inputs / Outputs
- **Inputs:** T-gDNjN2 (`ResultCache`, used through the protocol); T-JCOAsq parts 1–2 (the I-1
  and I-2 test code and the base golden); `budget.cycle_key` and
  `BudgetManager.reverse_estimate`.
- **Outputs:** an engine that consults an injected `ResultCacheHook`, and behaves identically when
  `None` is injected.

## Acceptance Criteria
1. **Seams.** The diff matches HLD §8.7.1 (a)–(e). The `git diff --stat` and a reviewer count
   confirm at most 80 formatted lines, at most 8 of them inside existing functions.
2. **NFR-1 evidence, all green:**
   - **I-1:** the poisoned hook, plus a subprocess `sys.modules` check;
   - **I-2:** `status.json` and stdout byte-identical to the base golden, with `<WS>` normalized;
   - **U-AST-E:** every `ImportFrom` of `.cache…` is inside `TYPE_CHECKING` or
     `_result_cache_lookup`;
   - the existing `engine.py` static audits (no `open(` or `.read(`).
3. **I-3.** Miss, then store, then delete the outputs, then hit:
   - 0 executor dispatches on the hit run, counted with a spy on `FakeExecutor.execute`;
   - identical output bytes;
   - the task is `succeeded`;
   - the record has `outcome=hit` and is bound by `ended_at`;
   - `cache.hit` and `task.end` (with `cached: true`) are logged.
4. **I-4.** In chain `a → b`, both tasks hit on the second run.
5. **I-5.** With `max_parallel=4` and mixed hits and misses, hits never reach a worker. The final
   `state.json` task statuses and output bytes equal those of the serial run.
6. **I-6 / I-6b (budget).**
   - A hit never calls `gate`, `charge_estimate` or `reconcile` (spy).
   - **I-6b:** charge, then a simulated crash before the worker runs, then resume, then hit. The
     stale `charged_estimate` entry is gone and the consumed counters are restored.
7. **I-7 (breakers).** A hit is never evaluated by breakers, and `cumulative_*` stay 0 for a
   first-pass hit.
8. **I-8 (resume after a hit).** The task stays `succeeded`, is in the initial `done` set, and is
   never looked up again (spy).
9. **I-18 (`dispatch_cycle`).**
   - The hit keeps its incremented cycle.
   - `ui.activity.locate_attempt_dirs` returns `[]` for the hit cycle.
   - A later real dispatch writes to `cycle-2/`.
10. **I-21 / I-22.**
    - Under `join: any`, a `not_taken` dependency means the task never reaches the lookup and gets
      no record.
    - A missing required input fails before the lookup and gets no record.
11. **Hygiene.**
    - The full suite meets the baseline (5041 passed / 8 skipped / 2 known bench failures) plus
      the new tests.
    - `tests/conftest.py` is unedited and the NFR-2 gate passes.
    - `ruff` and `mypy` are clean.

## Test requirements
- `tests/cache/test_engine_result_cache.py`: AC-3..AC-10 and U-AST-E. Use `Orchestrator` with a
  test-local `CountingExecutor(FakeExecutor)`, a fixed clock and temp workspaces. The coordinator
  is real, with fakes where needed.
- I-1 and I-2 come from T-JCOAsq and must pass on this branch before merge.

## Risks
- **Conflicts with E-Ag7Pw3 in `_prepare_and_maybe_dispatch`.** Mitigation: the seam comment and
  the merge note. Approval gates go **before** call site (c).
- **A future success side effect added to `_settle_completed_task` silently skips hits.**
  Mitigation: the documented rule in HLD §8.7.4 and §24.2.

## Dependencies
- T-gDNjN2.
- T-JCOAsq parts 1–2 (the I-1/I-2 test code and base golden) must be present on the branch.

## Pseudocode / Algorithm
```text
HLD §8.7.1 code blocks (a)–(e) verbatim.
```

## Schemas / Interface Notes
- **Interface:** `Orchestrator(..., result_cache: ResultCacheHook | None = None)`.
- **Events:** existing `task.end`, now with `cached: true` on a hit.

## Handoff Boundary
- **Upstream:** T-gDNjN2, T-JCOAsq (I-1/I-2).
- **Downstream:** T-o95l1M (passes `result_cache=`), T-JCOAsq (integration hardening).

## Artifacts
- **Docs/comments:** `meta/tickets/E-Rc4Hk8-cross-run-result-cache/T-XpF1pF-cache-engine-integration/`
- **Large outputs:** N/A

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: Engine seams plus integration
  tests.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2, re-estimated from 24 h to
  16 h because the CLI wiring moved to T-o95l1M. Changes:
  - **Engine-owned hit settle** through private methods and the `ResultCacheHook` Protocol
    (reviewer R5).
  - **`dispatch_cycle` keeps its increment** (critic #8a, reviewer R4).
  - **Stale budget-charge reversal** (reviewer R3, developer #14).
  - **`task.end` emitted on a hit** (critic #8b).
  - **I-1, I-2 and U-AST-E are acceptance gates** (reviewer R10).
