"""Local outcome/accuracy metrics AND post-run settlement grading (E-1cecSx B3.1/B3.2/B3.3,
`docs-md/cost-caching-optimization-hld.md` §3.1/§3.3, `docs-md/adr/
ADR-0015-prompt-cache-scope-and-post-run-grading.md` decision 2).

Two independent pieces, both pure/read-only over already-persisted state:

- **Local counts (B3.1)**: `task_outcome_summary`/`run_breakdown_frequency` -- the same
  "derive, don't duplicate persisted bookkeeping" convention `models.py::
  compute_run_usage_totals`/`compute_run_active_seconds` already use. Nothing here writes back
  to `RunState`; every value is recomputed on demand, so it is always resume-safe and correct
  mid-run, after a resume, or long after the run ended.
- **Post-run settlement grading (B3.2/B3.3)**: `grade_run` -- called by the `ao report outcomes
  --grade <hook-name>` CLI command, AFTER a run has completed. Resolves `<hook-name>` against
  `WorkflowSpec.hooks` (Epic A's existing registry, reused unchanged -- no new spec surface) and
  calls `hooks.run_hook` (also reused unchanged, `kind="settlement_hook"`) once per task in
  `RunState.tasks`, uniformly, whether that task was freshly dispatched or skipped via
  `skip_if_outputs_exist` -- covering both by construction, with zero `engine.py` involvement
  (ADR-0015 decision 2 -- an earlier design that fired this from inside `engine.py`'s dispatch/
  settle functions was rejected at early-gate review for a chain of correctness/performance
  defects; see the ADR for the full record). Results are returned as `SettlementGrade` rows and
  written to a report artifact by the CLI layer -- NEVER into `RunState`/`TaskRunState`, so
  there is no new resume/migration surface and no `on_failure`/gating semantic to misuse (there
  is no `on_failure` concept on this path at all).
"""

from __future__ import annotations

import os
from typing import Literal

from pydantic import BaseModel

from .artifacts import ArtifactStore
from .hooks import run_hook
from .models import HookOutcome, RunState, TaskRunState, TaskSpec, WorkflowSpec

# Deliberately NOT reusing `models.py::count_monitor_heal_retries` here (early-gate architect
# suggestion 5b, considered and NOT taken -- a disclosed, reasoned divergence, not an oversight):
# that function counts only `decision == "retry"` records, because its own caller
# (`engine.py`'s Consult Point B) needs "how many times has self-heal already retried this
# task" to compare against `max_heal_retries_per_task`. `self_heal_retry_count` below answers a
# different question -- "how many review-loop iterations did this task go through" -- which
# should count EVERY `task_failure` consult regardless of its decision (`retry` OR
# `accept_failure`); an `accept_failure` consult is still a review-loop iteration that happened,
# and silently excluding it would undercount for exactly the tasks an operator most wants
# visibility into (the ones self-heal gave up on). Reusing the narrower function would produce
# a wrong number under a different name, which is worse than the small, honestly-scoped filter
# below.


class TaskOutcomeSummary(BaseModel):
    """One task's retry/review-loop/breakdown-frequency counts for one run.

    Every field is derived from fields that already exist on `RunState`/`TaskRunState`/
    `TaskIntegrationState` pre-epic (E-9h3m7k, E-Wk9Tz3, E-XyfjuZ) -- this is a read-only
    aggregation, not a new capture mechanism.
    """

    task_id: str
    status: str
    # Last dispatch cycle's own in-call retry count (TaskRunState.attempts).
    attempts: int
    # R-21: monotonic across every self-heal/T2/T3/quota requeue this run
    # (TaskRunState.dispatch_cycle) -- "how many times was this task (re)dispatched at all".
    dispatch_cycle: int
    # T2 conflict-resolver dispatch count (TaskIntegrationState.resolver_attempts). 0 for a
    # task that was never isolated or never conflicted.
    resolver_attempts: int
    # T3 rerun-on-fresh-base count (TaskIntegrationState.reruns). 0 for the same reasons.
    reruns: int
    # Count of `RunState.monitor_decisions` entries with `consult_point == "task_failure"`
    # and `subject_id == task_id` -- the "review-loop" (self-heal) count this metric set
    # asks for. Mirrors `models.py::count_monitor_heal_retries`'s own filter, without needing
    # a task id passed in ahead of time (this builds the whole per-task table in one pass).
    self_heal_retry_count: int


