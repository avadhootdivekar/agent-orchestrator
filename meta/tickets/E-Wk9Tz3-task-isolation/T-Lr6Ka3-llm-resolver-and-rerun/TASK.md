# TASK: T-Lr6Ka3-llm-resolver-and-rerun

## Metadata
- Task ID: `T-Lr6Ka3-llm-resolver-and-rerun`
- Epic ID: `E-Wk9Tz3-task-isolation`
- Owner: unassigned (developer)
- Created: 2026-09-06
- Last Updated: 2026-09-06
- Status: Draft
- Estimate: 3 days

## Requirements Mapping
- Requirement IDs: FR-7 (tiers T2/T3/T4), FR-8 (verify-failure path), NFR-1 · Design: HLD §8.5, §8.6, §11 M7

## Description
Tiers 2-4: a bounded LLM merge-resolver, a bounded re-run on the fresh base, and the fall-through to
operator. Escalation reuses the **existing requeue machinery** — no DAG injection (ADR-0013 D6).

Files you own:
- `src/agent_orchestrator/isolation/escalation.py` (new)
- `src/agent_orchestrator/templates/builtin/instructions/merge-resolve.md` (new asset)
- `src/agent_orchestrator/engine.py` (edit — **only** the resolver/rerun dispatch overrides and the
  requeue branches `T-En8Hd4` left hook points for; read the merged file first)
- `tests/isolation/test_escalation.py`, `tests/test_engine_conflict_escalation.py` (new)

Do NOT touch: `integrator.py`, `resolvers.py`, `worktrees.py`, `models.py`, `spec.py`, `cli.py`,
`templates/builtin/routed-runner/` (that is `T-Tp7Zs2`).

## Acceptance Criteria
1. `escalate(ti, spec, cause) -> IntegrationResult` implements HLD §11 M7's decision table exactly.
   A **table-driven test** covers every combination of `ladder` membership x `resolver_attempts`
   vs `max_resolver_attempts` x `reruns` vs `max_reruns_per_task` x `cause in {conflict, verify}` —
   including `max_resolver_attempts: 0` (straight to T3) and both caps `0` (straight to T4, pure
   merge-queue ejection).
2. The conflict manifest `<run_dir>/<task>/integration/conflict-<n>.json` matches HLD §11 M7's shape
   and contains **only** ids, paths, refs and booleans. A test asserts no value in the JSON is read
   from a repository file's contents.
3. `merge-resolve.md` ships in the package and is **copied** into
   `<run_dir>/<task_id>/integration/merge-resolve.md` at requeue time (so it satisfies the artifact
   path guard). Its text instructs the agent to: read the manifest; resolve only the listed paths in
   the worktree; preserve **both** intents where the conflict is additive; never delete a sibling's
   change to make things compile; `git add` each resolved path; and **not** run
   `rebase --continue`/commit/push/switch branches. A test asserts the file is packaged (mirroring
   `tests/test_builtin_routed_runner_assets.py`) and that the copy lands in the run dir.
4. Resolver-mode dispatch: when `task_integration[tid].mode == "resolve"`, the engine builds
   `TaskContext` with `agents[integration.resolver_agent]`, `instruction_path` = the copied
   `merge-resolve.md`, and the conflict manifest appended to `input_paths` — while keeping the task's
   own id, worktree, timeout and output dir. Test with a `fake` executor asserting the substituted
   agent id and instruction path.
5. **Cost accounting (D9)**: a resolver attempt goes through the normal budget gate/charge on
   redispatch and its actuals accumulate into `TaskRunState.cumulative_cost_usd` /
   `cumulative_*_tokens`. Test: a task that conflicts once has a cumulative cost equal to
   attempt 1 + resolver attempt, `compute_run_usage_totals` reflects it, and a `task_cost_usd`
   breaker set just above attempt 1's cost **trips** on the resolver attempt.
6. After a successful resolver attempt the worker calls `Integrator.resume_integration`, which
   continues the rebase, verifies and lands. Test end to end with a fake resolver agent that writes a
   real resolution into the worktree.
7. A resolver agent that "succeeds" without resolving anything → `rebase_continue` still reports
   conflicts → `escalate` is called again with the counter already incremented → terminates at T3 or
   T4. Test proves termination (no infinite requeue loop).
8. T3 rerun: the worktree is `reset --hard` to the **fresh** integration head, the superseded squash
   is exported to `previous-<n>.patch` via `git diff base..squash`, that path is appended to the
   task's inputs, and the **original** agent is redispatched. Tests: the patch exists and applies
   cleanly to the old base; the worktree base recorded in state is updated; the rerun counter
   increments; a second conflict after the cap goes to T4.
9. Verify-failure entry point (HLD §8.6): a verify failure calls `escalate(cause="verify")`, which
   goes to T3 when reruns remain and T4 otherwise — it never invokes T2 (an LLM cannot resolve a
   conflict that git never reported). Dedicated test.
10. T4: `ts.status = failed`, `task_integration.status = failed`, the worktree and branch are
    **retained regardless of `keep_worktrees`**, and `integration.failed` names the conflicted paths,
    the worktree path and the branch. A follow-up test resolves the conflict by hand in the retained
    worktree and shows `ao resume` completes the run.
11. `uv run pytest -q` fully green with recorded counts; `ruff` clean; `uv run mypy src` zero new
    errors; the NFR-2 gate (pre-epic engine suite unedited) still passes.

## Risks
- **Runaway cost.** Mitigation: caps default to 1 each; the per-task budget breaker applies (AC-5);
  the counters live in `RunState` so a resume cannot reset them.
- **A plausible-but-wrong LLM merge** (R2). Mitigation: verify runs after resolution; the instruction
  forbids deleting a sibling's change; the result is one reviewable commit per task. Document that
  `rerere` does **not** learn from a resolution that fails verify (verify precedes landing).
- Editing `engine.py` after `T-En8Hd4`. Mitigation: read the merged file; keep the diff to the hook
  points that ticket published; do not refactor around them.

## Dependencies
- Upstream: `T-En8Hd4` (hook points, requeue signals), `T-Rm2Lx7` (the unresolved list).
- Downstream: `T-Ee3Mn8`.

## Pseudocode / Algorithm
```text
HLD §11 M7 escalate(); §8.5 the T1->T2->T3 sequence diagram; §8.6 the verify-failure diagram.
```

## Schemas / Interface Notes
- Interface / API (locked): `escalate(ti, spec, cause) -> IntegrationResult`,
  `write_conflict_manifest(...)`, `export_previous_patch(...)`.
- Spec / data schema: `conflict-<n>.json` as in HLD §11 M7 — versioned by a `"version": "1.0"` key so
  the resolver instruction can be evolved independently.
- Triggers / events: `integration.resolver_dispatched`, `integration.rerun_dispatched`,
  `integration.resolved tier=llm`, `integration.failed`.
- Artifacts: `<run_dir>/<task>/integration/{conflict-<n>.json, previous-<n>.patch, merge-resolve.md}`.

## Handoff Boundary
- Upstream: merged `engine.py` (hook points) + `Integrator.resume_integration`.
- Downstream: `T-Tp7Zs2` adds a `merge-resolver` agent to the routed-runner `required_agents`;
  `T-Ee3Mn8` drives the full ladder e2e.

## Artifacts
- Docs/comments: `meta/tickets/E-Wk9Tz3-task-isolation/T-Lr6Ka3-llm-resolver-and-rerun/`
- Large outputs: none
