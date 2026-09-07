# TASK: T-En8Hd4-engine-isolation-wiring

## Metadata
- Task ID: `T-En8Hd4-engine-isolation-wiring`
- Epic ID: `E-Wk9Tz3-task-isolation`
- Owner: unassigned (developer)
- Created: 2026-09-06
- Last Updated: 2026-09-07
- Status: In Review
- Estimate: 3 days

## Requirements Mapping
- Requirement IDs: FR-1, FR-4, FR-9, FR-12, FR-13, NFR-2, NFR-3 · Design: HLD §7.3, §7.4, §11 M5
- Review findings folded in: **R-19** (blocking — the primary execution path), **R-2** (blocking),
  **R-3** (blocking), **R-5** (blocking — the `rank_wave` call site), **R-23** (major), **S-7**
  (minor). **Moved OUT of this ticket** to keep it <= 3 days: R-1/R-21 -> `T-Ac6Vd9-requeue-accounting`,
  R-4/R-12 -> `T-Wl2Bq7-workspace-run-lock`. Estimate unchanged at 3 days.

## Description
Connect worktrees + the integrator to the wave scheduler **without breaking a single ADR-0007
invariant**. This is the task that must prove `max_parallel == 1` + `isolation: none` is byte-identical
to today.

Files you own:
- `src/agent_orchestrator/engine.py` (edit)
- ~~`src/agent_orchestrator/artifacts.py`~~ — **no longer this ticket's file.** `T-Wk3Nv6` already
  shipped everything needed there (a read-only `resolve_unchecked` + `root`, used solely by the view)
  and `isolation/view.py::IsolatedArtifactView`. **Do not add an `extra_roots` parameter to
  `LocalFsArtifactStore`** — HLD §7.3 rejects exactly that, because `RunStateStore` shares the same
  instance and widening it would silently widen run-state resolution too.
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
6. **Consume the already-shipped view; build nothing new here.** Per dispatch of an isolated task,
   construct `isolation.view.IsolatedArtifactView(base=self._store, task_isolation=task_iso)` and pass
   **that** object as the task's store (AC-15). The shared `LocalFsArtifactStore` is never widened and
   never mutated. `T-Wk3Nv6` already ships and tests the view's own guarantees (sibling-task,
   cross-run, traversal, symlink-escape, reserved prefixes, nested `RepoRef`) in
   `tests/isolation/test_view.py` — **do not duplicate those tests here.** This ticket's own assertion
   is the wiring one: `RunStateStore`'s store is the **base** store, never a view — follow
   `T-Wk3Nv6`'s `tests/isolation/test_view.py::TestRunStateStoreNeverWrapped` pattern
   (`assert rs_store._store is base` / `not isinstance(rs_store._store, IsolatedArtifactView)`), and
   assert a non-isolated task's `TaskContext` is built from the base store unchanged (NFR-2).
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

### Amendments from the 2026-09-07 review gates

*(These renumber nothing above; treat AC-15 as the new **first** thing to implement and test.)*

15. **R-19 — BLOCKING, and the single most important criterion in this ticket. Do this first.**
    `_run_with_retries` (`engine.py:1921-1970`) computes **six** of the seven remappable path
    categories *inside itself*, from `self._store`: `instruction_path`,
    `general_instruction_paths`, `input_paths`, `output_paths`, `output_manifest_path` and
    `agent_cwd`. Only `repo_paths` is already a parameter. As the design originally read, an
    "isolated" task would still read and write the **shared checkout** while ao dutifully created a
    worktree nothing used — silently defeating the entire epic while every other test passed.
    `self._store` cannot be swapped per call: one instance is shared across every concurrent worker.
    Fix: add `store: ArtifactStore | None = None` to `_run_with_retries` (and thread it through
    `_run_and_integrate`), defaulting to `self._store`, and route **all six** call sites through it.
    Tests: (a) for an isolated task, assert on the real `TaskContext` a `fake` executor receives that
    **each of the six** resolves inside the worktree — not on a helper's return value; (b) assert
    `task_manifest_path`, `gate_output_path` and `output_dir` do **not**; (c) with `store=None` the
    resolved paths are byte-identical to today (NFR-2).
