# TASK: T-Wx8vUq-tests-e2e-monitoring

## Metadata
- Task ID: `T-Wx8vUq-tests-e2e-monitoring`
- Epic ID: `E-XyfjuZ-agent-monitoring-self-healing`
- Owner: dev-epic agent (self-implemented)
- Created: 2026-07-14
- Last Updated: 2026-07-14
- Status: Draft
- Estimate: 1.5 day

## Requirements Mapping
- Requirement IDs: all FR/NFR (verification layer)

## Description
Full deterministic test matrix (fixed/stepping clocks, injected sleeper, `FakeExecutor`, no real
`claude` spawned in any required test) across new files:
- `tests/test_monitoring.py` — `RuleBasedMonitor`/`AgentMonitor` unit tests (via `FakeExecutor`).
- `tests/test_monitoring_breaker_consult.py` — Consult Point A engine integration tests.
- `tests/test_monitoring_self_heal.py` — Consult Point B engine integration tests.
- CliRunner e2e additions (new file `tests/test_e2e_monitoring_cli.py` or appended to
  `tests/test_e2e_cli.py` if closer to convention — decide by re-reading that file's structure
  first) covering config/CLI wiring end-to-end.
- Schema/model drift-guard test (can live in `tests/test_routing_breaker_models.py` alongside
  existing breaker-model tests, or a new dedicated file).

Must cover every Acceptance Criteria bullet listed in each of the 5 preceding tasks' TASK.md files
plus the epic-level Acceptance Criteria, including explicitly:
- bounds exhausted (both `max_extensions_per_breaker` and `max_heal_retries_per_task`)
- `max_monitor_calls_per_run` cap exhaustion (shared across both consult points)
- monitor invalid verdict / non-succeeded executor result → safe default
- mixed hard+recommend trips at one boundary → no consult at all
- resume with persisted monitor state (extensions count, heal-retry count, calls-made count all
  carried over correctly)
- self-heal on a task that then succeeds, and self-heal on a task that fails again after the
  healed retry
- old `state.json` (missing all 4 new `RunState` fields) resumes cleanly

## Acceptance Criteria
1. Every acceptance criterion listed in `T-mYMiPK`, `T-h2XLxe`, `T-TdildW`, `T-yjtdAq`,
   `T-QyNnf5` TASK.md files has at least one passing test exercising it.
2. `uv run pytest -q` reports a strictly-increasing pass count over the 686-passed/3-skipped
   baseline, zero new failures, zero skips added (unless explicitly `real_llm`-marked and
   opt-in).
3. Coverage does not regress below the 91% baseline (`uv run pytest -q --cov=agent_orchestrator
   --cov-report=term-missing`).
4. At least 2 new CliRunner-based end-to-end tests exist (memory `engine-api-tests-dont-cover-cli`
   — engine-API tests alone are insufficient).
5. `uv run ruff check .` / `uv run ruff format --check .` / `uv run mypy src` show ZERO new
   findings versus the captured baseline (2 ruff errors / 4 mypy errors, both pre-existing and
   unrelated).

## Risks
- Medium: test volume is large; risk of superficial tests that don't actually exercise the bound
  logic. Mitigation: each bound/cap test uses a monitor double that would give the WRONG answer if
  the bound weren't enforced (e.g., "always extend"), so a regression in the engine's bound
  enforcement fails the test even though the monitor itself "worked."

## Dependencies
- `T-mYMiPK`, `T-h2XLxe`, `T-TdildW`, `T-yjtdAq`, `T-QyNnf5` (tests the combined output of all
  five).

## Pseudocode / Algorithm
N/A (test-writing task; see Description for file layout and Acceptance Criteria for coverage
requirements).

## Schemas / Interface Notes
- Interface / API: none new (tests only).
- Spec / data schema (JSON/YAML): test fixtures may include a `specs/examples/workflow-monitoring.json`-shaped inline JSON (the actual example file itself is `T-H8Mmog`'s responsibility, but this task's CliRunner tests may construct similar inline specs, matching existing test conventions e.g. `tests/test_resume_extend_breaker_cli.py::_write_specs`).
- Triggers / events (cron/event): N/A.
- Artifacts (inputs/outputs by path): N/A (test-only).

## Handoff Boundary
- Upstream: all 5 prior tasks.
- Downstream: `T-H8Mmog` late-gate `tester` run references these tests + adds one end-to-end
  representative-workflow execution as independent evidence.

## Artifacts
- Docs/comments: `meta/tickets/E-XyfjuZ-agent-monitoring-self-healing/T-Wx8vUq-tests-e2e-monitoring/`
- Large outputs: none.
