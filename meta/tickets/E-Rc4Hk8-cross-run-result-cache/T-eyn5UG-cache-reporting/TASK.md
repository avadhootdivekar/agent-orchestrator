# TASK: T-eyn5UG-cache-reporting

## Metadata
- Task ID: `T-eyn5UG-cache-reporting`
- Epic ID: `E-Rc4Hk8-cross-run-result-cache`
- Owner: `developer` (Dev B)
- Created: `2026-10-04`
- Last Updated: `2026-10-05` (Rev 2)
- Status: `Draft`
- Estimate: `16 focus hours (2 days)` · Sprint 2 (first item for Dev B)

## Requirements Mapping
- Requirement IDs: FR-9, FR-11, NFR-1 (status/usage byte identity)
- HLD: §8.8.1 (`report.py`), §8.8.2 (`status.json`), §8.8.4 (`report-usage` data, `report-outcomes`), §13.5
- ADR-0019: D12, D14, D15, D16

## Description
This task adds the read side of the records. Every function filters through
`models.is_current_result_cache_record`, so no reader can disagree with another.

1. **`cache/report.py`** (pure, O(tasks); depends only on `models` and `constants`):
   - `current_records(state)`;
   - `current_hit(state, tid)`;
   - `task_view(rec)`, which gives the §13.5 per-task object and adds `"hit": outcome == "hit"`;
   - `run_block(state)`, which sums `saved_*` over current hits and `avoidable_cost_usd` over
     current would_hits;
   - `result_cache_status_fields(state)`;
   - `format_summary_line(block)`, which gives the exact §8.8.3 text;
   - `usage_counts(state)`.
2. **`runstate.write_status`** (about 4 lines, §8.8.2):
   - add a per-task `"result_cache"` key only for tasks that have a current record;
   - add a top-level `"result_cache"` block only when `run_block` is not None.
3. **`usage.py`:**
   - `aggregate_usage` **excludes** current hits from group metrics (R-D6);
   - add `UsageReport.result_cache_hits: int = 0` and `result_cache_saved_cost_usd: float = 0.0`,
     accumulated from `usage_counts`;
   - `usage_report_payload` **pops** both keys when `result_cache_hits == 0`.

   The text line for `ao report-usage` is printed by `cli.py` and belongs to T-o95l1M.
4. **`outcomes.py`:**
   - widen `SettleReason` to `Literal["dispatched", "skipped", "cached"]`;
   - change `_settle_reason(ts, state=None, tid=None)`. The new parameters are keyword-optional,
     so the change is backward compatible. It returns `"cached"` when `state` and `tid` are given
     and `current_hit(state, tid)` is true;
   - the existing caller in the grading loop passes `state` and `task_id`.

## File scope (exclusive)
- `src/agent_orchestrator/cache/report.py` (new)
- `src/agent_orchestrator/runstate.py`: `write_status` only
- `src/agent_orchestrator/usage.py`: `aggregate_usage`, `UsageReport`, `usage_report_payload`
- `src/agent_orchestrator/outcomes.py`: `SettleReason`, `_settle_reason` and its one caller
- `tests/cache/test_report.py`, `test_status_result_cache.py`, `test_usage_result_cache.py`,
  `test_outcomes_result_cache.py` (new)

## Inputs / Outputs
- **Inputs:** T-28J9oR (`ResultCacheRecord`, `is_current_result_cache_record`); T-FJH6LI
  (`constants`).
- **Outputs:** the report helpers, consumed by T-o95l1M (CLI lines) and T-bLpoze (dashboard).

## Acceptance Criteria
1. **U-RP1..RP3.** `current_records` and `current_hit` follow the currency truth table, including
   the `ended_at` binding.
   - `task_view` output validates against the §13.5 `resultCacheTask` schema with `jsonschema`.
   - `run_block` output validates against `resultCacheRun`.
2. **U-RP4 (sums).**
   - `run_block` sums `saved_*` over current hits only.
   - `avoidable_cost_usd` sums current would_hits only.
   - `misses`, `ineligible` and `stored` count current records.
   - `run_block(state)` is None when nothing is current.
