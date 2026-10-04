# TASK: T-eyn5UG-cache-reporting

## Metadata
- Task ID: `T-eyn5UG-cache-reporting`
- Epic ID: `E-Rc4Hk8-cross-run-result-cache`
- Owner: `developer` (Dev B)
- Created: `2026-10-04`
- Last Updated: `2026-10-05` (Rev 3)
- Status: `Draft`
- Estimate: `18 focus hours (2.25 days)` · Sprint 2 · engine set

## Requirements Mapping
- Requirement IDs: FR-9, FR-11, NFR-1 (status/usage byte identity, lazy imports)
- HLD: §8.8.1 (`report.py`), §8.8.2 (`status.json`), §8.8.4 (`report-usage` data,
  `report-outcomes`), §13.5, §13.6
- ADR-0019: D9, D12, D14, D15, D16, D34, D35

## Description
The read side of the records. Every function filters through
`models.is_current_result_cache_record`. **Every caller imports `cache.report` inside the
function, and only when `state.result_cache` is non-empty** (D9), so a run the cache never
touched never loads it.

1. **`cache/report.py`** (pure, O(tasks); depends only on `models` and `constants`; HLD §8.8.1):
   `current_records`, `current_hit`, `task_view` (exactly the D35 fields `hit`, `key`,
   `saved_cost_usd`, `saved_tokens`, `saved_seconds`, plus the additive fields), `run_block`
   (`hits`, `saved_cost_usd`, `saved_tokens`, `saved_seconds`, `would_hits`, `misses`,
   `ineligible`, `stored`, `lookups`, the token split, `avoidable_cost_usd`),
   `result_cache_status_fields`, `format_summary_line`, `usage_counters`.
2. **`runstate.write_status`** (about 6 lines, HLD §8.8.2): per-task `"result_cache"` only for
   current records; the top-level `"result_cache"` block only when `run_block` is not None.
3. **`usage.py`** (HLD §8.8.4):
   - **Site A (group metrics, usage.py ~409):** a current hit is not counted in `tasks`,
     `succeeded`, `failed` or `retried`, but its **real** carried `cumulative_*` spend
     (hit-after-spend) is still added to the group sums.
   - **Site B (producer attribution, usage.py ~472):** a producer that is a current hit is
     skipped, like a never-dispatched producer.
   - **`UsageReport.result_cache: ResultCacheUsage | None = None`**, the cross-run object of
     §13.6: `hits`, `saved_cost_usd`, `saved_tokens`, `saved_seconds`, plus the G0 fields
     `lookups`, `would_hits`, `misses`, `ineligible`, `avoidable_cost_usd`, `miss_reasons`,
     `store_skip_reasons`. `usage_report_payload` omits the key when it is None. (The text lines
     are printed by `cli.py`: T-o95l1M.)
4. **`outcomes.py`:** `SettleReason` widened to include `"cached"`;
   `_settle_reason(ts, *, state=None, tid=None)` returns `"cached"` for a current hit; the one
   caller passes `state` and `task_id`.

## File scope (exclusive)
- `src/agent_orchestrator/cache/report.py` (new)
- `src/agent_orchestrator/runstate.py`: `write_status` only
- `src/agent_orchestrator/usage.py`: sites A and B, `UsageReport.result_cache`,
  `usage_report_payload`
- `src/agent_orchestrator/outcomes.py`: `SettleReason`, `_settle_reason` and its caller
- `tests/cache/test_report.py`, `test_status_result_cache.py`, `test_usage_result_cache.py`,
  `test_outcomes_result_cache.py`, `test_lazy_read_sides.py` (new)

## Inputs / Outputs
- **Inputs:** T-28J9oR (`ResultCacheRecord`, `is_current_result_cache_record`); T-FJH6LI.
- **Outputs:** the report helpers for T-o95l1M (CLI) and T-bLpoze (dashboard); the G0
  measurement object for T-nPMuz4.

## Acceptance Criteria
1. **U-RP1..RP3.** `current_records` and `current_hit` follow the currency truth table, including
   the `ended_at` binding. `task_view` output validates against §13.5 `resultCacheTask` and
   contains exactly the five D35 fields plus the listed additive fields; `run_block` validates
   against `resultCacheRun`.
