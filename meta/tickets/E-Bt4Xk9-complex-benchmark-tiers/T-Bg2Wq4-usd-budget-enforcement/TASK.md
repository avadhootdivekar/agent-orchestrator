# TASK: T-Bg2Wq4-usd-budget-enforcement

## Metadata
- Task ID: `T-Bg2Wq4-usd-budget-enforcement`
- Epic ID: `E-Bt4Xk9-complex-benchmark-tiers`
- Owner: developer agent
- Created: 2026-07-22
- Last Updated: 2026-07-22
- Status: Draft
- Estimate: 2.5 days

## Requirements Mapping
- FR-2 (per-`ao-bench run` USD cost cap; `skipped_budget` status; resume-aware; fingerprint exclusion)

## Description
Add a hard **USD cost budget** to `run_suite` (one suite × one subject), distinct from the pre-existing token `budget_total`. Before scheduling each task, if the cumulative recorded `cost_usd` for this run has reached the cap, stop scheduling and record the remaining tasks with a new `subject_status = "skipped_budget"` (solved=false, score=0, cost=0). Wire the default cap from the suite's tier (`cost_budget_usd_per_subject` via `bench/tiers.py`) with a `--cost-budget-usd` CLI override. Make resume budget-aware. Keep this **serial** — T-Pl3Rx7 makes the same logic concurrency-correct next.

## File ownership (exclusive)
- `src/agent_orchestrator/bench/runner.py` — budget accounting in the task loop; `skipped_budget` record; resume rule; fingerprint exclusion. (Sequenced BEFORE T-Pl3Rx7, which also edits runner.py.)
- `src/agent_orchestrator/bench/subjects.py` — add `"skipped_budget"` to `SubjectResult.status` `Literal` + a `_budget_skipped_result()` factory. (Parallel-safe: T-Wp4Nz5/T-Sg6Jf2 do not touch subjects.py.)
- `src/agent_orchestrator/bench/cli.py` — add `--cost-budget-usd` to `run`; exclude `skipped_budget` from `_HARNESS_FAILURE_STATUSES`. (Sequenced before T-Pl3Rx7/T-Cm9Tb4 cli edits.)
- (read-only) `bench/tiers.py` (from T-Tr1Km8), `bench/metrics.py` (`subject_status: str` already accepts the new value — no edit).

## Inputs / Outputs
- Inputs: `cost_budget_usd` (CLI) or tier default; per-task `SubjectResult.cost_usd`.
- Outputs: `run.json` where tasks past the cap carry `subject_status="skipped_budget"`; a clean (exit 0) run.

## Acceptance Criteria
1. **Given** a fake subject scripted to report $2/task and `--cost-budget-usd 5` over a 6-task suite **When** run **Then** exactly the first tasks whose cumulative cost < $5 run for real, the crossing task completes (bounded 1-task overshoot), and all later tasks are recorded `subject_status="skipped_budget"`, solved=false, cost=0.
2. `skipped_budget` **never** causes a non-zero CLI exit (it is not in `_HARNESS_FAILURE_STATUSES`); a run that only budget-skips exits 0.
3. **Given** a prior `run.json` with some `skipped_budget` tasks **When** re-run with a higher `--cost-budget-usd` (no `--force`) **Then** the previously-skipped tasks are re-attempted (not skipped as "already recorded"), and already-succeeded tasks are NOT re-run.
4. **Given** no `--cost-budget-usd` **When** run on a `tier: medium` suite **Then** the cap defaults to `tiers.json` `medium.cost_budget_usd_per_subject` ($50); on `tier: small` → $5.
5. `cost_budget_usd` is **excluded** from `config_fingerprint` (W4 guard): changing only the cap between two same-day runs does NOT trip the "config changed" error (unit-asserted: fingerprint identical for two caps).
6. Cumulative cost treats `cost_usd is None` (e.g. missing `state.json`) as $0 for the running total, and this is documented in the code.

## Risks
- Off-by-one on "check-before-schedule": must check BEFORE running a task, so the cap is a floor the run stops *above*, with at most one task of overshoot. Explicitly test the boundary.
- Resume rule must special-case ONLY `skipped_budget` as re-runnable; every other recorded status still skips (don't regress the existing resume contract).

## Pseudocode / Algorithm
```text
# subjects.py
status: Literal["succeeded","failed","timed_out","error","skipped_budget"]
FUNCTION _budget_skipped_result(ctx) -> SubjectResult:
    RETURN SubjectResult(status="skipped_budget", wall_clock_seconds=0.0, cost_usd=0.0,
                         capture_dir=ctx? or "", raw_error=None)   # no subprocess, no workspace needed

# runner.run_suite(...) additions
cost_budget_usd = param or tiers.load_tier_config(suite.tier).cost_budget_usd_per_subject
running_cost = sum(t.cost_usd or 0 for t in tasks_dict.values() if t.subject_status != "skipped_budget")
# resume: a recorded task is skipped ONLY IF (task.id in tasks_dict AND record.subject_status != "skipped_budget" AND not force)
FOR task IN id_sorted(tasks):
    IF already_recorded_non_skipped(task) and not force: continue
    IF cost_budget_usd is not None AND running_cost >= cost_budget_usd:
        record skipped_budget metric for task; persist; continue           # do NOT materialize/run
    ...materialize -> subject.run -> grade... (unchanged)
    tasks_dict[task.id] = metric
    running_cost += sr.cost_usd or 0
    persist
# overrides dict for _run_config_fingerprint: DO NOT add cost_budget_usd (control-flow, like force/task_filter)
```

## Schemas / Interface Notes
- `run_suite(..., cost_budget_usd: float | None = None)`; `SubjectResult.status` gains `"skipped_budget"`.
- CLI: `ao-bench run ... [--cost-budget-usd U]`.
- Triggers/events: extend `bench.task.*` log to include a `bench.task.skip_budget {task_id, running_cost, cap}` event.
- Artifacts: `run.json` (new status value; append-only compatible).

## Handoff Boundary
- Upstream: T-Tr1Km8 (tier config).
- Downstream: T-Pl3Rx7 (makes this concurrency-correct), T-Cm9Tb4 (whole-run campaign cap builds on the per-run cap + `total_cost_usd`). Leave the thread pool to T-Pl3Rx7 — implement serial here.

## Artifacts
- Docs/comments: this folder. Large outputs: none.
