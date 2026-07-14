# STATUS

- ID: `T-69MnaW-run-active-seconds-breaker`
- Updated At: 2026-07-14
- State: **Done**
- Owner: developer

## This update (2026-07-14)
`run_active_seconds` landed: `BreakerCondition` Literal + `specs/workflow.schema.json`'s
`condition` enum (+ threshold-required `allOf`) both gained the entry; `RunActiveSecondsBreaker`
registered in `BREAKER_REGISTRY["run_active_seconds"]` (`breakers.py`), summing
`_settled_task_active_seconds(state)` — a pure reconstruction over `state.tasks[*]
.started_at/ended_at`, same style as the existing `_consecutive_failure_streak`. All 7 TASK.md
acceptance criteria covered by `tests/test_run_active_seconds_breaker.py` (11 tests): threshold
exactness (below/at/above), multi-task summation, the pause-gap differentiator vs
`run_wall_clock_seconds` (same fixture: wall-clock trips, active-seconds does not), JSON
round-trip resume-safety, `run_wall_clock_seconds` untouched, never calls `time.time()`
directly, registered at import time. Schema validation test added to
`tests/test_routing_breaker_models.py` (accepts with threshold; rejects without, via the
existing parametrized conditional-required test). `uv run pytest tests/
test_run_active_seconds_breaker.py tests/test_routing_breaker_models.py -q` -> 44 passed.
`ruff check`/`ruff format --check`/`mypy` clean on both touched source files
(`breakers.py`, `models.py`) and both test files.

By: developer · Role: developer · Date: 2026-07-14 · Comment: T-69MnaW DONE. All 7 acceptance
criteria verified with tests; `run_wall_clock_seconds` proven byte-identical (existing
`tests/test_mvp_breaker_conditions.py::TestRunWallClockSecondsBreaker` still green, unmodified).

## Post-review fix (2026-07-14)
The `T-gzG0EI` reviewer pass (agent id `a862873eeae56a8a8`) found and reproduced a real bug this
task's own semantics depend on: `engine.py`'s single `ts.started_at` writer was unconditionally
reset on every quota/429/budget-wait redispatch of the SAME task, silently excluding the wait
from `run_active_seconds`'s sum — contradicting this task's own AC (in-task waits DO count).
Fixed by guarding the write in `engine.py` (~:462) to fire only on a task's first-ever dispatch;
confirmed safe (grep: exactly one writer, one reader — this breaker — in the whole codebase).
New integration test added: `TestRunActiveSecondsCountsQuotaWaitTime` (drives a real
`Orchestrator` + `FakeExecutor(quota_exhausted_tasks=...)` through 2 quota-wait redispatches,
monkeypatching `engine.py`'s `datetime.now` to a controlled stepping fake since
`started_at`/`ended_at` use the real clock, not the injectable one). `uv run pytest
tests/test_run_active_seconds_breaker.py -q` -> 12 passed (was 11 before this fix).

By: developer · Role: developer · Date: 2026-07-14 · Comment: Post-review fix applied and
pinned by a new test; full suite re-confirmed green (676 passed, 3 skipped).

## Evidence
- `src/agent_orchestrator/models.py`: `BreakerCondition` Literal gains `"run_active_seconds"`.
- `src/agent_orchestrator/breakers.py`: `_settled_task_active_seconds`, `RunActiveSecondsBreaker`,
  registry entry, module docstring bumped (eight -> nine conditions).
- `specs/workflow.schema.json`: `condition` enum + threshold-required `allOf` both updated.
- `tests/test_run_active_seconds_breaker.py` (new, 11 tests, all passing).
- `tests/test_routing_breaker_models.py` (+2: conditional-required param case, threshold-accepts
  case).

## Risks / Blockers
- None.

## Next actions
1. None — task complete. Feeds into `T-yX1Oi5`/`T-gzG0EI` (docstring/docs already reflect this
   task's landing).
