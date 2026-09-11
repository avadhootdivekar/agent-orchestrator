# Epic: E-Wk9Tz3-task-isolation

## Metadata
- Epic ID: `E-Wk9Tz3-task-isolation`
- Title: Per-task git worktree isolation with squash+rebase integration and soft overlap-aware task assignment
- Owner: architect (agent) — implementation owner TBD
- Created: 2026-09-06
- Last Updated: 2026-09-07
- Status: Implementation complete, epic closing (2026-09-07). 11 of 14 tasks merged; `T-Cx4Jf1` Part B
  (observability) and `T-Ee3Mn8` (e2e + review gates) in flight, plus an as-built security remediation
  pass. Design reconciled against the shipped code by `T-Dr5Yq6` — see
  [`task-isolation-hld.md`](../task-isolation-hld.md) §25.
- Origin ask: user request, flagged as the **highest-priority** epic. The core mechanism (worktree per
  task, squash → rebase → verify → fast-forward, soft `touches` hints, the tiered conflict ladder) was
  specified by the user and is recorded in the HLD/ADR rather than re-derived here.
- Mirror: [`meta/tickets/E-Wk9Tz3-task-isolation/EPIC.md`](../../meta/tickets/E-Wk9Tz3-task-isolation/EPIC.md)
  (task-level tracking lives there; this file is the running narrative + evidence log).

## Goal
Close the write-conflict gap [ADR-0007](../adr/ADR-0007-parallel-task-execution.md) accepted and
[`meta/ROADMAP.md`](../../meta/ROADMAP.md) §3.4/§4 carries:

- each `isolation: worktree` task runs in its own git worktree on branch `ao/<run_id>/<task_id>`,
  based at the integration head **at dispatch time**;
- results land on an ao-owned integration ref by **squash → rebase → verify → compare-and-swap
  fast-forward**, serialized per repository;
- conflicts are repaired by a cost-ordered ladder — free git auto-merge, free mechanical resolvers
  (`rerere` / union / regenerate), one bounded LLM merge-resolver, one re-run on the fresh base, then
  the operator;
- task assignment *prefers* co-scheduling non-overlapping tasks using **soft** `touches` hints and
  hotspot data, and **never** withholds a slot because of overlap;
- everything is opt-in, and non-git workspaces degrade rather than fail.

Full design: [`docs-md/task-isolation-hld.md`](../task-isolation-hld.md).
Decision record: [`docs-md/adr/ADR-0013-per-task-git-isolation-and-rebase-integration.md`](../adr/ADR-0013-per-task-git-isolation-and-rebase-integration.md).

## Why this is the top priority
Not "conflicts are annoying" — **the missing isolation is being paid for in lost parallelism.** In the
primary consumer (`../ao-runner-finplan`, read-only reference), the breakdown agent adds cross-task
`depends_on` edges purely to avoid file collisions: 14 of 15 fan-out tasks in one epic, 15 of 20 in
another, with `tasks.md` stating outright that a phase ordering exists only because those tasks "touch
overlapping core Rust files". A live run also produced real contention on
`src/server/fin_rust/src/apis/accounts.rs` (23 lifetime commits, 18 in six months — a genuine top-10
hotspot), survived only by a self-heal `git stash` that the same repo's own
`PARALLEL_DEVELOPMENT_GUIDELINES.md` §6.4 forbids. Isolation lets decomposition stop hedging.

