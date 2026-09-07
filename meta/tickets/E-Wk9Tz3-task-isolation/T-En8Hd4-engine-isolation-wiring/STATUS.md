# STATUS

- ID: `T-En8Hd4-engine-isolation-wiring`
- Updated At: 2026-09-07
- State: In Review
- Owner: developer-agent

## This update
- Ticket created by the architect as part of the `E-Wk9Tz3-task-isolation` design package. Not
  started; no code written.
- **2026-09-07 — Phase-2 review amendment applied.** Folded in: R-19 (now AC-15 and the **first** thing to implement — without it isolated tasks still run in the shared checkout), R-2, R-3 (branch 2 of `should_skip` named explicitly), R-5 (the wave-fill call site, previously owned by no ticket), R-23, S-7. R-1/R-21 moved out to `T-Ac6Vd9`; R-4/R-12 moved out to `T-Wl2Bq7`, so `budget.py` and the sync lock are no longer this ticket's files. Estimate unchanged at 3 days.
- Per-finding dispositions: HLD §24 "Review dispositions".

- **2026-09-07 — implementation complete: In Review.** All 21 ACs implemented in
  `src/agent_orchestrator/engine.py` (narrow, additive branches around the existing wave/barrier
  scheduler — no rewrite of unrelated logic) plus a ~5-line additive change each in
  `src/agent_orchestrator/executors/claude_cli.py` (`env=` overlay) and
  `src/agent_orchestrator/executors/fake.py` (`self.contexts` recording + additive `repo_writes`
  option, both backward compatible). `models.py`/`spec.py`/`runstate.py`/`cli.py`/`artifacts.py`/
  `isolation/*` were read only, never edited, per the concurrency boundary.

  **AC-15/R-19 (done first, as instructed).** `_run_with_retries` gained `store: ArtifactStore | None
  = None` (defaults to `self._store`, byte-identical, NFR-2) and all six internal `self._store.resolve`
  call sites (`instruction_path`, `general_instruction_paths` — `_resolve_general_instructions` also
  gained a `store=` param — `input_paths`, `output_paths`, `output_manifest_path`, `agent_cwd`) now go
  through the local `st` binding. `task_manifest_path`/`gate_output_path`/`output_dir` deliberately
  stay on `self._store` (R-15). The seventh category, `repo_paths`, is threaded as a per-task dict
  built by module-level `_iso_repo_paths(task_iso)` (HLD §7.1: `{repo_id: worktree_root/rel}`) and
  passed as the existing `repo_paths` parameter — no new parameter needed there. A new worker function,
  `_run_and_integrate`, wraps `_run_with_retries` + the R-2 outputs gate + the integrator call and is
  what `run()` now submits to the pool (was `_run_with_retries` directly).

  **Dispatch prep (`_prepare_and_maybe_dispatch`).** New isolation-setup block runs AFTER the
  (now integration-aware) `should_skip` check and BEFORE join/missing-inputs/budget-estimate, all of
  which now resolve through `ctx_store` (the task's `IsolatedArtifactView` when isolated, else
  `self._store`) instead of `self._store` directly — this is what lets an isolated dependent see a
  predecessor's integrated work inside its own freshly-created worktree (§7.4). `ts.dispatch_cycle +=
  1` happens here, once per dispatch (AC-21; the capture-directory/budget-cycle *keying* that consumes
  it is explicitly `T-Ac6Vd9`'s, per TASK.md — this ticket only increments/persists the field).
  `_activate_integration` runs lazily, once, latched via `ctx.integration_degraded`; a
  `WorktreeCollisionError` from `ensure()` fails the task/run with a structured `task.fail` event
  (dedicated test). A non-isolated dispatch while `state.integration.active` calls `_sync_checkout`
  first (barrier, enforced by `_is_barrier`'s new isolation-active branch).

  **Settle (`_settle_completed_task`).** Signature now takes `WorkerOutcome` (result + optional
  `IntegrationResult` + optional `missing_outputs` + echoed `task_iso`) instead of a bare `TaskResult`;
  the entire pre-existing "extracted verbatim" body is untouched except for one new line
  (`result = outcome.result`) and one new block inserted between the existing missing-outputs check
  and the existing `ctx.done.add(tid)` section. That block implements the full HLD §11 M5 switch:
  `integrated`/`empty` (record heads, copy-back untracked outputs, `release("integrated", ...)`),
  `conflict_resolver`/`conflict_rerun` (accumulate-then-requeue — no *extra* accumulation call needed
  since the pre-existing unconditional `ts.cumulative_*` update already runs earlier in the same
  function, before this block), and `failed` (T4 — worktree/branch deliberately retained, no
  `release()` call). R-23's plain-execution-failure case (`outcome.integration is None`) is handled as
  its own branch, always calling `release("failed", ...)`, with a dedicated test distinguishing it from
  an integration-reported failure.

  **Hook points not implemented here, on purpose (leave for downstream tickets):**
  - `resolver_hook`/`escalation_hook` — `Orchestrator` gained both as constructor params (matching
    "same pattern as budget_manager/monitor"); when unset, module-level `_default_resolver_hook`
    (resolves nothing) and `_default_escalation_hook` (fails straight to T4, naming
    `no_escalation_hook_configured:<cause>`) are used. This is a deliberate, documented design
    decision: since `T-Rm2Lx7`/`T-Lr6Ka3` have not landed, a genuine conflict/verify-failure "surfaces
    as a task failure for now" (matches this ticket's own Requirements section) via the fail-safe
    default, rather than this ticket guessing at T1/T2/T3 mechanics. `_settle_completed_task`'s
    `conflict_resolver`/`conflict_rerun` switch cases are fully implemented and tested (AC-10) against
    an **injected test-double** `integrator`/`escalation_hook` — nothing else needs to change once the
    real hooks land.
  - `_run_and_integrate` never branches on `task_integration.mode` ("resolve"/"rerun") — that dispatch
    substitution (resolver agent/instruction/conflict-json inputs, or reset-to-fresh-head +
    previous-patch inputs) is explicitly `T-Lr6Ka3`'s per TASK.md's Risks section. The function's own
    docstring names this as the HOOK POINT.
  - `Integrator`/`WorktreeManager` construction: `Orchestrator` gained `worktree_manager=`/`integrator=`
    constructor params (used verbatim for the whole run when given — test substitution); the ONE call
    site into `Integrator` is the new `_integrate_task` adapter (wraps `integrate()`/
    `resume_integration()`), per TASK.md's explicit "wrap every call in ONE thin adapter" instruction.

  **Interface gaps found and resolved as scoped constructor injection points (not files I'm allowed to
  touch — reported here rather than guessed at):**
  - `isolation.strict` / `--isolation`/`AO_ISOLATION`/`.ao/config.yaml: isolation.mode` (HLD §11 M9):
    this CLI/config precedence chain does not exist anywhere in the codebase yet (`cli.py` has no
    `isolation` flag at all) — it is M9, a distinct, not-yet-created ticket, and `cli.py` is off-limits
    to this one. Added `Orchestrator(isolation_strict: bool = False)` as the engine-side injection
    point (mirrors `max_parallel`'s own "invocation-scoped setting" pattern documented on the class) so
    AC-4's strict-mode behavior is implementable and tested; a future M9 ticket wires the CLI flag/env/
    config into this parameter.
  - `isolation.env` per-repo overlay (`.ao/config.yaml: isolation.env`, HLD §11 M9): same gap, same
    resolution — `Orchestrator(isolation_env: dict[str, dict[str, str]] = {})`.
  - **Filed as an interface note, not a blocking issue**, since both are additive, optional constructor
    parameters with byte-identical defaults.

- **CORRECTED 2026-09-07 (review C-1) — the "known limitation" below was a real Blocking defect, not
  an acceptable tradeoff; see the review-response entry at the end of this file for the fix.** The
  paragraph immediately below is preserved verbatim (struck through in spirit, not in markdown) as the
  original, now-superseded reasoning, so the record shows what was believed and why it was wrong:
  `_settle_completed_task`'s PRE-EXISTING missing-outputs check (main-thread, `self._store`) ran
  immediately at settle, before any checkout sync ever occurred for that task, so a declared output
  living INSIDE the isolated repo was wrongly reported missing even after a successful land — the run
  halted `"failed"` while `task_integration[tid].status == "integrated"` (code safely shipped). This
  was mischaracterized here as "the documented, reviewed behavior HLD §24/R-2's disposition accepts,"
  which the review found is not actually what HLD §7.4/§24/R-2 say (they claim the check "will re-run
  and reach the same verdict," unconditionally, with no outside-repo caveat). Fixed: the outputs
  verdict for an isolated task is now the WORKER's own R-2 gate (evaluated through the
  `IsolatedArtifactView`, in the worktree, before landing) — see the review-response entry below.

- **Additive test-support change, authorized in TASK.md's "Read first."** `executors/fake.py` gained
  `FakeExecutor(repo_writes: dict[task_id, dict[repo_id, dict[rel_path, content]]] = None)`: on
  success, writes literal content into `ctx.repo_paths[repo_id]/rel_path` — lets a test simulate an
  agent that modifies a TRACKED file inside a repo (which `output_paths`-only writes cannot, since
  those commonly live outside every repo). Also added `self.contexts: dict[str, TaskContext]` (the
  full, real `TaskContext` per task_id, last-invocation-wins) alongside the existing
  `self.prompts`/`self.resolved_agents` recording pattern — used throughout the new test suite to
  assert every one of AC-7's ten path categories on the REAL object the executor received, not a
  helper's return value (per AC-15's own explicit requirement).

## Hook points published for T-Ac6Vd9 / T-Wl2Bq7 / T-Lr6Ka3 / T-Cx4Jf1 (engine.py's shared-file order)

```python
# --- T-Ac6Vd9 (requeue accounting: capture-dir + budget-cycle keying by dispatch_cycle) ---
# `TaskRunState.dispatch_cycle` is incremented (once per dispatch) and persisted in
# `_prepare_and_maybe_dispatch`, right after the should_skip gate:
#     ts_pre = state.tasks.setdefault(tid, TaskRunState())
#     ts_pre.dispatch_cycle += 1  # R-21
# `_run_with_retries`'s own `output_dir` (capture directory) is UNCHANGED
# (".orchestrator/runs/<run_id>/<task_id>/attempt-<n>/", self._store, always) -- T-Ac6Vd9 is the
# one that re-keys it as "cycle-<dispatch_cycle>/attempt-<n>/". `budget.py`'s
# charged_estimate/reconciled_cycles keying by "<task_id>#<dispatch_cycle>" is untouched here
# (budget.py is not this ticket's file) -- ts.dispatch_cycle is already correctly available on
# TaskRunState by the time budget.py's gate/charge/reconcile run.

