# TASK: T-TNleFt-tests-parallel-matrix

## Metadata
- Task ID: `T-TNleFt-tests-parallel-matrix`
- Epic ID: `E-IasNXu-parallel-execution`
- Owner: tester agent
- Created: 2026-07-15
- Last Updated: 2026-07-15
- Status: Draft
- Estimate: 2.5 days

## Requirements Mapping
- NFR-1, NFR-2 (verification of); cross-cutting coverage of FR-3..FR-8

## Description
Own the cross-cutting test assets for parallel execution: (1) a deterministic **gated executor** test
harness, (2) the **`N=1` byte-identical regression gate**, (3) the **deterministic `N>1` parallelism
proof**, (4) the concurrency **interaction integration matrix**, and (5) **CliRunner e2e** for the
`max_parallel` surface. Per-unit tests for individual helpers live with `T-j8YLGd`/`T-VSfAUN`; this
task provides the shared harness and the end-to-end/gate coverage they build on.

### Sub-deliverables
1. **Gated executor** (`tests/support/gated_executor.py` or a fixture in `tests/conftest.py`): an
   `Executor` that, on `execute(ctx)`, records the dispatch (task id + timestamp/sequence), then blocks
   on a per-task `threading.Event` (or a shared barrier) until the test releases it in a chosen order;
   returns a configurable `TaskResult` (success/fail/timeout/429/quota, token fields). It is
   **thread-safe** (guard the record structures with a `Lock`) — unlike `FakeExecutor`, whose mutable
   counters (`_gate_invocations`, `_rate_limited_once`, `_quota_exhausted_remaining`) are not safe to
   share under `N>1` (ADR-0007 consequences). Provide helpers: `wait_until_in_flight(n)`, `release(tid)`,
   `dispatched_overlap()`.
2. **N=1 baseline capture + gate**: capture the final `RunState` + ordered `run.log` events for a
   representative workflow (diamond + emit_tasks + loop + router + budget) on the current serial engine
   (baseline fixture), then assert `max_parallel=1` reproduces it exactly. Also assert the whole
   pre-existing engine suite passes unedited at default N (this is a CI expectation, documented here).
3. **N>1 parallelism proof**: with `max_parallel>=2` and the gated executor, assert ≥2 independent
   tasks are in flight simultaneously (`wait_until_in_flight(2)` succeeds before any release) and that
   two different completion orders yield the same final `RunState`.