2. **U-RP4 (sums).** `saved_*` (including `saved_tokens`) sum current hits only;
   `avoidable_cost_usd` sums current would_hits; `lookups == hits + would_hits + misses`;
   `run_block(state)` is None when nothing is current.
3. **U-RP5.** `format_summary_line` returns exactly
   `Result cache: hits=2 (saved ~$1.2345 est., ~54000 tokens, ~312s) would_hits=0 misses=1 stored=1 ineligible=1`
   for the §8.8.2 example; `format_summary_line(None)` is None.
4. **U-RP6.** `usage_counters(state)` returns the per-run contribution to §13.6, or None.
5. **U-RP7.** `report.py` imports only `models` and `constants`.
6. **U-RP8 (stale filtering).** One current hit, one stale hit (cycle mismatch), one stale hit
   (`ended_at` mismatch) and one current miss give the same answer through every helper,
   `write_status`, `aggregate_usage` and `_settle_reason`.
7. **U-RS1 (NFR-1).** With an empty map, `write_status` produces exactly the pre-epic top-level
   and per-task key sets (literal lists).
8. **U-RS2.** With a current hit, `status.json` gains `tasks[tid].result_cache` and the top-level
   `result_cache`, and nothing else changes.
9. **U-US1..US4.**
   - Site A: a current first-pass hit is not counted in tasks/succeeded/failed/retried and adds 0
     spend; a **hit-after-spend** task (carried `cumulative_cost_usd = 0.5`) adds its 0.5 and its
     tokens to the group but is still not counted as a dispatched task.
   - Site B: a current-hit producer receives no review attribution.
   - The cross-run object sums `usage_counters` over runs and validates against §13.6;
     `usage_report_payload` omits `result_cache` when no run has records and is otherwise
     byte-identical to the pre-epic payload.
10. **U-OC1.** `_settle_reason(ts, state=..., tid=...)` is `"cached"` for a current hit and
    `"dispatched"` for a stale hit or a normal success; `_settle_reason(ts)` behaves as before.
11. **U-LZ1 (lazy imports).** In a subprocess, with `result_cache == {}`, calling
    `write_status`, `aggregate_usage` and `_settle_reason` leaves
    `agent_orchestrator.cache.report` out of `sys.modules`; with a non-empty map it is loaded.
12. **Hygiene.** Existing `runstate`, `usage` and `outcomes` tests pass unedited; the NFR-2 gate
    passes; ruff (≤ 100 columns) and mypy are clean; `pytest -q` has no new failures.

## Test requirements
- `tests/cache/test_report.py`: AC-1..AC-5; `test_status_result_cache.py`: AC-7, AC-8;
  `test_usage_result_cache.py`: AC-9; `test_outcomes_result_cache.py`: AC-10;
  `test_lazy_read_sides.py`: AC-11. AC-6 spans these modules.

## Risks
- **An existing test asserts the `UsageReport` payload shape.** Mitigation: the object is omitted
  when absent, so the payload is byte-identical when off.
- **Signature change to `_settle_reason`.** Keyword-only optional parameters; no test calls it
  directly (verified at `bb6d8a0`).

## Dependencies
- T-28J9oR, T-FJH6LI.

## Pseudocode / Algorithm
```text
HLD §8.8.1–§8.8.4 verbatim. Every caller: if state.result_cache: (lazy import) ...
```

## Schemas / Interface Notes
- **Spec / data schema:** `status.json` additions (§13.5); `report-usage --json` `result_cache`
  (§13.6).
- **Interface:** the `report.py` functions of HLD §8.8.1.

## Handoff Boundary
- **Upstream:** T-28J9oR.
- **Downstream:** T-o95l1M, T-bLpoze, T-nPMuz4, T-JCOAsq.

## Artifacts
- **Docs/comments:** `meta/tickets/E-Rc4Hk8-cross-run-result-cache/T-eyn5UG-cache-reporting/`
- **Large outputs:** N/A

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: Read-side reporting helpers.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2: hit exclusion, shadow
  counters, `ended_at` binding, `settle_reason: cached`, key-set equality test.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 3 (early-gate C; manager B):
  exact D35 fields; both usage sites, keeping a hit's real carried spend; the cross-run
  `result_cache` usage object with the G0 fields; lazy `cache.report` imports (U-LZ1);
  re-estimated from 16 h to 18 h.