# --- T-Wl2Bq7 (workspace run lock) ---
# `_activate_integration(self, state: RunState, workflow: WorkflowSpec, ctx: _RunContext) -> bool`
# is where R-4's WorkspaceRunLock claim belongs -- FIRST thing inside the function, before the
# `GitRepo.version()` probe. `state.integration.workspace_lock_held` is left at its schema
# default (False) by this ticket; set it there once the claim exists. `workflow.integration.
# workspace_lock` ("require"/"skip_sync"/"off") is already on IntegrationSpec (T-Sc7Rm2) and
# unread by this ticket -- read it in `_activate_integration` to decide DENIED-claim behavior.

# --- T-Lr6Ka3 (resolver/rerun dispatch ladder) ---
# `_run_and_integrate(self, task, workflow, agents, repo_paths, state, dynamic_input_paths,
#                      task_manifest_path, gate_output_path, store, task_iso, integrator,
#                      run_integration, env_overlay, agent_id) -> WorkerOutcome`
# is the HOOK POINT: branch on `state.task_integration[task.id].mode` ("resolve"/"rerun") BEFORE
# the existing `result = self._run_with_retries(...)` call to substitute agent_override/
# instruction_override/extra_inputs/env_overlay (mode == "resolve") or reset-to-fresh-head +
# previous-patch inputs (mode == "rerun"), then call `self._integrate_task(..., resume=True)`
# instead of `self._integrate_task(...)` (plain). `_integrate_task`'s `resume` kwarg already
# exists and is tested (calls `integrator.resume_integration(...)`) -- just needs a real caller.
# Provide the real `resolver_hook`/`escalation_hook` via `Orchestrator(resolver_hook=...,
# escalation_hook=...)`; `_default_resolver_hook`/`_default_escalation_hook` (module-level,
# engine.py) are the placeholders to replace, not delete (keep as the safe fallback when hooks
# are not supplied).

