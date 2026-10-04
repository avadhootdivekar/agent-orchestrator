# HANDOFF: T-eyn5UG-cache-reporting

- Task: `T-eyn5UG-cache-reporting`
- State: `Draft` (handoff not yet available)
- From: `developer` (Dev B)
- To: T-o95l1M, T-bLpoze, T-nPMuz4, T-JCOAsq

## What will be handed over
- `agent_orchestrator.cache.report`: `current_records`, `current_hit`, `task_view`, `run_block`,
  `result_cache_status_fields`, `format_summary_line`, `usage_counters`.
- `write_status` additions; `UsageReport.result_cache` (§13.6); `SettleReason` with `"cached"`.

## Frozen names / contracts
- The `status.json` shapes (§13.5), the usage object (§13.6) and the summary-line text (§8.8.3).
- Callers import `cache.report` lazily and only when `state.result_cache` is non-empty.

## Verification the receiver should run
- `pytest -q tests/cache/test_report.py tests/cache/test_status_result_cache.py tests/cache/test_usage_result_cache.py tests/cache/test_outcomes_result_cache.py tests/cache/test_lazy_read_sides.py`

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: Handoff stub created.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2 contents.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 3: exact fields, usage
  object, lazy imports. State `Draft` mirrors `TASK.md` and `STATUS.md`.
