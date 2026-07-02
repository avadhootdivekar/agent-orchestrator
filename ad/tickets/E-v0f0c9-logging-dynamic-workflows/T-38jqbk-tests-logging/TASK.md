# TASK: T-38jqbk-tests-logging

## Metadata
- Task ID: `T-38jqbk-tests-logging`
- Epic ID: `E-v0f0c9-logging-dynamic-workflows`
- Owner: tester
- Created: 2026-06-18
- Last Updated: 2026-06-18
- Status: Done
- Estimate: `< 2 days`

## Requirements Mapping
- Requirement IDs: FR-1, FR-2, FR-3, FR-4, FR-5, FR-6, NFR-2

## Description
Unit + integration tests for Feature Area 1 (structured logging, agent output
capture, status artifact, `ao status`). Use `FakeExecutor` and a fixed clock for
determinism. Verify the NFR-1 boundary is preserved (engine records output paths
but never reads them).

## Acceptance Criteria (all met)
1. Unit: `JSONFormatter` emits valid JSON with required fields.
2. Unit: `write_status` projection matches `RunState`.
3. Unit: per-run handler attach/detach leaves no lingering handler.
4. Integration: full FakeExecutor run produces `run.log` (valid JSON lines) + `status.json` consistent with `state.json`.
5. Integration: each task's `stdout.txt`/`stderr.txt` exist and `TaskRunState.output_artifact_path` is set.
6. Integration: `ao status <run_id>` prints from `status.json`; falls back to `state.json` when absent.
7. Coverage ≥80% on logging_setup.py, status-writer code, capture code paths.
8. pytest green; ruff/mypy clean.

## Artifacts
- Code: `tests/conftest.py`, `tests/test_logging.py`, `tests/test_status_artifact.py`.
