# TASK: T-Pl3Rx7-parallel-bench-runner

## Metadata
- Task ID: `T-Pl3Rx7-parallel-bench-runner`
- Epic ID: `E-Bt4Xk9-complex-benchmark-tiers`
- Owner: developer agent
- Created: 2026-07-22
- Last Updated: 2026-07-22
- Status: Draft
- Estimate: 2.5 days

## Requirements Mapping
- FR-3 (bounded task-level parallelism; budget correct under concurrency; deterministic persisted output)

## Description
Add opt-in **task-level parallelism** to `run_suite` via a bounded `ThreadPoolExecutor`. `--max-parallel N` (default 1 = byte-identical serial behavior). Tasks are independent (no DAG — do NOT reuse the core engine's ADR-0007 wave/barrier scheduler). Per-task workspaces are already disjoint, so the only shared mutable state is `tasks_dict`, the running-cost total (from T-Bg2Wq4), and the atomic `run.json` persist — guard all three with one lock, and perform the budget check under that lock so it stays correct under concurrency. Persisted `run.json` task order stays id-sorted (deterministic) even though completion order is not.

## File ownership (exclusive)
- `src/agent_orchestrator/bench/runner.py` — thread-pool execution path + lock. (Sequenced AFTER T-Bg2Wq4; same file.)
- `src/agent_orchestrator/bench/cli.py` — add `--max-parallel` to `run`. (Sequenced after T-Bg2Wq4; before T-Cm9Tb4.)
- (read-only) `bench/tiers.py` (`default_max_parallel`), `bench/workspace.py` (disjoint per-task dirs — no edit).

## Inputs / Outputs
- Inputs: `max_parallel` (CLI) or tier default; the suite's tasks.
- Outputs: identical `run.json` shape as serial; faster wall-clock; correct budget accounting.

## Acceptance Criteria
1. **Given** `--max-parallel 1` (default) **When** run **Then** behavior is byte-identical to today's serial runner (same events, same `run.json`; existing runner tests pass unedited).
2. **Given** `--max-parallel 4` over an 8-task fake suite **When** run **Then** all 8 tasks are recorded exactly once, `run.json` `tasks` is id-sorted, and no task record is lost (no last-write-wins corruption) — asserted over repeated runs.
3. **Given** a fake subject with injected per-task costs and `--cost-budget-usd C` under `--max-parallel 4` **When** run **Then** cumulative recorded cost stops scheduling new tasks at the cap; overshoot is bounded to ≤ `max_parallel` in-flight tasks (documented); the invariant `sum(non_skipped cost) is within [cap, cap + max_parallel*max_task_cost)` holds.
4. Persisted `run.json` after every completed task is always valid JSON (atomic rename under the lock); an interrupted parallel run leaves a resumable file.
5. `max_parallel` is **excluded** from `config_fingerprint` (control-flow, not per-task config) — asserted.
6. Determinism caveat documented: *which* tasks receive `skipped_budget` near the cap may vary run-to-run under parallelism (scores already stochastic, A5); the set of *completed* task results does not depend on worker count for a non-budget-capped run.

## Risks
- Threads vs processes: tasks are subprocess/IO-bound (`claude`/`ao`/Docker spawn — GIL released on wait), so a `ThreadPoolExecutor` is correct and keeps shared state trivially in-process. Do NOT use processes (pickling/logging-handler complications).
- Lock scope: hold the lock only around shared-state mutation + persist + budget check — NOT around the (long) subject.run/grade, or parallelism is defeated. Materialize+run+grade happen outside the lock; only the read-budget/dispatch-decision and the write-back are inside it.
- The per-run log handler (`attach_run_handler`) and `get_run_logger(..., task_id=...)` must be thread-safe for concurrent task logging (verify; the stdlib logging module is thread-safe, but confirm the run-handler attach/detach happens once around the pool, not per task).

## Pseudocode / Algorithm
```text
from concurrent.futures import ThreadPoolExecutor
lock = threading.Lock()
def _dispatch_or_skip(task) -> bool:          # returns True if it should run
    with lock:
        if already_recorded_non_skipped(task) and not force: return False
        if cost_budget_usd is not None and running_cost >= cost_budget_usd:
            record_skipped_budget(task); persist(); return False
        return True
def _run_one(task):
    if not _dispatch_or_skip(task): return
    ctx = materialize_workspace(task); sr = subject.run(task, ctx); gr = grade(task, ctx)   # OUTSIDE lock
    with lock:
        tasks_dict[task.id] = build_metric(sr, gr)
        running_cost += sr.cost_usd or 0
        persist(_build_record())              # id-sorted, atomic rename
max_parallel = param or tiers.load_tier_config(suite.tier).default_max_parallel
if max_parallel <= 1:
    for t in id_sorted(tasks): _run_one(t)                 # exact serial path preserved
else:
    with ThreadPoolExecutor(max_workers=max_parallel) as ex:
        list(ex.map(_run_one, id_sorted(tasks)))
```
Note: to keep the budget floor tight under concurrency, `_dispatch_or_skip` re-reads `running_cost` under the lock at dispatch time; because in-flight tasks have not yet added their cost, overshoot is bounded by the number of concurrently in-flight tasks (≤ max_parallel).

## Schemas / Interface Notes
- `run_suite(..., max_parallel: int | None = None)`; CLI `ao-bench run ... [--max-parallel N]`.
- Triggers/events: `bench.run.start` gains `max_parallel`. Artifacts: unchanged `run.json`.

## Handoff Boundary
- Upstream: T-Bg2Wq4 (serial budget logic to make concurrency-correct).
- Downstream: T-Cm9Tb4 (campaign uses `run_suite` per subject with `max_parallel`), T-Sg6Jf2 (SweBenchGrader adds its OWN Docker-eval lock so agent runs parallelize but grading serializes — this task provides the task-level pool, not the grading lock).

## Artifacts
- Docs/comments: this folder. Large outputs: none.
