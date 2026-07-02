# TASK: T-pd2vu2-structured-logging

## Metadata
- Task ID: `T-pd2vu2-structured-logging`
- Epic ID: `E-v0f0c9-logging-dynamic-workflows`
- Owner: developer
- Created: 2026-06-18
- Last Updated: 2026-06-18
- Status: Done
- Estimate: `< 2 days`

## Requirements Mapping
- Requirement IDs: FR-2, FR-3, NFR-4

## Description
Introduce structured JSON logging across the engine and executors. Add a new
`logging_setup.py` module providing a stdlib `JSONFormatter` and a per-run log
handler that writes `.orchestrator/runs/<run_id>/run.log` (one JSON object per
line) in addition to console output. The handler is attached at the start of
`Orchestrator.run()` and detached in a `finally` block so handlers never leak
across runs. `run_id`/`task_id` flow into log records via a `LoggerAdapter` or
`extra=` so call sites stay clean. No new third-party dependency.

## Acceptance Criteria
1. Given a run, when it completes, then `.orchestrator/runs/<run_id>/run.log`
   exists and every non-empty line parses as a JSON object with at least:
   `ts`, `level`, `logger`, `event` (or `msg`), and `run_id`.
2. Given a task-scoped log call, when emitted, then the record includes the
   `task_id` field.
3. Given two sequential runs in the same process, when both complete, then each
   `run.log` contains only its own run's records (no handler leakage / cross-write).
4. Given `run()` raises an exception, when it unwinds, then the per-run handler
   is still detached (verified: no lingering handler on the root/engine logger).
5. No new entry added to project dependencies; only stdlib `logging` + existing deps used.
6. `ruff check`, `ruff format --check`, `mypy`, and `pytest` pass on touched files.

## Implementation Notes
- Used `_MergingAdapter(LoggerAdapter)` to properly merge call-site `extra=` with adapter extra; the stdlib `LoggerAdapter.process()` replaces rather than merges, which would silently drop `event` fields.
- Console + file share one `JSONFormatter` instance per run; handler lifecycle owned by `Orchestrator.run()` in `try/finally`.

## Risks
- Global logging state leaking between runs → mitigated with explicit attach/detach + unique handler per run_id.
- Over-logging volume → keep field set minimal; log at INFO for transitions, DEBUG for detail.

## Dependencies
- None (foundational; other Area-1 tasks build on it).

## Artifacts
- Code: `src/agent_orchestrator/logging_setup.py`, wiring in `engine.py`.
- Tests: `tests/test_logging.py`, `tests/conftest.py`.