## Key design decisions (locked; full rationale in ADR-0013)
- **D1** Worktree per task per **git repository** (several `RepoRef`s inside one repo share one
  worktree — the consumer's `core=./fin_plan` + `docs=./fin_plan/docs-md` shape), branch
  `ao/<run_id>/<task_id>`, based at the integration head at dispatch. Worktrees live **outside every
  working tree** under `$AO_STATE_DIR/worktrees/...` so agents never search N duplicate source trees.
- **D2** Opt-in per workflow and per task; non-git or git < 2.30 degrades to `isolation: none` with a
  warning (`isolation.strict: true` opts into hard failure). Structural tasks (`emit_tasks`, router,
  loop gate) are forced to `none` — they are already barriers and usually need the shared checkout.
- **D3** Squash to one commit (`git commit-tree`), rebase onto the integration head, verify, then land
  with an **atomic `git update-ref` compare-and-swap** under a per-repo lock (thread + `flock`).
  Merge-commit integration was evaluated and reserved in the schema, not shipped.
- **D4** The integration branch is an **ao-owned ref that is never checked out**, so a ref move can
  never fail on a dirty tree — decisive, because the consumer's checkout currently shows 4106
  `status --porcelain` entries and their `git-branch-off` agent aborts on dirt. It is created
  **lazily at the first isolated dispatch**, so it inherits whatever branch the workflow's own git
  stage established.
- **D5** A dependent dispatches only after its predecessors are **integrated**, and `should_skip`
  requires integration too — without that rule a resumed run would skip a task whose artifact exists
  but whose code never landed (the consumer sets `skip_if_outputs_exist: true` on every fan-out entry
  and keeps artifacts outside the git repo, so this is a live hazard, not a theoretical one).
- **D6** The conflict ladder T0→T4, with T2/T3 implemented as extra **attempts of the same task** via
  the existing requeue path — so budget gating, cumulative cost, and the `task_cost_usd` /
  `run_cost_usd` breakers bound conflict spending with zero new plumbing, and no DAG reshape stalls
  the wave. Every integration also runs a **verify** step on the post-rebase tree (the merge-queue
  lesson); the default verify is free and structural.
- **D7** `touches` is advisory. `rank_wave` is pure and deterministic, is a provable no-op at
  `max_parallel == 1`, and always fills every slot. Separately: isolation isolates **source, not build
  caches** — `isolation.env` ships a shared `CARGO_TARGET_DIR` recipe (cargo file-locks its target
  dir, so sharing serializes rather than corrupts).

## Landscape, in one line each
Claude Code worktree mode and Cursor background agents both give branch-per-agent isolation and stop
there — integration is a human PR. OpenHands/Devin-style agents prove that a bounded LLM conflict
resolution is a normal action. CI merge queues (Bors, GitHub, Zuul) supply the landing discipline:
serialize the landing, verify the **post-merge** tree, and define an ejection policy. **The
differentiator is the repair loop**: we attempt a bounded, budgeted repair before ejecting, inside the
run, instead of waiting for a human.

## Decomposition
**14 tasks / 33 developer-days / 3 sprints** (after the 2026-09-07 review gates; was 12 / 28 / 2).
Each is <= 3 days with a disjoint file-ownership boundary. `engine.py` is the one shared file, edited
by five tasks in a fixed order — `T-En8Hd4` -> `T-Ac6Vd9` -> `T-Wl2Bq7` -> `T-Lr6Ka3` -> `T-Cx4Jf1` —
each narrowly scoped and each required to read the merged file rather than re-derive from the design.
`cli.py` is split explicitly between `T-Ov9Bt5` (`ao hotspots`) and `T-Cx4Jf1` (`--isolation`,
`ao prune`).

| Task | Owns | Days | Requirements |
|---|---|---|---|
| `T-Gt4Pw8-git-porcelain` | `isolation/git.py` + the shared git test fixtures | 2 | FR-2, FR-6, NFR-4 |
| `T-Sc7Rm2-isolation-schema-models` | `models.py`, `spec.py`, `workflow.schema.json`, `runstate.py` (state only) | 2 | FR-1, NFR-5 |
| `T-Wk3Nv6-worktree-lifecycle` | `isolation/paths.py`, `worktrees.py`, `view.py`, shared `xdg.py` | 3 | FR-2, FR-3, FR-4, FR-14 |
| `T-Ib5Qy9-integrator-core` | `isolation/integrator.py`, `isolation/locks.py` | 3 | FR-5, FR-6, FR-8 |
| `T-En8Hd4-engine-isolation-wiring` | `engine.py`, `artifacts.py`, `runstate.py`, `executors/` | 3 | FR-4, FR-9, FR-12, FR-13, NFR-2, NFR-3 |
| `T-Rm2Lx7-mechanical-resolvers` | `isolation/resolvers.py` | 2.5 | FR-7 (T1) |
| `T-Lr6Ka3-llm-resolver-and-rerun` | `isolation/escalation.py`, `merge-resolve.md` | 3 | FR-7 (T2-T4), FR-8 |
| `T-Ac6Vd9-requeue-accounting` *(new)* | `budget.py`, narrow `engine.py` settle/cycle edits | 2 | FR-7 cost bound (R-1, R-21) |
| `T-Wl2Bq7-workspace-run-lock` *(new)* | `isolation/runlock.py`, narrow `engine.py` sync edits | 1.5 | FR-13 (R-4, R-12) |
| `T-Ov9Bt5-overlap-scheduling-hotspots` | `scheduling/overlap.py`, `isolation/hotspots.py`, `ao hotspots` | 2.5 | FR-10, FR-11 |
| `T-Cx4Jf1-cli-config-prune-observability` | `cli.py`, `project_config.py`, events, `status.json`, dashboard column | 2.5 | FR-14, FR-15 |
| `T-Tp7Zs2-instructions-and-templates` | conflict-friendly rules + `routed-runner` wiring + push-directive removal | 2 | FR-16, NFR-6 |
| `T-Ee3Mn8-e2e-and-review` | tests only + security/review passes | 3 | all, esp. NFR-1/2/3 |
| `T-Dr5Yq6-docs-refresh` | `docs-md/` reconciliation + ticket sync | 1 | post-implementation |

**Sprint plan.** Three 2-week sprints, team size 3, 40% overhead:
`Gross = 3*10*8 = 240 h` → `Net = 144 h` → `Commitment = 100.8-122.4 h` = **12.6-15.3 dev-days/sprint**.
Sprint 1 (foundation + core integration) = **13.0 d**; Sprint 2 (ladder, accounting, multi-run,
scheduling) = **13.5 d**; Sprint 3 (surface, verification, docs) = **6.5 d** — deliberately under the
floor, as the remediation budget for a late gate covering five tasks' worth of `engine.py` edits plus
a full security-implementation pass. Total **33 d** against 37.8-45.9 available.

## Acceptance Criteria (testable)
See the epic ticket's FR-1..FR-16 / NFR-1..NFR-6 table for the traceable list, and HLD §17.4 for the
row-by-row verification matrix. The gates that decide the epic:

1. **NFR-2 (blocking):** the pre-epic engine suite passes **unedited** at defaults
   (`max_parallel=1`, no `isolation` key) — any edit to a pre-existing engine test fails the gate.
2. Three tasks land concurrently from three worktrees onto one integration branch, verified via
   `CliRunner` with `max_parallel: 3`, with the final tree containing all three changes.
3. The full ladder is exercised end to end: T1 union resolution, T2 resolver dispatch (with its cost
   showing up in the task's cumulative spend), T3 rerun after a verify failure, T4 → retained
   worktree → manual fix → `ao resume` completes.
4. `should_skip` refuses to skip an isolated task whose artifact exists but whose integration failed.
5. Security pass over the artifact path-guard widening: traversal and symlink escapes still raise,
   the widening is a per-task wrapper (`isolation/view.py`) rather than a widened shared store, its
   roots are producer-restricted, and `RunStateStore` is never wrapped.
6. NFR-1 audit: no module under `isolation/` reads a repository file's contents; the conflict manifest
   handed to an agent contains only ids, paths and refs.

## Evidence Log
- 2026-09-06 — **Design package delivered (this change).** `docs-md/task-isolation-hld.md` (1874
  lines: requirements, landscape survey, HLD, LLD for 11 modules with pseudocode/interfaces/edge
  cases, schema deltas, mermaid block/state/sequence diagrams for the happy path and every conflict
  tier, resume/crash/cancel paths, security analysis, test plan, acceptance matrix, readiness gate,
  risks, open questions), `docs-md/adr/ADR-0013-...md` (269 lines: 7 decisions, alternatives,
  consequences), this page, the epic ticket and 12 task tickets. **No code changed; no commits.**
- 2026-09-06 — Design validated against the real code (`engine.py` @ 2204 lines, `models.py`,
  `artifacts.py`, `runstate.py`, `cli.py`, `specs/*.schema.json`, `templates/builtin/routed-runner/`)
  and against the real consumer's specs, agents, breakdown contract, git history and
  parallel-development guidelines. Four field findings changed a design decision rather than being
  noted and ignored: the chronically dirty checkout → D4 (ref-based landing, never a working-tree
  merge); the 105 GB `target/` vs ~18-21 GB free disk → D7 (worktrees copy tracked files only; the
  cost is a cold rebuild, addressed with a shared-cache recipe); `skip_if_outputs_exist: true` on
  every fan-out entry with artifacts outside the repo → D5 (integration-aware `should_skip`); the
  already-leaked `worktree-agent-*` branches and a stray `.worktrees/full-test-*` → FR-14 (GC +
  `ao prune --worktrees-only`).

- 2026-09-07 — **Implementation landed.** Eleven task commits on `ad/task-isolation`, in the fixed
  `engine.py` edit order the design required: `T-Gt4Pw8` (git porcelain) -> `T-Sc7Rm2` (schema/models)
  -> `T-Wk3Nv6` (worktree lifecycle, paths, per-task artifact view) -> `T-Ov9Bt5` (overlap ranking +
  hotspots, plus a follow-up batching fix) -> `T-Tp7Zs2` (instructions/templates) -> `T-Ib5Qy9`
  (integrator core) -> `T-En8Hd4` (engine wiring) -> `T-Ac6Vd9` (requeue accounting) -> `T-Cx4Jf1`
  Part A (CLI/config/prune) -> `T-Rm2Lx7` (mechanical resolvers) -> `T-Wl2Bq7` (workspace run lock,
  safe checkout sync) -> `T-Lr6Ka3` (LLM resolver + rerun). Every one carried a code review; ten
  reviews returned APPROVE WITH CHANGES and one (`T-Lr6Ka3`) returned REWORK first.
- 2026-09-07 — **Live-run gate.** The shipped CLI was driven against a throwaway repository with two
  parallel isolated tasks, a real `claude_cli` agent, and the T3 rerun path exercised. Headline result:
  **the feature works end to end on a real run.** Three defects surfaced that no unit test had caught —
  `tier_counts` never incremented, `tier_reached`/`conflicted_count` overwritten by a clean final
  attempt, and `ao prune` leaking every `ao/` ref of a fully successful run. All three routed to
  `T-Cx4Jf1` Part B rather than absorbed.
- 2026-09-07 — **As-built security audit** (`REVIEW-security-asbuilt-2026-09-07.md`). Verdict: the
  architecture is sound and most design-time controls landed correctly — S-1 (hook suppression), S-4
  (the per-task `IsolatedArtifactView`), S-6, S-9, NFR-1, the CAS/lock discipline and the ref/path
  sanitizer were each verified present and working, several by execution. **The exception is the one
  control the design itself called blocking**: S-2, structural containment of the T2 LLM resolver, is
  ineffective in the shipped code — both halves fail. That is in remediation, not accepted. One finding
  (**M-1**) is accepted as a documented limitation: engine git calls suppress every repository **hook**
  (proven — 8 hook invocations from a plain `git commit`, 0 from the engine) but do **not** neutralize
  `filter`/`merge` drivers reached through git attributes, so a bootstrapped repo's configured filter
  runs once per task, unattended, across worktrees. Documented for adopters in
  `conflict-friendly-coding.md` and HLD §14. A second finding (**M-3**) produced a new hard guard: a
  worktree root that overlaps the workspace root is now refused with a `ConfigError`, because that
  layout silently defeats the per-task artifact containment.
- 2026-09-07 — **Docs reconciliation (`T-Dr5Yq6`, this entry).** Every factual claim in
  `task-isolation-hld.md` re-verified against merged code or `--help` output rather than against the
  tickets that claimed it. Ten as-built deviations recorded in a new HLD §25; **six** §24 review-finding
  dispositions changed (R-2 and R-3 fixed by a different mechanism than the row described — R-2's stated
  rationale was demonstrably false and shipped as a blocking defect; S-1 and R-12 fixed with a named
  residual; S-5 and S-7 downgraded to partially-shipped because their observability half is unshipped).
  Two operator procedures were **executed**, not described: `ao prune --worktrees-only` against a real
  temp repository (reaps the worktree, its registration and its `ao/` branch; `--dry-run` names both the
  path and the ref), and the T4 -> resume recovery path, which **did not work as documented** — see
  Risks below. ADR-0013 moved to `Accepted / shipped`.

## Requirement traceability

FR/NFR -> the module that implements it -> its dedicated tests. Test files marked *(in flight)* were
uncommitted working-tree files owned by `T-Ee3Mn8` when this table was written; they are named because
they are the dedicated coverage, not because their results are being claimed.

| Req | Landed in | Dedicated tests |
|---|---|---|
| **FR-1** task/workflow `isolation`, backward compatible | `models.py` (`IsolationMode`, `WorkflowIsolation`, `resolve_task_isolation`), `spec.py` (V1-V13), `specs/workflow.schema.json` | `tests/test_isolation_models.py`, `tests/test_isolation_spec_validation.py`, `tests/test_nfr2_regression_gate.py` *(in flight)* |
| **FR-2** worktree at dispatch from the integration head, outside every working tree | `isolation/paths.py`, `isolation/worktrees.py` | `tests/isolation/test_paths.py`, `tests/isolation/test_worktrees.py` |
| **FR-3** several `RepoRef`s in one repo share one worktree | `isolation/worktrees.py::group_repos` | `tests/isolation/test_worktrees.py` |
| **FR-4** `effective_path` remap of every handed-out path | `isolation/paths.py::effective_path`, `isolation/view.py`, `engine.py::_run_with_retries(store=...)` | `tests/isolation/test_paths.py`, `tests/isolation/test_view.py`, `tests/test_engine_isolation.py` |
| **FR-5** engine auto-commit, ids/paths only | `isolation/integrator.py::_auto_commit_and_screen` | `tests/isolation/test_integrator.py` |
| **FR-6** squash -> rebase -> verify -> CAS, per-repo lock | `isolation/integrator.py`, `isolation/locks.py`, `isolation/git.py::update_ref_cas` | `tests/isolation/test_integrator.py`, `tests/isolation/test_locks.py`, `tests/isolation/test_git.py` |
| **FR-7** the T0-T4 ladder | `isolation/resolvers.py` (T1), `isolation/escalation.py` (T2/T3/T4), `isolation/integrator.py::materialize_conflict` | `tests/isolation/test_resolvers.py`, `tests/isolation/test_escalation.py`, `tests/isolation/test_ladder_e2e.py` *(in flight)*, `tests/isolation/test_conflict_fixtures.py` *(in flight)* |
| **FR-8** verify before landing | `isolation/integrator.py::_run_verify*`, `isolation/git.py::grep_conflict_markers`/`diff_check` | `tests/isolation/test_integrator.py`, `tests/isolation/test_git.py` |
| **FR-9** dependents wait for *integrated* | `engine.py::_settled_for_dependents` | `tests/test_engine_isolation.py`, `tests/test_e2e_isolation.py` *(in flight)* |
| **FR-10** `touches` is a soft hint, never a gate | `scheduling/overlap.py::rank_wave` + the `engine.py` wave-fill call site | `tests/test_overlap_ranking.py`, `tests/test_engine_isolation.py` |
| **FR-11** `ao hotspots` | `isolation/hotspots.py`, `cli.py` | `tests/test_hotspots.py`, `tests/test_e2e_cli_hotspots.py` |
| **FR-12** non-git / old git degrades (or fails under `strict`) | `engine.py::_activate_integration`, `isolation/worktrees.py`, `project_config.py` | `tests/test_e2e_isolation.py` *(in flight)*; verified by execution during this pass |
| **FR-13** non-isolated tasks are barriers; checkout fast-forwarded on demand | `engine.py::_is_barrier`/`_sync_checkout`, `isolation/git.py::fast_forward_checkout`, `isolation/runlock.py` | `tests/isolation/test_runlock.py`, `tests/test_e2e_isolation.py` *(in flight)* |
| **FR-14** worktrees/branches/refs are GC'd | `isolation/worktrees.py` (`release`/`reconcile`/`gc_run`), `cli.py` (`ao prune`) | `tests/isolation/test_worktrees.py`, `tests/test_e2e_cli_prune_worktrees.py`; **executed** during this pass |
| **FR-15** structured events / state / `status.json` traceability | `engine.py`, `isolation/*`, `runstate.py::write_status` | `tests/test_isolation_events.py` *(in flight)*, `tests/ui/test_runs_integration_surface.py` *(in flight)* — **partially unshipped, see Risks** |
| **FR-16** conflict-friendly instructions + `routed-runner` wiring | `templates/instructions/conflict-friendly-coding.md`, `templates/builtin/instructions/merge-resolve.md`, `templates/builtin/routed-runner/` | template render + push-directive invariant tests in `tests/` |
| **NFR-1** core never reads artifact contents | `isolation/*` (git subprocesses + temp files only); `conflict-<n>.json` is ids/paths/refs | audited by the 2026-09-07 security pass; `tests/isolation/test_escalation.py` |
| **NFR-2** `max_parallel == 1` + `isolation: none` byte-identical | the whole opt-in construction | `tests/test_nfr2_regression_gate.py` *(in flight)* — the blocking gate |
| **NFR-3** single-writer `RunState` | `engine.py` (workers return `WorkerOutcome`; only the main thread mutates/saves) | `tests/test_engine_isolation.py::TestNoRunStateMutationOnWorkerThread` |
| **NFR-4** every git op bounded and re-attemptable | `isolation/git.py` (timeouts), `Integrator` (idempotent squash/rebase/CAS) | `tests/isolation/test_git.py`, `tests/isolation/test_integrator.py` |
| **NFR-5** additive schema/state only | `models.py`, `runstate.py` | `tests/test_isolation_models.py`, `tests/isolation/test_service_paths_migration.py` |
| **NFR-6** bounded, understood disk cost | design + docs (`isolation.env` recipe, HLD §16) | not test-covered by construction; **the first-adoption measurement is outstanding** |

## Review gates (2026-09-07)

Two pre-implementation gates ran before any code was written — reviewer
(`REVIEW-design-2026-09-07.md`, **APPROVE WITH CHANGES**: 6 Blocking, 11 Major, 7 Minor) and
dev-security (`REVIEW-security-design-2026-09-07.md`, **conditional pass**: 2 Blocking, 3 Major,
3 Minor, 3 Info), both in the epic ticket folder. **No ADR-0013 decision (D1-D8) was overturned** — by
those gates, by the implementation, or by the later as-built security audit.

The findings that changed the design rather than merely tightening it:

- **R-19 (the one that mattered).** `_run_with_retries` computes six of the seven remappable path
  categories *inside itself*, from the engine's single shared store — so as originally pseudocoded an
  "isolated" task would still have read and written the **shared checkout** while ao dutifully created
  worktrees nothing used, with every other test passing. Now `T-En8Hd4`'s first acceptance criterion,
  asserted on the real `TaskContext` a `fake` executor receives.
- **R-4 → ADR-0013 D8.** `meta/ROADMAP.md` §3.4 and ADR-0014 both defer the multi-run policy *to this
  epic*, and the design was silent. Landing was already safe (per-repo lock + CAS); D5's checkout
  fast-forward was not. Answer: a per-workspace `WorkspaceRunLock`, `workspace_lock: "require"` by
  default, second run degrades to `isolation: none`. `E-Sc9Rt4` needs no change.
- **R-1 / R-21.** The ladder's "cost is bounded for free" claim was not true: actuals are discarded
  across a requeue, and the token ledger latches one-shot per task id — the same bug classes this repo
  already fixed for in-call retries (E-9h3m7k) and for self-heal's cross-call redispatch. Split into
  the new `T-Ac6Vd9`, which keys both the ledger and the transcript capture by dispatch cycle.
- **S-1 / S-2 / S-3.** Three places where **the engine**, not a workflow author, is the actor and the
  containment was advisory rather than structural: repo hooks firing unattended ~100 times per run (now
  suppressed at the single git choke point), the T2 resolver's tool access (now force-injected, not
  prompted), and the engine's unconditional `git add -A` (now screened against a secret denylist).
- **R-10.** ADR-0013 presented as a verbatim ADR-0007 quotation a sentence that is actually
  `meta/ROADMAP.md` §4's. Corrected in both documents; the substance was unaffected.

Per-finding dispositions — fixed / accepted-with-rationale / deferred, each with the location of the
fix — are recorded once, in [`task-isolation-hld.md`](../task-isolation-hld.md) §24.
`T-Dr5Yq6-docs-refresh` re-verifies every *Fixed* row against merged code before the epic closes.

## Risks & Blockers
- **R1 cold rebuilds** for heavy toolchains — mitigated by `isolation.env` (shared, cargo-locked
  target dir), by leaving heavy stages `isolation: none` (they are barriers anyway), and by per-repo
  opt-in. Documented, not solved; measure on first adoption.
- **R2 a plausible-but-wrong LLM merge** — verify runs after resolution, T2 is capped at one attempt,
  and one commit per task keeps the result reviewable.
- **R3 integration as the serial bottleneck** — the default verify is free; measure
  `integration.merged duration_ms`; speculative integration is a named non-MVP follow-on.
- **R4 the artifact path-guard widening** — `T-Ee3Mn8` carries a mandatory security pass over exactly
  that diff.
- **Concurrency**: a scheduler/cron epic owns `docs-md/scheduler-triggers-hld.md` and ADR-0014 on this
  branch. This epic touches neither, and does not edit `CLAUDE.md`, existing ADRs, or
  `../ao-runner-finplan`. `meta/ROADMAP.md` and `meta/learnings*.md` were explicitly re-scoped **into**
  this epic at closing time, for `T-Dr5Yq6` — the original "do not edit" boundary applied to the
  design and implementation phases, not to the reconciliation that closes the epic.
- Not blocked. Five user decisions are recorded with recommended defaults already applied (HLD §20);
  the integration target and the default-verify behaviour should be confirmed before
  `T-Ib5Qy9-integrator-core` merges, since they are the hardest to reverse.

## Open at closing time

These are the things a reader should not assume are done.

1. **S-2 resolver containment is ineffective as shipped** and is in remediation. Until it lands, a T2
   merge-resolver dispatch does not have the tool/push containment the design calls blocking.
2. **`ao resume` after a T4 failure discards an operator's hand-resolution** — `TaskIntegrationState.mode`
   is never reset to `"normal"` on T4, so the resume redispatches in `"rerun"` mode and hard-resets the
   retained worktree. Verified by executing `prepare_resume`. A code defect, not a docs gap; the
   one-line fix and the two recoveries that do work today are in HLD §12.2 / §25 D-7.
3. **`ao prune` leaks the `ao/` refs of a fully successful run** (discovery probes worktree directories
   the engine has already removed). `T-Cx4Jf1` Part B.
4. **Observability is half-shipped**: `tier_counts` is never incremented, a conflicted-then-rerun task
   reports `tier_reached: "auto"`, and `worktree.retention_high` plus the dashboard integration column
   do not exist. `T-Cx4Jf1` Part B. HLD §11 M9's event list is deliberately **not** reconciled yet for
   the same reason.
5. **Stale text in user-visible code**: `models.py` and `specs/workflow.schema.json` still describe
   `integration.workspace_lock` as "reserved … this ticket implements no behaviour for it". `T-Wl2Bq7`
   implemented it; the schema description ships to every user who reads the spec.
6. **HLD §20 decisions 1 and 3** (integration target, default verify) were to be confirmed before
   `T-Ib5Qy9` merged. It merged without a recorded confirmation, so they stand as implemented-by-default
   rather than explicitly ratified.
7. **The R-12 first-adoption measurement** against the consumer's real dirty-file set (`T-Ee3Mn8` AC-19)
   has not been performed, so the predicted collision rate at the first barrier is still a prediction.

## Next actions
1. Land the security remediation (S-2 containment) and `T-Cx4Jf1` Part B; then re-run the docs pass over
   HLD §11 M9's event contract and the `status.json`/dashboard text, which was deliberately deferred.
2. Fix the T4 resume defect (item 2 above) — it is the only place where the shipped behaviour
   contradicts a procedure the design promises an operator.
3. Close `T-Ee3Mn8`'s remaining gates (security-pass record, reviewer acceptance matrix, per-module
   coverage table) and reconcile its test-count evidence with the epic rollup, which disagree.
4. Propose consumer adoption in `../ao-runner-finplan` as a ticket **in that repo** (HLD §20 item 7),
   once items 1-3 are done.
