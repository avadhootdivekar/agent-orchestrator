# HANDOFF: T-eyn5UG-cache-reporting

- Task: `T-eyn5UG-cache-reporting`
- State: `Done (handoff available)`
- From: `developer` (Dev B)
- To: T-o95l1M, T-bLpoze, T-nPMuz4, T-JCOAsq
- Commit: `cbf9152` on branch `worktree-agent-a18ce2c08e42a3a5a`.

## What was delivered
- `agent_orchestrator.cache.report` (depends on `models` only): `current_records`, `current_hit`,
  `task_view`, `run_block`, `result_cache_status_fields`, `format_summary_line`, `usage_counters`;
  constants `BRIEF_FIELDS`, `UNKNOWN_REASON`.
- `RunStateStore.write_status`: per-task `result_cache` (current records only) and the top-level
  `result_cache` run block; both omitted when nothing is current.
- `usage.py`: both dispatch-counting sites skip current hits (a hit's real carried spend still
  counts), `ResultCacheUsage`, `UsageReport.result_cache` (None unless a scanned run has current
  records), `usage_report_payload` omits the key when None.
- `outcomes.py`: `SettleReason` gains `"cached"`; `_settle_reason(ts, *, state=None, tid=None)`.
- Tests: `tests/cache/test_report.py`, `test_status_result_cache.py`, `test_usage_result_cache.py`,
  `test_outcomes_result_cache.py`, `test_lazy_read_sides.py`, and the shared builders and 13.5 /
  13.6 schemas in `tests/cache/_report_states.py` (reusable by the next tasks).

## Frozen names / contracts
- The `status.json` shapes (HLD 13.5), the usage object (13.6) and the summary-line text (8.8.3).
- Callers import `cache.report` inside the function, only when `state.result_cache` is non-empty.
  A `status.json` snapshot dict carries the run block already; `_print_status_snapshot` formats it
  directly (T-o95l1M).
- `report.run_block` / `usage_counters` round floats (6 and 3 digits); `format_summary_line` prints
  cost with 4 decimals and seconds with 0.

## Verification the receiver should run
- `pytest -q tests/cache/test_report.py tests/cache/test_status_result_cache.py tests/cache/test_usage_result_cache.py tests/cache/test_outcomes_result_cache.py tests/cache/test_lazy_read_sides.py`

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: Handoff stub created.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2 contents.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 3: exact fields, usage
  object, lazy imports. State `Draft` mirrors `TASK.md` and `STATUS.md`.
- By: developer · Role: developer · Date: 2026-10-05 · Comment: State -> Done; handoff available
  (commit `cbf9152`). Deviations are small and documented in `STATUS.md`.
