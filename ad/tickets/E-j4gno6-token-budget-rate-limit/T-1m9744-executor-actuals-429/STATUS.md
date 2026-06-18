# STATUS

- ID: `T-1m9744-executor-actuals-429`
- Updated At: 2026-06-18
- State: Done
- Owner: manager

## This update
- Added `--output-format json` to `ClaudeCliExecutor` argv via `_ensure_output_format_json()` (idempotent).
- Added pure `parse_usage_and_429(stdout, stderr, returncode, now_epoch)` function: parses `usage` block, detects 429 from JSON error object and exit/stderr text signals.
- `ClaudeCliExecutor.execute()` now populates all `TaskResult` token fields and 429 flags.
- Fallback: any parse failure leaves `actuals_available=False` — engine keeps the estimate.
- Extended `FakeExecutor` with `token_outputs` and `rate_limit_tasks` parameters for integration test injection.
- Fixture saved at `tests/fixtures/claude_usage.json` with real CLI JSON shape (verified against installed claude CLI).
- OPEN_QUESTION on usage shape: RESOLVED — confirmed `total_cost_usd` at top level, `usage` sub-fields match assumed shape exactly.
- Unit tests added to `tests/test_executor.py` (25 new tests).

By: manager · Role: manager · Date: 2026-06-18 · Comment: Implemented and verified. 269 tests pass at completion. CLI usage shape confirmed live.

## Evidence
- `src/agent_orchestrator/executors/claude_cli.py` — `parse_usage_and_429`, `_ensure_output_format_json`
- `src/agent_orchestrator/executors/fake.py` — `token_outputs`, `rate_limit_tasks` params
- `tests/fixtures/claude_usage.json` — real CLI fixture
- `tests/test_executor.py` — 25 new unit tests
- `pytest -q`: 269 passed

## Risks / Blockers
- None.

## Next actions
- Task complete.
