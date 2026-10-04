# HANDOFF: T-eyn5UG-cache-reporting

- Task: `T-eyn5UG-cache-reporting`
- State: `Draft` (handoff not yet available)
- From: `developer` (Dev B)
- To: T-o95l1M, T-bLpoze, T-JCOAsq

## What will be handed over
- `agent_orchestrator.cache.report`: `current_records`, `current_hit`, `task_view`,
  `run_block`, `result_cache_status_fields`, `format_summary_line` and `usage_counts`.
- `write_status` additions.
- `UsageReport.result_cache_hits` and `UsageReport.result_cache_saved_cost_usd`.
- `SettleReason` now includes `"cached"`.

## Frozen names / contracts
- The `status.json` shapes (HLD §13.5).
- The summary-line text (HLD §8.8.3).

## Verification the receiver should run
- `pytest -q tests/cache/test_report.py tests/cache/test_status_result_cache.py tests/cache/test_usage_result_cache.py tests/cache/test_outcomes_result_cache.py`

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: Handoff stub created.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2 contents. State `Draft`
  mirrors `TASK.md` and `STATUS.md`.
