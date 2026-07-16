# TASK: T-h2XLxe-monitor-abstraction

## Metadata
- Task ID: `T-h2XLxe-monitor-abstraction`
- Epic ID: `E-XyfjuZ-agent-monitoring-self-healing`
- Owner: dev-epic agent (self-implemented)
- Created: 2026-07-14
- Last Updated: 2026-07-14
- Status: Draft
- Estimate: 1.5 day

## Requirements Mapping
- Requirement IDs: FR-4, NFR-3, NFR-7

## Description
New `src/agent_orchestrator/monitoring.py`, mirroring the `Breaker`/`Executor`/`BudgetManager` DI
style: ABC + concrete implementations, no engine wiring yet (that's `T-TdildW`/`T-yjtdAq`). Not
wired into `engine.py`/`cli.py` in this task — pure module addition, independently unit-testable.

Contents:
- `BreakerTripSummary`, `BreakerVerdict`, `TaskFailureSummary`, `HealVerdict` (ephemeral DTOs,
  paths/ids/counts only per NFR-1/NFR-7 — never transcript or artifact payload content).
- `Monitor(ABC)` with `decide_breaker_trip(trip, *, run_id) -> BreakerVerdict` and
  `decide_task_failure(summary, *, run_id) -> HealVerdict`.
- `RuleBasedMonitor`: deterministic, zero-cost. Breaker policy: extend once (by the original
  threshold amount, i.e. `extend_by_seconds=None` → caller uses `extend_by_same=True`) when
  `trip.prior_extensions == 0`, else halt. Failure policy: regex-match a combined
  (terminal_reason + errors + stderr_tail) text blob against `DEFAULT_TRANSIENT_PATTERNS` (+ any
  configured extra patterns) — retry (with `wait_seconds`, default 30.0) only when a pattern
  matches AND `summary.prior_heal_retries == 0`, else accept_failure.
- `AgentMonitor`: looks up an `AgentSpec` + `Executor` + `ArtifactStore` (injected at
  construction), writes a baked-constant instruction file + the compact context JSON under
  `.orchestrator/runs/<run_id>/monitor/call-<n>-<subject>/`, invokes `executor.execute(...)` with
  a manually-built `TaskContext` (reusing the existing dataclass, no `TaskSpec`/DAG involvement),
  and reads back a verdict JSON via the existing bounded `artifacts.read_control` reader. ANY
  non-`succeeded` executor result, missing/oversized/malformed verdict file, or verdict failing
  strict shape validation → returns the SAFE DEFAULT verdict (halt / accept_failure) — never
  raises out of `decide_breaker_trip`/`decide_task_failure`.
- `DEFAULT_TRANSIENT_PATTERNS`: regex list covering connection reset/refused, timeout, 5xx,
  bad gateway/service unavailable, JSON decode errors, broken pipe.
- A small bounded stderr-tail helper (`_read_stderr_tail`, cap 2000 chars, guarded try/except,
  never raises) — the one new file read this epic introduces, scoped to this module (Design
  Decision D5 in the epic doc).

## Acceptance Criteria
1. `Monitor` is an ABC; `RuleBasedMonitor`/`AgentMonitor` both implement both methods.
2. `RuleBasedMonitor.decide_breaker_trip`: `prior_extensions=0` → `extend`; `prior_extensions>=1`
   → `halt`. Pure function of the input DTO (no internal mutable state).
3. `RuleBasedMonitor.decide_task_failure`: transient pattern + `prior_heal_retries=0` → `retry`
   (wait_seconds=30.0 default); non-transient text → `accept_failure`; transient but
   `prior_heal_retries>=1` → `accept_failure` (own-policy bound, belt-and-suspenders with the
   engine's `max_heal_retries_per_task`).
4. `AgentMonitor` round-trips through `FakeExecutor`: a configured valid verdict file is parsed
   correctly for both breaker-trip and task-failure shapes; an invalid/missing verdict, or a
   `FakeExecutor` behavior of `"fail"`/`"timeout"`, produces the safe default without raising.
5. `_read_stderr_tail` never raises on a missing file/directory; returns `""` in that case; caps
   at 2000 chars with an elision marker when the file is longer.
6. No import of `engine.py` from `monitoring.py` (one-directional dependency, avoids cycles).

## Risks
- Medium: the `AgentMonitor` plumbing (context/instruction/verdict file layout) is new and must
  be gotten right on the first pass since `T-TdildW`/`T-yjtdAq` build directly on it.

## Dependencies
- `T-mYMiPK-guardrail-mode-schema-model` (for `CircuitBreakerSpec.mode`, referenced by
  `BreakerTripSummary`'s originating spec — actually only `.id`/`.condition`/`.action`/`.detail`
  are needed from the spec, so this is a soft dependency; safe to implement in parallel if
  needed).

## Pseudocode / Algorithm
```text
class Monitor(ABC):
    name: str
    def decide_breaker_trip(trip: BreakerTripSummary, *, run_id: str) -> BreakerVerdict
    def decide_task_failure(summary: TaskFailureSummary, *, run_id: str) -> HealVerdict

RuleBasedMonitor.decide_breaker_trip:
    if trip.prior_extensions == 0: return BreakerVerdict("extend", extend_by_seconds=None, reason="first extension")
    return BreakerVerdict("halt", reason="already extended once")

RuleBasedMonitor.decide_task_failure:
    text = lower(summary.terminal_reason + " " + " ".join(summary.errors) + " " + summary.stderr_tail)
    if summary.prior_heal_retries == 0 and any(re.search(p, text) for p in patterns):
        return HealVerdict("retry", wait_seconds=self._wait_seconds, reason="transient pattern matched")
    return HealVerdict("accept_failure", reason="no transient pattern / already retried")

AgentMonitor.decide_breaker_trip(trip, run_id):
    call_dir = store.resolve(f".orchestrator/runs/{run_id}/monitor/call-{n}-{trip.breaker_id}")
    write context.json (trip.model_dump()), instruction.md (baked constant)
    ctx = TaskContext(run_id=run_id, task_id=f"monitor-breaker-{trip.breaker_id}-{n}", agent=self._agent,
                      instruction_path=<instruction.md>, input_paths=[<context.json>],
                      output_paths=[<verdict.json>], repo_paths={}, timeout_seconds=..., cwd=workspace_root,
                      output_dir=<call_dir>/capture)
    result = executor.execute(ctx)
    if result.status != "succeeded": return SAFE_DEFAULT_BREAKER_VERDICT
    try: verdict = read_control(store, verdict.json path); validate shape
    except (ControlFileError, KeyError, ValueError, TypeError): return SAFE_DEFAULT_BREAKER_VERDICT
    return BreakerVerdict(**verdict)
```

## Schemas / Interface Notes
- Interface / API: `Monitor` ABC (Python), reused nowhere else in this task (wiring is
  `T-TdildW`/`T-yjtdAq`).
- Spec / data schema (JSON/YAML): AgentMonitor's context/verdict JSON shapes (documented in
  module docstrings, not a JSON-schema file — these are engine-internal control files, not
  user-authored specs).
- Triggers / events (cron/event): N/A.
- Artifacts (inputs/outputs by path): `.orchestrator/runs/<run_id>/monitor/call-<n>-<subject>/{instruction.md,context.json,verdict.json,capture/}`.

## Handoff Boundary
- Upstream: `T-mYMiPK` (mode field, soft dependency).
- Downstream: `T-TdildW`/`T-yjtdAq` construct and call into `Monitor` implementations from
  `engine.py`; `T-QyNnf5` constructs `AgentMonitor`/`RuleBasedMonitor` from CLI config.

## Artifacts
- Docs/comments: `meta/tickets/E-XyfjuZ-agent-monitoring-self-healing/T-h2XLxe-monitor-abstraction/`
- Large outputs: none.