3. **U-RP5 (summary text).** `format_summary_line` returns exactly:
   `Result cache: hits=2 (saved ~$1.2345 est., ~54000 tokens, ~312s) would_hits=0 misses=1 stored=1 ineligible=1`.
   Use the §8.8.2 example block. `format_summary_line(None)` returns None.
4. **U-RP6.** `usage_counts` returns `(current hits, sum of their saved_cost_usd)`.
5. **U-RP7.** The helpers import only `models` and `constants`; assert on the module's import
   graph.
6. **U-RP8 (stale filtering).** A state containing one current hit, one stale hit (cycle
   mismatch), one stale hit (`ended_at` mismatch) and one current miss gives the same answer
   through **every** helper: `current_records`, `current_hit`, `run_block`, `usage_counts`,
   `write_status`, `aggregate_usage` and `_settle_reason`.
7. **U-RS1 (NFR-1).** With an empty `result_cache` map, `write_status` produces exactly the
   pre-epic top-level key set and per-task key set. Compare against literal lists of key names,
   which removes the dependency on the base-commit golden.
8. **U-RS2.** With a current hit, `status.json` gains `tasks[tid].result_cache` and the
   top-level `result_cache`, and no other key changes.
9. **U-US1..US3.**
   - `aggregate_usage` excludes a current hit from its group metrics (its `dispatch_cycle >= 1`
     no longer counts it as dispatched).
   - A stale hit is counted as before.
   - The totals equal `usage_counts`.
   - `usage_report_payload` has neither key when hits are 0, and has both keys when hits are
     greater than 0.
10. **U-OC1.**
    - `_settle_reason(ts, state, tid)` returns `"cached"` for a current hit.
    - It returns `"dispatched"` for a stale hit and for a normal success.
    - It returns `"skipped"` for a skipped task.
    - `_settle_reason(ts)` with no extra arguments behaves exactly as before.
11. **Hygiene.**
    - Existing `runstate`, `usage` and `outcomes` tests pass unedited.
    - The NFR-2 gate passes.
    - `ruff` and `mypy` are clean.
    - `pytest -q` has no new failures.

## Test requirements
- `tests/cache/test_report.py`: AC-1..AC-6.
- `tests/cache/test_status_result_cache.py`: AC-7 and AC-8.
- `tests/cache/test_usage_result_cache.py`: AC-9.
- `tests/cache/test_outcomes_result_cache.py`: AC-10.
- AC-6 is spread across these modules.

## Risks
- **An existing test asserts the `UsageReport` payload shape.** Mitigation: the keys are popped
  when zero, so the payload is byte-identical when off.
- **Signature change to `_settle_reason`.** Mitigation: the new parameters are optional keyword
  arguments, and no test calls the function directly (verified at `bb6d8a0`).

## Dependencies
- T-28J9oR, T-FJH6LI.

## Pseudocode / Algorithm
```text
HLD §8.8.1–§8.8.4 verbatim. All helpers: for tid, rec in state.result_cache.items():
    if is_current_result_cache_record(rec, state.tasks.get(tid)): ...
```

## Schemas / Interface Notes
- **Spec / data schema:** `status.json` additions (HLD §13.5); usage payload keys
  `result_cache_hits` and `result_cache_saved_cost_usd`.
- **Interface:** the `report.py` functions listed in HLD §8.8.1.

## Handoff Boundary
- **Upstream:** T-28J9oR.
- **Downstream:** T-o95l1M, T-bLpoze, T-JCOAsq.

## Artifacts
- **Docs/comments:** `meta/tickets/E-Rc4Hk8-cross-run-result-cache/T-eyn5UG-cache-reporting/`
- **Large outputs:** N/A

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: Read-side reporting helpers and
  hooks.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2 changes:
  - explicit exclusion of current hits from usage (the `dispatch_cycle` increment is kept, D12);
  - `would_hits` and `avoidable_cost_usd`;
  - `ended_at` binding in every reader;
  - `settle_reason: cached` (reviewer R6);
  - key-set equality replaces the base-golden dependency (developer #10).