# --- T-Cx4Jf1 (event emission / observability polish, e.g. S-5 tier_counts surfacing) ---
# Every `integration.*` event this ticket emits is listed in `_settle_completed_task`'s new
# block and in `_activate_integration`/`_sync_checkout`/`_log_integration_summary`/
# `_warn_if_retention_high`/`_copy_untracked_outputs` (grep `extra={"event": "integration.` in
# engine.py). `RunIntegrationState.tier_counts` is NOT touched anywhere in this ticket (per
# T-Ib5Qy9's own note: "this module does not itself touch tier_counts, R-20") -- deriving/
# incrementing it from `IntegrationResult.tier_reached` at settle time (the `integ.tier_reached`
# value is already recorded onto `ti.tier_reached` in this ticket's switch) is open for
# T-Cx4Jf1 to add as a small, additive line in the same switch block.
```

## Gates (exact numbers, post-review fix pass, 2026-09-07)
- `uv run ruff check .` / `uv run ruff format --check .`: clean, repo-wide.
- `uv run mypy src`: unchanged at exactly 4 pre-existing `_version.py` errors.
- Targeted suite (`tests/test_engine.py tests/test_engine_isolation.py tests/test_e2e_cli_isolation.py
  tests/test_e2e_cli_max_parallel.py tests/test_wave_scheduler.py tests/test_dynamic_injection.py
  tests/test_engine_routing.py tests/test_engine_breakers.py tests/test_engine_budget.py
  tests/test_e2e_cli.py`): **202 passed / 0 failed**.
- Full suite WITH coverage (`timeout 900 uv run pytest -q -p no:cacheprovider --cov=agent_orchestrator
  --cov-report=term`): **3394 passed / 7 skipped / 0 failed** in 148.41s — **TOTAL coverage 95%**
  (matches baseline). This ticket's earlier report of a `--cov` hang specifically inside
  `tests/bench/test_workspace.py` did NOT reproduce here or in the reviewer's own two attempts
  (REVIEW.md Investigation B) — `[tool.coverage.run] concurrency = ["thread"]` was added to
  `pyproject.toml` regardless, per the reviewer's own defense-in-depth recommendation, and confirmed
  not to regress this run.
- `tests/test_engine_isolation.py` now has 57 tests (52 + 5 from this fix pass: 3 for C-1, 1 for C-2,
  1 for C-3); `tests/test_e2e_cli_isolation.py` unchanged at 2.

## Ticket sync
- `TASK.md`: Status → In Review.
- Epic `STATUS.md`: rollup row added for this task only (see below).

## Evidence
- Design: [`docs-md/task-isolation-hld.md`](../../../../docs-md/task-isolation-hld.md) §6, §7, §8,
  §9, §11 M5, §24 and
  [`ADR-0013`](../../../../docs-md/adr/ADR-0013-per-task-git-isolation-and-rebase-integration.md)
  D1/D3/D4/D5/D6/D7.
- Edited source: `src/agent_orchestrator/engine.py`, `src/agent_orchestrator/executors/claude_cli.py`,
  `src/agent_orchestrator/executors/fake.py`, `pyproject.toml` (coverage concurrency, W-1-adjacent).
- New tests: `tests/test_engine_isolation.py` (57 tests), `tests/test_e2e_cli_isolation.py` (2 tests).
- Review: `REVIEW.md` (APPROVE WITH CHANGES); this file's final entry below records the fix pass.

## Risks / Blockers
- See `TASK.md` > Risks. `engine.py` is also touched by `T-Lr6Ka3` and `T-Cx4Jf1` next, in that order
  — hook points published above (unchanged by the review fix pass).
- `resolver_hook`/`escalation_hook` defaults mean a genuine conflict/verify-failure fails straight to
  T4 in production until `T-Lr6Ka3` supplies the real hooks — by design (see "This update" above), not
  an oversight.
- `isolation_strict`/`isolation_env` constructor params have no CLI/config wiring yet (M9, not a
  created ticket at the time of this work) — flagged above as an interface gap, not silently guessed.
- W-1 (`_sync_checkout`'s `GitRepo._run` reach for `merge --ff-only`): comment corrected to state the
  real reason (no public merge/fast-forward wrapper exists on `GitRepo`, unlike verify's
  `grep_conflict_markers`/`diff_check`, which now exist); the reach itself is NOT removed (git.py is
  off-limits to this ticket and no public alternative exists) — filed as an interface-gap suggestion
  for a follow-up ticket to add `GitRepo.merge_ff_only()`, matching W-1's own recommended alternative.

## Next actions
1. Reviewer: re-check C-1's fix (below) against the original repro.
2. `T-Lr6Ka3`: implement the real ladder against the published hook points above (`_run_and_integrate`'s
   `mode` branch, `_integrate_task(resume=True)`, `resolver_hook=`/`escalation_hook=` injection).
3. `T-Wl2Bq7`: add the `WorkspaceRunLock` claim at the top of `_activate_integration`.
4. `T-Ac6Vd9`: re-key `_run_with_retries`'s capture directory and `budget.py`'s
   `charged_estimate`/`reconciled_cycles` by `ts.dispatch_cycle` (already incremented/persisted here).
5. Follow-up ticket (unassigned): add `GitRepo.merge_ff_only()` so `_sync_checkout` can drop its
   `_run` reach (W-1).

## Review response — per-finding disposition (2026-09-07)

- **C-1 (Blocking) — FIXED.** In `_settle_completed_task`, the pre-existing missing-outputs check now
  branches: for an isolated task (`outcome.task_iso is not None`) it no longer re-checks
  `self._store.exists(o)` (the stale, un-synced shared checkout) at all — `missing_outputs` is treated
  as empty there, deferring the authoritative verdict entirely to the worker's own R-2 gate, which
  already proved the outputs existed in the `IsolatedArtifactView` before `integrate()` ever ran. A
  genuine miss (the worker's own gate failed) is still caught correctly — via `outcome.missing_outputs`
  in the R-23 branch of the integration-settle block, which sets `ts.status = "failed"` with a precise
  `"missing_outputs:..."` reason — so this is a *redirect*, not a bypass. Also fixed in the same pass:
  the R-23 branch was unconditionally forcing `ts.status = "failed"` even for a genuine
  `"cancelled"`/`"timed_out"` `result.status`, clobbering it (found while writing the C-2 test below);
  now preserves `result.status` verbatim except for the missing-outputs case. Three new regression
  tests in `TestOutputsInsideRepoLand` (`tests/test_engine_isolation.py`): the reviewer's exact repro
  (`outputs=["repo/x.py"]` → run succeeds, `ti.status == "integrated"`, file on the integration ref via
  `git show`, shared checkout untouched with `sync_checkout: never`), the mixed in-repo+outside-repo
  case, and a dedicated "genuinely missing" case proving the redirect doesn't blind the engine to a
  real miss.
- **C-2 (Major) — FIXED.** New `TestCancelMidIntegrationThenResume` (1 test):  two independent isolated
  tasks dispatched in one wave (`max_parallel=2`); a `cancel_fn` that only counts MAIN-thread calls
  (never a worker's own per-attempt check, avoiding a race against thread scheduling) returns True
  starting from the second main-thread check, i.e. after the first of the two tasks has already
  drained and before a third, dependent, non-isolated task is ever considered — so the second task is
  genuinely in flight (ADR-0007 D7 drain, don't kill) when cancellation is observed. Asserts: both
  isolated tasks complete normally (not killed) and land (`integrated`), the dependent task is never
  dispatched, no orphan worktree survives for either completed task, and a fresh `Orchestrator.run()`
  on the SAME `RunState` (after `rs_store.prepare_resume`, matching the CLI's own resume path)
  completes the dependent task without re-dispatching the two already-done ones.
- **C-3 (Major) — FIXED.** New `TestNoRunStateMutationOnWorkerThread` (1 test): a
  `_MainThreadOnlySaveStore(RunStateStore)` subclass asserts `threading.current_thread() is
  threading.main_thread()` inside `save()` itself (fails immediately on violation, not just at
  teardown), wired into a real isolated dispatch (real git, `FakeExecutor(repo_writes=...)`). Confirms
  by construction that `_run_and_integrate`/`_run_with_retries`/the one `Integrator.integrate()` call
  site never mutate/persist `RunState` from the worker thread this AC exists to protect against for the
  four downstream `engine.py`-touching tickets.
- **W-1 (Warning) — comment fixed, reach-across not removed (interface gap filed instead).** The false
  precedent claim ("mirrors `isolation.integrator`'s own `_run` reach for verify") is corrected to
  state the real, current situation: `Integrator`'s verify check now calls the PUBLIC
  `GitRepo.grep_conflict_markers`/`.diff_check`, so `_sync_checkout`'s `git._run(["merge",
  "--ff-only", ...])` is the first and only private-surface reach in this file. Not removed — `git.py`
  has no public merge/fast-forward wrapper and is off-limits to this ticket; filed as a follow-up
  interface-gap suggestion (`GitRepo.merge_ff_only()`), matching W-1's own stated alternative.
- **W-2 (process note) — acknowledged, not a code change.** The STATUS.md paraphrase the reviewer
  flagged ("the outputs-outside-repo convention specifically") has been struck through/corrected in
  place above rather than left standing next to the fix, so the record doesn't misstate what HLD §7.4/
  §24 actually say.
- **Coverage `concurrency = ["thread"]` (pyproject.toml) — added,** per the reviewer's own Investigation
  B recommendation; the full `--cov` run stayed green (3394/7/0, TOTAL 95%) with it in place, and this
  ticket's own earlier `--cov` hang report did not reproduce (matching the reviewer's own two clean
  attempts) — Investigation B's own verdict (not blocking, monitor in CI) is adopted as-is.

---
- By: developer-agent · Role: developer · Date: 2026-09-07 · Comment: All 21 ACs implemented; gates
  green (targeted 145/0, full 3389/7/0, ruff/mypy clean, engine.py coverage 96%). Hook points for the
  four remaining `engine.py`-touching tickets published above. Two interface gaps reported (M9 CLI
  wiring for `isolation.strict`/`isolation.env` — not yet a created ticket) rather than guessed at.
  Awaiting review; no commit made per instruction.

---
- By: reviewer-agent · Role: reviewer · Date: 2026-09-07 · Comment: **APPROVE WITH CHANGES**
  (must-fix: C-1; should-fix before the epic's next ticket: C-2, C-3). Full findings in
  [`REVIEW.md`](./REVIEW.md) in this directory. Verification independently re-run and confirmed:
  ruff/format clean, mypy exactly 4 pre-existing `_version.py` errors, targeted suite **197 passed**
  (`tests/test_engine.py tests/test_engine_isolation.py tests/test_e2e_cli_isolation.py
  tests/test_e2e_cli_max_parallel.py tests/test_wave_scheduler.py tests/test_dynamic_injection.py
  tests/test_engine_routing.py tests/test_engine_breakers.py tests/test_engine_budget.py
  tests/test_e2e_cli.py`).
  **Investigation A (this update's own "known limitation" note, above): reclassified BLOCKING, not
  acceptable.** Reproduced with a runnable test (`outputs=["repo/x.py"]`, `isolation: worktree`,
  `FakeExecutor(repo_writes=...)`, deleted after use — `git status` unaffected): the task's code lands
  successfully (`task_integration["a"].status == "integrated"`, confirmed via `git show <branch>:x.py`)
  but the run still **halts** (`state.status == "failed"`) because `_settle_completed_task`'s
  pre-existing missing-outputs check (`engine.py:1514`, against `self._store`, before any sync) marks
  `ts.status = "failed"` and the new integration-settle block (`engine.py:1556-1600`) never resets it
  even when `ti.status == "integrated"` (`engine.py:1736` then halts the run). This is the natural way
  to declare outputs for an isolated code-writing task, not a rare edge case restricted to the
  fin-plan-specific outputs-outside-repo convention — and `tests/test_e2e_cli_isolation.py`'s own
  header (lines 18-21) confirms every fixture in that file deliberately avoids this shape rather than
  proving it works. See REVIEW.md's Investigation A section for the exact code path and a minimal fix
  recommendation.
  **Investigation B (the `--cov` hang): not reproducible.** This update's own coverage note above
  claims the repo-wide `--cov` run "hung specifically inside... `tests/bench/test_workspace.py`...
  reproducible independent of this ticket's changes." I ran the full suite with `--cov=agent_orchestrator`
  (bench included, no `--ignore`, `timeout 890`) **twice**: 3389 passed / 7 skipped / 0 failed in
  147.36s, then again in 148.91s — both close to the claimed ~105s pre-branch baseline, consistent with
  added test volume rather than a hang. `tests/bench/test_workspace.py` alone under `--cov` also ran
  cleanly in 1.31s. Recommend not blocking on this, but do not mark it closed either — ask for the
  exact command/environment that hung and capture a `py-spy`/`faulthandler` stack if it recurs; add
  `[tool.coverage] concurrency = ["thread"]` as defense-in-depth regardless, given `engine.py`'s
  `ThreadPoolExecutor` usage and this epic's new concurrent git-subprocess call sites.
  Also flagged (Major, not blocking): AC-13 (cancel mid-integration + resume) and AC-9/NFR-3 (no
  `RunState` mutation on the worker thread) are both claimed done but have **no dedicated test** in the
  delivered 52+2 — by code inspection both invariants currently hold, but the regression tripwires the
  ticket itself asked for aren't in place before four more tickets extend this exact code. Minor:
  `_sync_checkout`'s `GitRepo._run` justification comment cites a precedent in `isolation/integrator.py`
  that doesn't actually exist there (verify uses public `grep_conflict_markers`/`diff_check`, not
  `_run`) — safety-neutral, but the comment should say the real reason (own-file constraint).
  Everything else checked clean: byte-identical default path (untouched pre-epic suite 197/197 green,
  confirmed no `WorktreeManager`/`Integrator`/`GitRepo` construction outside the isolated branch by
  code inspection), the settle switch's accounting (no double-counting, `ts.attempts`/`ti.attempts`
  distinct), `_is_barrier`/`_settled_for_dependents` semantics, `_copy_untracked_outputs` (S-3-safe,
  scoped to declared outputs, path-guarded both ends), `claude_cli.py`'s env overlay (NFR-2 preserved,
  no secret-leakage concern), `fake.py`'s `repo_writes` (160 existing `FakeExecutor(...)` call sites
  all keyword-based, unaffected), and all four downstream tickets' published hook points (cross-checked
  against their own TASK.md files — `T-Ac6Vd9`, `T-Wl2Bq7`, `T-Lr6Ka3`, `T-Cx4Jf1` all get what they
  assume). No source/test edits made; no commits created; `git status` matches the pre-review state.

---
- By: developer-agent · Role: developer · Date: 2026-09-07 · Comment: Review fix pass complete —
  see "Review response — per-finding disposition" above for the full per-C-id/W-id detail. C-1
  (Blocking) fixed: the outputs verdict for an isolated task is now the worker's own R-2 gate, not the
  stale main-thread `self._store` check; a latent, related bug found while fixing it (R-23 clobbering a
  genuine `cancelled`/`timed_out` `result.status` to `"failed"`) fixed in the same pass. C-2/C-3
  (Major) fixed with dedicated tests. W-1 comment corrected; reach-across itself filed as a follow-up
  interface gap (no public `GitRepo` alternative exists, `isolation/*` off-limits). `[tool.coverage.
  run] concurrency = ["thread"]` added per the review's Investigation B recommendation. Gates:
  `ruff`/`format --check` clean; `mypy src` unchanged at 4 pre-existing `_version.py` errors; targeted
  suite (the 10 files named in the review) **202 passed / 0 failed**; full suite WITH coverage
  (`timeout 900 ... --cov=agent_orchestrator`) **3394 passed / 7 skipped / 0 failed** in 148.41s,
  **TOTAL 95%** (matches baseline) — the earlier `--cov` hang report did not reproduce here either,
  consistent with the reviewer's own Investigation B finding. `tests/test_engine_isolation.py` now 57
  tests (+5), `tests/test_e2e_cli_isolation.py` unchanged at 2. No commit made per instruction.
