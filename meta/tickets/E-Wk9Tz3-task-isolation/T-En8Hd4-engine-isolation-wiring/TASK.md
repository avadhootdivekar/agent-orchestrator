# TASK: T-En8Hd4-engine-isolation-wiring

## Metadata
- Task ID: `T-En8Hd4-engine-isolation-wiring`
- Epic ID: `E-Wk9Tz3-task-isolation`
- Owner: unassigned (developer)
- Created: 2026-09-06
- Last Updated: 2026-09-06
- Status: Draft
- Estimate: 3 days

## Requirements Mapping
- Requirement IDs: FR-1, FR-4, FR-9, FR-12, FR-13, NFR-2, NFR-3 · Design: HLD §7.3, §7.4, §11 M5

## Description
Connect worktrees + the integrator to the wave scheduler **without breaking a single ADR-0007
invariant**. This is the task that must prove `max_parallel == 1` + `isolation: none` is byte-identical
to today.

Files you own:
- `src/agent_orchestrator/engine.py` (edit)
- `src/agent_orchestrator/artifacts.py` (edit — `extra_roots` + `IsolatedArtifactView` only)
- `src/agent_orchestrator/runstate.py` (edit — integration-aware `should_skip` only)
- `src/agent_orchestrator/executors/claude_cli.py` (edit — `env=` overlay only, ~2 lines)
- `src/agent_orchestrator/executors/fake.py` (edit — record `ctx.env`/`ctx.cwd` for assertions)
- `tests/test_engine_isolation.py`, `tests/test_isolated_artifact_view.py` (new)

Do NOT touch: any `isolation/` module (read them), `models.py`, `spec.py`, `specs/*.schema.json`,
`cli.py`, `templates/`. Read the **merged** code of `T-Gt4Pw8`, `T-Sc7Rm2`, `T-Wk3Nv6`, `T-Ib5Qy9`.

## Acceptance Criteria
1. **NFR-2 blocking gate.** The entire pre-epic engine test suite (`tests/test_engine*.py`,
   `tests/test_wave_*.py`, `tests/test_engine_routing.py`, `tests/test_dynamic_injection.py`,
   `tests/test_loop_construct.py`, budget/breaker/monitoring suites) passes **with zero edits** at
   defaults (`max_parallel=1`, no `isolation` key anywhere). Record the exact before/after pass counts
   in `STATUS.md`. If any of those files needs a change, **stop and flag it** — it means the default
   path moved.
2. `_ready_ids` uses `_settled_for_dependents` (HLD §11 M5): a predecessor counts as settled only when
   its integration status is `integrated`/`none` (or it was never isolated). Test: an isolated
   predecessor left `pending` blocks its dependent even though `TaskRunState.status == "succeeded"`.
3. `_is_barrier` additionally returns `True` for a non-isolated task whenever
   `state.integration.active`. Test: with isolation active, a shared-checkout task never overlaps an
   isolated one (assert via a gated executor that only one is ever in flight).
4. `_activate_integration` (HLD §11 M5) runs **once per run**, lazily at the first isolated dispatch;
   it records `branch`, per-repo `heads`/`base_heads`, appends `.orchestrator/` to each repo's
   `.git/info/exclude` (**never** edits `.gitignore`), and degrades with a single
   `integration.degraded` event when git is absent/old, no repo is a git repo, HEAD is unborn, or
   `$AO_STATE_DIR` is inside a repo. `isolation.strict: true` turns each degrade into a run failure.
   One test per degrade cause plus one for `strict`.
5. **Lazy activation is load-bearing**: a workflow whose first task (`isolation: none`) creates and
   checks out a branch, followed by isolated tasks, must base the integration ref on the **new**
   branch's HEAD. Test this exact shape (it mirrors the consumer's `git-branch-off` → fan-out flow).
6. `IsolatedArtifactView` wraps the base store, applies `effective_path`, and adds only the task's own
   worktree roots as `extra_roots`. Tests: traversal (`../../etc/passwd`) still raises
   `ArtifactPathError`; a symlink pointing outside still raises; a path in another task's worktree
   raises; `RunStateStore`'s store is **not** widened (assert its `_extra_roots` is empty).
7. Every path in `TaskContext` is built through the view for an isolated task — `instruction_path`,
   `general_instruction_paths`, `input_paths`, `output_paths`, `output_manifest_path`, `repo_paths`,
   `cwd` — and `task_manifest_path`, `gate_output_path`, `output_dir` are **not** (they stay under
   `.orchestrator/`). A single test asserts all ten in one dispatch.
