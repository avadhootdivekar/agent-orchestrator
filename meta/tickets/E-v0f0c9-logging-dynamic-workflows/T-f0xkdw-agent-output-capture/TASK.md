# TASK: T-f0xkdw-agent-output-capture

## Metadata
- Task ID: `T-f0xkdw-agent-output-capture`
- Epic ID: `E-v0f0c9-logging-dynamic-workflows`
- Owner: developer
- Created: 2026-06-18
- Last Updated: 2026-06-18
- Status: Done
- Estimate: `< 2 days`

## Requirements Mapping
- Requirement IDs: FR-4, FR-5, NFR-1

## Description
Capture each executor run's stdout/stderr to per-task artifact files so
downstream agents/auditors can read them by path. `ClaudeCliExecutor.execute()`
currently discards captured output on success; it must write it to
`<output_dir>/stdout.txt` and `<output_dir>/stderr.txt` where `output_dir =
.orchestrator/runs/<run_id>/<task_id>/`. Add `output_dir` to `TaskContext` and
`output_artifact_path` to `TaskResult` (and mirror onto `TaskRunState`). The
engine records the path only — it never reads the captured content (NFR-1).

## Acceptance Criteria
1. Given a task executes via `ClaudeCliExecutor`, when it finishes (success OR
   failure OR timeout), then `<output_dir>/stdout.txt` and `<output_dir>/stderr.txt`
   exist (empty files allowed when no output) and `TaskResult.output_artifact_path`
   points to `<output_dir>`.
2. Given the engine processes the result, when it records state, then
   `TaskRunState.output_artifact_path == result.output_artifact_path` and the
   engine performs NO read of the captured files.
3. Given `FakeExecutor`, when used in tests, then it honors the same contract
   (writes capture files / sets `output_artifact_path`) so integration tests are deterministic.
4. `output_dir` is resolved via `ArtifactStore.resolve` (path-traversal safe).
5. `ruff`, `mypy`, `pytest` pass on touched files.

## Artifacts
- Code: `src/agent_orchestrator/models.py`, `executors/claude_cli.py`, `executors/fake.py`, `engine.py`.
- Tests: `tests/test_status_artifact.py` (TestOutputCapture class).
