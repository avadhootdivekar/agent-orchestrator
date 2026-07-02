# TASK: T-67kiia-tests-budget

## Metadata
- Task ID: `T-67kiia-tests-budget`
- Epic ID: `E-j4gno6-token-budget-rate-limit`
- Owner: TODO
- Created: 2026-06-18
- Last Updated: 2026-06-18
- Status: Draft
- Estimate: < 3 days

## Requirements Mapping
- Requirement IDs: FR-1..FR-11, NFR-2, NFR-3 (verification of all)

## Description
Full unit + integration test coverage for budgeting/rate-limiting, all deterministic
(fixed clock + injected sleeper, per CLAUDE.md). Reuse the epic's existing
`conftest.py` fixtures (`fixed_clock`, `workspace`, `store`, `rs_store`, `make_workflow`,
`read_jsonl`) and extend `FakeExecutor` (token outputs + 429 injection from T-1m9744).

### Unit tests
- `test_estimator.py`: chars/4 + buffer + output allowance math (golden value), zero/missing files, dynamic inputs included, multi-byte note, pluggable stub estimator.
- `test_budget_manager.py`: total-cap admit/block; rate-window admit/block; window roll at exact boundary (`now == start+window`), before, and after; `next_available_epoch` exact values under fixed clock; `charge → reconcile` net math; **double reconcile is no-op**; `reverse_estimate` un-charges; `on_provider_429` with and without retry-after; no-limit passthrough.
- `test_executor_usage.py`: `parse_usage_and_429` over valid usage, missing `usage`, malformed JSON, 429 with/without retry-after; `--output-format json` appended once, not duplicated; fallback keeps fields None.

### Integration tests (FakeExecutor + fixed clock/sleeper)
- `test_budget_stop_total.py`: total cap blocks task 2 with `stop`; task 2 stays `pending`; run non-success; `budget.exhausted` logged.
- `test_budget_stop_rate.py`: rate cap blocks with `stop` (blocked_by="rate").
- `test_budget_wait_rate.py`: rate cap with `wait`; sleeper called with exact `next_available - now`; window rolls; run completes.
- `test_budget_wait_429.py`: FakeExecutor returns `provider_rate_limited` once with retry-after; `wait` sleeps to reset; estimate reversed; task re-runs; completes; `budget.provider_429`+`budget.resume` logged.
- `test_budget_resume_continuance.py`: a stopped-on-exhaustion run resumes with a larger total; **no completed task re-charged**; final `consumed_tokens == sum of reconciled actuals`.
- `test_reconcile_actuals.py`: actuals replace estimate (net delta) end-to-end; `actuals_available=False` keeps estimate.
- `test_no_budget_regression.py`: `budget_manager=None` → identical behavior to a pre-epic run (status, RunState shape).

## Acceptance Criteria
1. Every FR (FR-1..FR-11) and NFR-2/NFR-3 has at least one asserting test mapped to it (traceability table in the test module docstrings).
2. All wait/clock assertions use the injected `sleeper`/`clock` and assert **exact** sleep durations and `next_available_epoch` values (no real sleeping, no wall-clock).
3. The double-charge guard is proven: a test charges an estimate, kills/resumes before reconcile, and asserts `consumed_tokens` is correct after resume (no double count).
4. `pytest -q` green for the full suite (new + pre-existing — zero regressions); `ruff check`, `ruff format --check`, `mypy` clean.
5. Coverage ≥80% line coverage on `estimator.py`, `budget.py`, and the new branches in `engine.py`/`claude_cli.py` (report attached to STATUS).
6. Schema round-trip test: an example spec with a full `budget` block loads → re-serializes → re-validates against `workflow.schema.json`.
7. NFR-1 audit test/grep: no artifact-content read added outside the existing control allow-list (estimator uses `store.size`/`os.stat` only).

## Risks
- Flaky time assertions if any real `time.sleep`/`datetime.now` leaks in — assert the injected sleeper/clock were used; ban wall-clock in budget tests.
- Coverage of the engine wait re-gate loop is tricky — drive it with a FakeExecutor + fixed clock that rolls the window after one sleep.

## Dependencies
- Upstream: all implementation tasks (T-oh5gl5, T-n7hmwj, T-7kp8iv, T-1m9744, T-algywf, T-xefapr).
- Downstream: T-5igs6g (docs reference verified behavior).

## Pseudocode / Algorithm
```text
# example: wait-rate integration
clock = stepping_clock([T0, T0, T0+61, ...])      # advances after sleep
sleeps = []
orch = Orchestrator(fake_exec, store, rs_store, sleeper=sleeps.append,
                    budget_manager=DefaultBudgetManager(spec, clock), estimator=est, clock=clock)
state = orch.run(wf, reposets, agents)
ASSERT sleeps == [60.0]            # slept exactly to window reset
ASSERT state.status == "succeeded"
ASSERT state.budget_counters.consumed_tokens == expected_actuals_sum
```

## Schemas / Interface Notes
- Interface / API: tests only; extends `FakeExecutor`, reuses `conftest.py` fixtures.
- Spec / data schema: adds `specs/examples/workflow-budget.json` (full budget block) for schema round-trip + integration.
- Triggers / events: asserts on `budget.*` log records via `read_jsonl`.
- Artifacts: test fixtures under `tests/fixtures/` (`claude_usage.json`).

## Handoff Boundary
- Upstream: implemented features.
- Downstream: docs-refresh consumes the verified behavior + coverage numbers.