def task_outcome_summary(state: RunState) -> list[TaskOutcomeSummary]:
    """One `TaskOutcomeSummary` per task in *state*, sorted by task id for determinism.

    O(tasks + monitor_decisions) -- a single pass to build the self-heal-count index, then
    one pass over `state.tasks`. Safe to call at any point (mid-run, on resume, or after
    completion); a task with no integration bookkeeping (`state.task_integration` has no
    entry for it -- never isolated) reports `resolver_attempts=reruns=0`, not an error.
    """
    heal_counts: dict[str, int] = {}
    for decision in state.monitor_decisions:
        if decision.consult_point == "task_failure":
            heal_counts[decision.subject_id] = heal_counts.get(decision.subject_id, 0) + 1

    summaries: list[TaskOutcomeSummary] = []
    for task_id, ts in state.tasks.items():
        ti = state.task_integration.get(task_id)
        summaries.append(
            TaskOutcomeSummary(
                task_id=task_id,
                status=ts.status,
                attempts=ts.attempts,
                dispatch_cycle=ts.dispatch_cycle,
                resolver_attempts=ti.resolver_attempts if ti is not None else 0,
                reruns=ti.reruns if ti is not None else 0,
                self_heal_retry_count=heal_counts.get(task_id, 0),
            )
        )
    return sorted(summaries, key=lambda s: s.task_id)


def run_breakdown_frequency(state: RunState) -> dict[str, int]:
    """Run-wide breaker-trip frequency by `condition` (`RunState.tripped_breakers`, FR-CB4).

    E.g. `{"consecutive_failures": 2, "provider_429": 1}` -- how often each breaker
    *condition* fired this run, the run-wide "breakdown frequency" half of B3.1 (task-level
    counts are `task_outcome_summary`'s job; this is the run-wide complement).
    """
    freq: dict[str, int] = {}
    for tripped in state.tripped_breakers:
        freq[tripped.condition] = freq.get(tripped.condition, 0) + 1
    return freq


# ---------------------------------------------------------------------------
# Post-run settlement grading (B3.2/B3.3, ADR-0015 decision 2) -- see module docstring.
# ---------------------------------------------------------------------------

# Task statuses meaningful to grade: every TERMINAL status a completed run can leave a task in.
# "pending"/"running" (a run that didn't actually finish) and "not_taken" (a routing-unselected
# task -- nothing ran, nothing to grade) are deliberately excluded.
_GRADEABLE_STATUSES = frozenset({"succeeded", "failed", "skipped", "cancelled", "timed_out"})

SettleReason = Literal["dispatched", "skipped"]

# Capture-dir/context.json shape locked in per the early-gate architect's required findings
# (B-6): a flat, RUN-scoped directory (not cycle-nested like pre_hook/post_hook's own capture
# layout, since settlement grading is per-RUN, not per-dispatch-cycle) that is overwritten on
# each `ao report outcomes --grade` invocation -- re-grading is an explicit, operator-invoked
# CLI action, not an implicit engine re-fire, so "last grade wins" on disk is the right, simple
# semantic (no per-cycle history to preserve here, unlike a hook that can fire mid-run).
_SETTLEMENT_HOOK_CAPTURE_DIRNAME = "settlement_hook"
_RUN_DIR_SEGMENTS = (".orchestrator", "runs")  # mirrors engine.py's own _task_run_dir convention
_SETTLEMENT_GRADES_FILENAME = "settlement_grades.json"


class SettlementGrade(BaseModel):
    """One task's post-run settlement grade -- the CLI's own report row, never written into
    `RunState`/`TaskRunState` (deliberate, ADR-0015 decision 2: no new resume/migration
    surface)."""

    task_id: str
    settle_reason: SettleReason
    outcome: HookOutcome


def _settle_reason(ts: TaskRunState) -> SettleReason:
    return "skipped" if ts.status == "skipped" else "dispatched"