4. **Interaction integration matrix** (gated executor, `N>1`): barrier isolation (emit_tasks / loop /
   router never overlap a sibling); budget cap blocks the (N+1)th while N run; budget-wait no-deadlock;
   quota/429/self-heal requeue with siblings in flight; breaker halt mid-wave drains in-flight; cancel
   mid-wave drains + resumable via `ao resume`. (These mirror `T-VSfAUN`'s ACs at the run level.)
5. **CliRunner e2e** (`tests/test_e2e_cli.py` style, via `CliRunner` — learning #12): `--max-parallel
   4`, `AO_MAX_PARALLEL=4`, and config `max_parallel: 4` each take effect (assert observable
   concurrency or at least successful run + resolved value); negative `--max-parallel` (e.g. `-1`)
   exits 1 with "must be >= 1"; `--max-parallel 0` exits 0 (falls through to default, serial);
   precedence CLI > env > config verified through the CLI boundary. Reuse the `ao resume` CLI pattern
   (learning #14: first failing run via Python API to get `run_id`, then `CliRunner ao resume
   --run-id`).

## Acceptance Criteria
1. Gated executor exists, is thread-safe, and supports: recording dispatch order, blocking until
   released, releasing in a test-chosen order, and asserting simultaneous in-flight count. Unit test of
   the harness itself proves `wait_until_in_flight(2)` observes overlap.
2. **N=1 gate passes**: the representative workflow at `max_parallel=1` yields a `RunState` and
   `run.log` event sequence byte-identical to the captured serial baseline; and `uv run pytest`
   (entire suite) is green with the pre-existing engine tests unedited.
3. **N>1 proof passes**: ≥2 tasks demonstrably concurrent; final `RunState` invariant under two forced
   completion orders (e.g. release A-before-B vs B-before-A → identical statuses + budget counters).
4. Barrier isolation test: for each of emit_tasks, loop-gate, and router barriers, the gated executor
   records zero overlap between the barrier and any sibling at `max_parallel=4`.
5. Budget-under-concurrency tests pass: (N+1)th task gated while N run; no double-charge; no deadlock
   on window roll (fixed clock + sleeper).
6. Requeue/halt/cancel integration tests pass: quota, 429, self-heal each requeue with a sibling
   draining; breaker halt drains in-flight and finalizes `failed`; cancel drains and `ao resume`
   completes the remainder.
7. CliRunner e2e: three precedence sources each verified through the CLI; negative `--max-parallel`
   → exit 1; `--max-parallel 0` → exit 0 (default serial); an `ao run --max-parallel N` on a small
   real-ish (fake-executor-backed) workflow exits 0.
8. `ruff`/`mypy src` clean for new test support code (note: test files may carry pre-existing mypy
   errors per learning #11 — keep new support modules clean and run `mypy src`).

## Risks
- Gated-executor tests can flake if they rely on timing instead of explicit latches — use
  `Event`/`Barrier` + bounded `wait(timeout=...)` with a clear failure message, never `sleep`-based
  races. Deterministic release order is mandatory (ADR-0007 D6).
- Do NOT reuse a single `FakeExecutor` instance across concurrent tasks — its counters are not
  thread-safe. Use the gated executor (or a fresh per-task fake) for `N>1`.
- The `N=1` baseline must be captured from the pre-change engine (or reconstructed from current tests)
  — coordinate with `T-j8YLGd` so the baseline predates the scheduler rewrite.

## Dependencies
- `T-j8YLGd` (scheduler), `T-VSfAUN` (semantics). Uses existing fixtures/patterns:
  `tests/conftest.py`, `tests/test_engine*.py`, `tests/test_e2e_cli.py`, `tests/test_resume_replay.py`,
  `tests/test_budget_integration.py`, `tests/test_engine_breakers.py`, `tests/test_monitoring_self_heal.py`.

## Pseudocode / Algorithm
```text
class GatedExecutor(Executor):
    def __init__(self, results: dict[str, TaskResult]):
        self._results = results; self._events = defaultdict(threading.Event)
        self._in_flight = set(); self._order = []; self._lock = threading.Lock()
    def execute(self, ctx):
        with self._lock: self._in_flight.add(ctx.task_id); self._order.append(ctx.task_id)
        self._events[ctx.task_id].wait(timeout=10)          # block until released
        with self._lock: self._in_flight.discard(ctx.task_id)
        return self._results.get(ctx.task_id, TaskResult(task_id=ctx.task_id, status="succeeded"))
    def wait_until_in_flight(self, n, timeout=5): spin-with-timeout on len(self._in_flight) >= n
    def release(self, tid): self._events[tid].set()

# N>1 proof
ex = GatedExecutor({...}); orch = Orchestrator(ex, ..., max_parallel=2)
t = Thread(target=orch.run, args=(...)); t.start()
assert ex.wait_until_in_flight(2)          # two tasks concurrently in flight
ex.release("a"); ex.release("b"); t.join()
assert final_state ==  order_independent_expected
```

## Schemas / Interface Notes
- Interface / API: internal test support only (`GatedExecutor`, fixtures). No product interface change.
- Artifacts: test fixtures for the N=1 baseline (checked into `tests/fixtures/`), not root `output/`.

## Handoff Boundary
- Upstream: `T-j8YLGd`, `T-VSfAUN`.
- Downstream: `T-EJKD6f` cites the passing matrix as evidence in the design-doc reconciliation.

## Artifacts
- Docs/comments: `meta/tickets/E-IasNXu-parallel-execution/T-TNleFt-tests-parallel-matrix/`
- Large outputs: none.
