# STATUS

- ID: `T-h2XLxe-monitor-abstraction`
- Updated At: 2026-07-15
- State: Done
- Owner: dev-epic agent

## This update
- By: dev-epic agent
- Role: developer
- Date: 2026-07-15
- Comment: Implemented `src/agent_orchestrator/monitoring.py`: `Monitor` ABC,
  `RuleBasedMonitor` (deterministic extend-once-then-halt / retry-once-if-transient policies),
  `AgentMonitor` (invokes a named agent through the existing `Executor`/`TaskContext` machinery,
  strict verdict parsing, safe-default fallback on any error), DTOs (`BreakerTripSummary`,
  `BreakerVerdict`, `TaskFailureSummary`, `HealVerdict`), baked instruction templates, and
  `build_task_failure_summary`. Revised mid-task per the early-gate reviewer's finding: dropped
  the originally-planned `_read_stderr_tail` file read entirely (redundant with
  `executors/claude_cli.py`'s existing bounded `TaskResult.error`) — `stderr_tail` now derives
  from `TaskResult.error` with zero new file I/O. Used a small LOCAL test-double `Executor`
  (`_VerdictWritingExecutor`) for AgentMonitor tests rather than extending the shared
  `FakeExecutor`, per the epic's narrow-scope policy. Confirmed no import cycle with `engine.py`
  (one-directional; `engine.py` will import FROM `monitoring.py`, never the reverse).

## Evidence
- `uv run pytest tests/test_monitoring.py -q` → 42 passed.
- `uv run pytest -q` (full suite) → 735 passed, 3 skipped (693 prior + 42 new, zero regressions).
- `uv run ruff check src/agent_orchestrator/monitoring.py tests/test_monitoring.py` → All checks
  passed. `ruff format --check` → clean. `uv run mypy src/agent_orchestrator/monitoring.py` →
  Success, no issues.

## Risks / Blockers
- None. The `AgentMonitor` file-layout contract (`.orchestrator/runs/<run_id>/monitor/
  call-<n>-<subject>/{instruction.md,context.json,verdict.json,capture/}`) is implemented and
  covered by tests asserting the exact written paths/content.

## Next actions
1. Done — `T-TdildW`/`T-yjtdAq` construct and call into `Monitor` implementations from
   `engine.py`; `T-QyNnf5` constructs `AgentMonitor`/`RuleBasedMonitor` from CLI config.