8. `TaskContext.env` is populated with `AO_ISOLATION`, `AO_TASK_BRANCH`, `AO_INTEGRATION_BRANCH`,
   `AO_WORKTREE_ROOT_<repo>` plus `isolation.env` per-repo entries; `ClaudeCliExecutor` passes
   `env={**os.environ, **ctx.env}` when `ctx.env` is non-empty and `env=None` otherwise (so the
   no-isolation path is byte-identical). Test both branches.
9. `_run_and_integrate` runs on the **worker**: `_run_with_retries` unchanged, then `integrate(...)`
   when the task is isolated and succeeded. It performs **no** `RunState` mutation and **no** `save`
   (NFR-3). A test asserts this by failing the run if `RunState.model_dump()` changes while a worker
   holds it (e.g. a wrapper store that records the calling thread of every `save`).
10. `_settle_completed_task` records the `IntegrationResult` into `state.task_integration[tid]`,
    updates `state.integration.heads`, copies untracked declared outputs per
    `integration.untracked_outputs`, releases the worktree per `keep_worktrees`, and returns
    `"requeue"` for `conflict_resolver` / `conflict_rerun`. Each branch has a test.
11. `should_skip` for an isolated task additionally requires
    `task_integration[tid].status == "integrated"`. **Dedicated regression test for R6**: a task whose
    declared output exists (written outside the repo) but whose integration status is `failed` is
    **not** skipped on resume.
12. Checkout sync (FR-13): before a non-isolated task dispatches with `state.integration.active`, the
    engine fast-forwards the primary checkout (`git merge --ff-only <integration_branch>`), emitting
    `integration.sync_ok` / `sync_skipped` / `sync_failed`. A failed sync returns a `halt`
    `DispatchPrep` with a structured error. Run end performs a final best-effort sync +
    `reconcile()` + `integration.summary`. Tests for success, dirty-tree failure, and `sync_checkout:
    never`.
13. Cancel/halt during integration drains (ADR-0007 D7) and leaves a resumable state: a test cancels
    mid-integration and asserts `ao resume` completes the run.
14. `uv run pytest -q` fully green with recorded counts; `ruff` clean; `uv run mypy src` zero new
    errors.

## Risks
- Highest-blast-radius file in the repo. Mitigation: AC-1 is the gate; make the isolated path an
  additive branch around the existing code, never a rewrite of it — the same "verbatim extraction"
  discipline that made ADR-0007's `N=1` byte-identical.
- `engine.py` is also touched by `T-Lr6Ka3` and `T-Cx4Jf1`. Land this first; those two read the merged
  file and make narrowly-scoped edits.
- The path-guard widening is security-relevant; `T-Ee3Mn8` will review exactly this diff. Keep it
  minimal and comment the invariant in code.
- `prepare_resume` resets `TaskRunState`; integration state must be read from `RunState`, never from
  the reset object.

## Dependencies
- Upstream: `T-Sc7Rm2`, `T-Wk3Nv6`, `T-Ib5Qy9`.
- Downstream: `T-Lr6Ka3`, `T-Cx4Jf1`, `T-Ee3Mn8`.

## Pseudocode / Algorithm
```text
HLD §11 M5 in full — _settled_for_dependents, _is_barrier, the wave-fill change,
_prepare_and_maybe_dispatch additions, _run_and_integrate, _settle_completed_task
additions, _activate_integration, and the run-end block.
```

## Schemas / Interface Notes
- Interface / API: `Orchestrator.__init__` gains `worktree_manager`/`integrator` injection points
  (default constructed) so tests can substitute fakes — same pattern as `budget_manager`/`monitor`.
- Spec / data schema: consumes `T-Sc7Rm2`'s models; adds none.
- Triggers / events: `integration.activated|degraded|sync_ok|sync_skipped|sync_failed|summary`.
- Artifacts: none new (writes only through existing stores).

## Handoff Boundary
- Upstream: read the merged `isolation/` package.
- Downstream: publish the exact hook points (`_run_and_integrate`, the settle switch, the requeue
  signals) in `STATUS.md` so `T-Lr6Ka3` extends rather than re-derives them.

## Artifacts
- Docs/comments: `meta/tickets/E-Wk9Tz3-task-isolation/T-En8Hd4-engine-isolation-wiring/`
- Large outputs: none