16. **R-2 — outputs gate integration.** `_run_and_integrate` performs the missing-outputs check itself,
    on the worker, through the isolated view, and calls `Integrator.integrate()` **only** when it
    passes; a failure returns `WorkerOutcome(result, None, missing_outputs=[...])`. The main-thread
    check in `_settle_completed_task` (`engine.py:1122-1138`) is left **untouched** — it re-runs and
    reaches the same verdict, which is what keeps the non-isolated path byte-identical.
    Test: an isolated task that succeeds but is missing a declared output **lands nothing** (assert the
    integration ref did not move) and then settles `failed`.
17. **R-3 — the integration gate applies to BOTH branches of `should_skip`.** `runstate.py:156-172` has
    two independent branches; the second (`if task.skip_if_outputs_exist and task.outputs:`) has **no
    `ts.status` check at all**, runs on every wave (`engine.py:527`), and is the one the consumer
    actually exercises (`skip_if_outputs_exist: true` on every fan-out entry). A task parked in
    `conflict_resolver` has `ts.status == "pending"` and an output file that already exists, so
    branch 2 would mark it `"skipped"`, add it to `ctx.done`, and let dependents proceed as though its
    code had landed. **Dedicated regression test must target branch 2 by name.**
18. **R-5 — the `rank_wave` call site is owned here.** In `run()`'s wave-fill loop, immediately after
    `ready = self._ready_ids(...)`: compute `pref = resolve_overlap_preference(workflow)` and, when
    `"soft"`, replace the candidate list with `rank_wave(ready, touches_of(workflow), ctx.hotspots,
    max_parallel - len(in_flight))`. Hotspots load **once at run start** via `load_hotspots(...)` with
    an empty-on-any-error fallback. Test: a live wave dispatch at `"soft"` actually applies the
    ordering (assert the **dispatched** set, not the pure function, which `T-Ov9Bt5` already covers);
    and at `"off"` / `max_parallel == 1` the dispatch order is unchanged.
19. **R-23 — `release()` on a plain execution failure.** A task whose execution simply fails (retries
    exhausted, integration never reached) returns `WorkerOutcome(result, None)`, which never enters the
    integration switch — so no `release()` call was reachable, and even `keep_worktrees: "never"` would
    leak the worktree and branch of arguably the most common failure path. Add the release on that path
    as its own named case. Test it separately from "integration itself reported failed".
20. **S-7 — confirm the breaker interaction, by test.** A verify-failure storm (a `verify_command` that
    fails for every task) must trip an existing `consecutive_failures`/`task_failures` breaker and HALT
    rather than accumulating retained worktrees silently. Confirm this **with a test**, not by
    assertion. Emit `worktree.retention_high` once at a named threshold constant, pointing at
    `ao prune --worktrees-only`.
21. `ts.dispatch_cycle += 1` on every dispatch (the field ships with `T-Sc7Rm2`). This ticket only
    increments and persists it; the capture-directory keying and the budget-cycle keying that consume
    it are `T-Ac6Vd9`'s.

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
- **Do AC-15 first.** Every other criterion in this ticket can pass while the epic does nothing useful
  if R-19 is not fixed; the review called it "a gap in the primary execution path", not an edge case.
- Five tasks now edit `engine.py`, in this order: **this one** -> `T-Ac6Vd9` -> `T-Wl2Bq7` ->
  `T-Lr6Ka3` -> `T-Cx4Jf1`. Publish your hook points (`_run_and_integrate`'s signature, the settle
  switch, the requeue signals, the wave-fill call site) in `STATUS.md` so the later four extend rather
  than re-derive.

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

---
- By: architect · Role: architect · Date: 2026-09-07 · Comment: Phase-2 amendment. Added the five
  findings that gate this ticket's merge — R-19 (now AC-15 and the first thing to implement; without it
  isolated tasks still run in the shared checkout), R-2, R-3 (branch 2 named explicitly), R-5 (the
  wave-fill call site, previously owned by nobody) and R-23 — plus S-7's breaker confirmation. To keep
  the ticket at <= 3 days, R-1/R-21 moved to the new `T-Ac6Vd9-requeue-accounting` and R-4/R-12 to the
  new `T-Wl2Bq7-workspace-run-lock`; `budget.py` and the sync lock are no longer this ticket's files.