def _settlement_context_fields(
    task: TaskSpec, ts: TaskRunState, store: ArtifactStore, run_id: str, settle_reason: SettleReason
) -> dict:
    """Build `context.json`'s field set for a settlement-hook grade -- shaped like `engine.py`'s
    own `_hook_context_fields` (same field NAMES for `instruction_path`/`input_paths`/
    `output_paths` so a grading script written against `post_hook`'s context also reads a
    `settlement_hook` context without changes), plus the `settle_reason` discriminator a
    `post_hook` context never needed (architect finding B-6: a grading script must be able to
    tell whether the task actually ran).

    Deliberately narrower than `_hook_context_fields`: `general_instruction_paths`/
    `dynamic_input_paths`/`repo_paths` are NOT resolved here (they require the `reposets`
    registry / dynamic-injection bookkeeping this post-run, workspace-only pass does not load)
    -- omitted rather than faked. `output_dir` points at this task's own run-scoped capture
    directory (see `_SETTLEMENT_HOOK_CAPTURE_DIRNAME`), not a per-cycle `attempt-N` directory --
    there may not have been a dispatch cycle at all (the skipped case).
    """
    fields: dict = {
        "run_id": run_id,
        "task_id": task.id,
        "hook_kind": "settlement_hook",
        "settle_reason": settle_reason,
        "instruction_path": store.resolve(task.instruction),
        "input_paths": [store.resolve(p) for p in task.inputs],
        "output_paths": [store.resolve(p) for p in task.outputs],
        "status": ts.status,
    }
    if ts.output_artifact_path:
        fields["output_artifact_path"] = ts.output_artifact_path
    return fields


def grade_run(
    state: RunState,
    workflow: WorkflowSpec,
    hook_name: str,
    store: ArtifactStore,
    workspace_root: str,
) -> list[SettlementGrade]:
    """Grade every gradeable task in *state* with the named `WorkflowSpec.hooks` entry.

    Called by `ao report outcomes --grade <hook-name>`, strictly AFTER `run()` has returned (the
    shared checkout has already gone through run-end sync by then, so a deterministic grader
    reading the working tree sees a correct, not-stale, tree -- the exact defect ADR-0015
    decision 2 exists to avoid). Iterates `state.tasks` in sorted order (determinism); a task
    whose status is not in `_GRADEABLE_STATUSES` (still pending/running, or `not_taken`) is
    skipped from the report entirely, not graded as a failure.

    `hook_name` is resolved against `workflow.hooks` with `.get()` -- never a bare subscript
    (architect finding B-3: an unguarded `workflow.hooks[name]` lookup is exactly the kind of
    crash this post-run pass must not reproduce, even though it runs from the CLI, not the
    engine's dispatch loop, and so cannot kill a live run either way). An unknown `hook_name`
    raises `KeyError` ONCE, at the top, before grading any task -- a normal, immediate CLI
    argument error, not a per-task surprise.

    `cwd` for every graded task is *workspace_root* (never a per-task worktree): worktrees are
    already released by the time a run completes (`IntegrationSpec.keep_worktrees` default
    `"on_failure"`), and the shared checkout is the one guaranteed-correct, guaranteed-present
    tree at this point (architect finding B-2).
    """
    if hook_name not in workflow.hooks:
        raise KeyError(
            f"unknown hook {hook_name!r} (not declared in this workflow's `hooks` registry)"
        )
    hook = workflow.hooks[hook_name]

    grades: list[SettlementGrade] = []
    for task_id in sorted(state.tasks):
        ts = state.tasks[task_id]
        if ts.status not in _GRADEABLE_STATUSES:
            continue
        task = workflow.task(task_id)
        settle_reason = _settle_reason(ts)
        capture_dir = store.resolve(
            os.path.join(
                *_RUN_DIR_SEGMENTS, state.run_id, task_id, _SETTLEMENT_HOOK_CAPTURE_DIRNAME
            )
        )
        outcome = run_hook(
            hook,
            kind="settlement_hook",
            hook_name=hook_name,
            run_id=state.run_id,
            task_id=task_id,
            cycle=ts.dispatch_cycle,
            capture_dir=capture_dir,
            context_fields=_settlement_context_fields(task, ts, store, state.run_id, settle_reason),
            env_overlay={},
            cwd=workspace_root,
            artifact_store=store,
        )
        grades.append(
            SettlementGrade(task_id=task_id, settle_reason=settle_reason, outcome=outcome)
        )
    return grades


def settlement_grades_path(store: ArtifactStore, run_id: str) -> str:
    """Where `ao report outcomes --grade` writes its report artifact for *run_id*.

    Overwritten on each invocation (§ `grade_run`'s own docstring) -- re-running the CLI command
    is the explicit, operator-controlled way to re-grade; there is no implicit engine trigger to
    make idempotent-across-cycles guarantees about.
    """
    return store.resolve(os.path.join(*_RUN_DIR_SEGMENTS, run_id, _SETTLEMENT_GRADES_FILENAME))
