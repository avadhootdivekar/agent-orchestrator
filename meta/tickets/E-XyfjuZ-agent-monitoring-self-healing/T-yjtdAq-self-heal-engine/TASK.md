# TASK: T-yjtdAq-self-heal-engine

## Metadata
- Task ID: `T-yjtdAq-self-heal-engine`
- Epic ID: `E-XyfjuZ-agent-monitoring-self-healing`
- Owner: dev-epic agent (self-implemented)
- Created: 2026-07-14
- Last Updated: 2026-07-14
- Status: Draft
- Estimate: 1 day

## Requirements Mapping
- Requirement IDs: FR-3, FR-6 (heal side)

## Description
Wire Consult Point B into `engine.py`, per Design Decision D4/D5 in the epic doc: placed
immediately after the existing budget-reconcile/429 block (~L687) and BEFORE
`ts.attempts = result.attempts` / `ts.ended_at = ...` (~L689-690), scoped strictly to
`result.status == "failed"` (not `timed_out`/`cancelled`) and gated by `self._self_heal_enabled`
(opt-in, default False).

New `Orchestrator.__init__` params: `self_heal_enabled: bool = False`,
`max_heal_retries_per_task: int = 1` (shares `monitor`/`max_monitor_calls_per_run` with
`T-TdildW`).

New `RunState` field: `monitor_heal_retries: dict[str, int] = {}` (per task id).

New private engine method `_consult_task_failure_heal(tid, task, result, state, run_log) ->
HealVerdict | None` (returns `None` when bound/cap exhausted — caller treats `None` and
`decision == "accept_failure"` identically: fall through to the existing, unmodified
settle/outcome-handling code). Builds the compact failure summary using ONLY existing
`TaskResult` fields (`task_id`, `attempts`, `error` reused as `terminal_reason`/`errors`,
`exit_code`) plus one bounded, guarded `stderr.txt` tail read (≤2000 chars) via
`monitoring._read_stderr_tail(result.output_artifact_path)` — never full transcript/artifact
content (NFR-7).

Retry mechanics mirror the existing quota-exhaustion requeue exactly: `self._sleeper(wait_seconds)`
→ cancel check → `state.monitor_heal_retries[tid] += 1` → `ts.status = "pending"` → `cursor -= 1`
→ `self._runstate.save(state)` → `continue` (skips the rest of the loop body for this iteration:
no `ts.ended_at`/`ts.attempts` mutation, no `evaluate_breakers` call, no dynamic-expansion hooks —
the failed attempt is invisible to everything downstream of this hook, exactly as if it had not
yet been dispatched). Heal retries do NOT touch `RetryPolicy.max_attempts` accounting (a
completely separate counter).

## Acceptance Criteria
1. `self_heal_enabled=False` (default): a task that fails after exhausting `RetryPolicy` behaves
   BYTE-IDENTICALLY to today (run fails) — proven by re-running the existing engine/retry test
   suites unmodified, green.
2. `self_heal_enabled=True`, `RuleBasedMonitor`, a `FakeExecutor` behavior producing a
   transient-pattern-matching error then succeeding on the extra attempt → the run SUCCEEDS;
   `state.monitor_heal_retries[tid] == 1`; the task's final `TaskRunState` shows the SUCCEEDING
   attempt's data (no residual "failed" artifacts from the healed attempt).
3. Same setup but the task fails again on the healed retry → falls through to the existing
   failure/run-fail path exactly as if self-heal were off (bound exhausted after 1 use, matching
   `max_heal_retries_per_task` default).
4. `self_heal_enabled=True` but a NON-transient error → `accept_failure` on the FIRST consult (no
   retry attempted, but the consult DID happen — `state.monitor_calls_made` incremented and a
   `monitor_decisions` entry recorded) → run fails exactly as today.
5. Resume: a persisted `monitor_heal_retries[tid]` count (already at the bound before the run
   stopped for an unrelated reason) is NOT reset by `prepare_resume` — the task gets zero fresh
   heal budget on resume.
6. `ts.started_at` is never reset by the heal-retry requeue (respects the existing E-3JTmVu
   single-writer guard — verify by asserting `started_at` is unchanged across a heal-retry cycle
   in a test using a controlled clock).
7. `_read_stderr_tail` failures (missing file, permission error, etc.) never crash the consult —
   summary falls back to an empty stderr_tail.

## Risks
- Medium: getting the exact insertion point right (before vs. after the budget-reconcile block)
  matters for correctness of budget accounting on a healed retry — mitigated by placing the hook
  AFTER the existing budget reconcile (so the failed attempt's actual/estimated tokens are still
  correctly reconciled before the requeue) but BEFORE any `TaskRunState` settle-time mutation.

## Dependencies
- `T-h2XLxe-monitor-abstraction` (needs `Monitor`/`RuleBasedMonitor`/`TaskFailureSummary`/
  `HealVerdict`/`_read_stderr_tail`).
- Independent of `T-TdildW` (different hook site, different RunState fields) — safe to implement
  in parallel, but both touch `Orchestrator.__init__`'s signature so should be merged carefully
  (sequenced after `T-TdildW` in practice to avoid a merge conflict on the constructor).

## Pseudocode / Algorithm
```text
# ---- existing budget reconcile / 429 route block (unchanged) ----
...
# ---- NEW: Consult Point B ----
if self._self_heal_enabled and result.status == "failed":
    heal_verdict = self._consult_task_failure_heal(tid, task, result, state, run_log)
    if heal_verdict is not None and heal_verdict.decision == "retry":
        self._sleeper(heal_verdict.wait_seconds)
        if self._cancel_fn():
            state.status = "cancelled"; failed = True; self._runstate.save(state); break
        state.monitor_heal_retries[tid] = state.monitor_heal_retries.get(tid, 0) + 1
        ts = state.tasks.setdefault(tid, TaskRunState())
        ts.status = "pending"
        cursor -= 1
        self._runstate.save(state)
        continue
    # else: bound/cap exhausted (None) or explicit accept_failure -> fall through unchanged

ts.attempts = result.attempts       # existing code, unchanged
ts.ended_at = datetime.now(UTC).isoformat()   # existing code, unchanged
...
```

## Schemas / Interface Notes
- Interface / API: `Orchestrator.__init__` new kwargs; `Orchestrator._consult_task_failure_heal`
  (private); `monitoring._read_stderr_tail` (used here).
- Spec / data schema (JSON/YAML): none new.
- Triggers / events (cron/event): N/A.
- Artifacts (inputs/outputs by path): `RunState.monitor_heal_retries` persisted in `state.json`.

## Handoff Boundary
- Upstream: `T-h2XLxe-monitor-abstraction`.
- Downstream: `T-QyNnf5` wires `self_heal_enabled`/`max_heal_retries_per_task` from config/CLI;
  `T-Wx8vUq` covers the full acceptance-criteria test matrix.

## Artifacts
- Docs/comments: `meta/tickets/E-XyfjuZ-agent-monitoring-self-healing/T-yjtdAq-self-heal-engine/`
- Large outputs: none.
