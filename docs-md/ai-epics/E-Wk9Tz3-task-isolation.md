# Epic: E-Wk9Tz3-task-isolation

## Metadata
- Epic ID: `E-Wk9Tz3-task-isolation`
- Title: Per-task git worktree isolation with squash+rebase integration and soft overlap-aware task assignment
- Owner: architect (agent) — implementation owner TBD
- Created: 2026-09-06
- Last Updated: 2026-09-06
- Status: Draft (design complete, implementation not started)
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
12 tasks under `meta/tickets/E-Wk9Tz3-task-isolation/`, each <= 3 days with a disjoint file-ownership
boundary. `engine.py` is edited by exactly one task (`T-En8Hd4`) plus two narrowly-scoped edits
(`T-Lr6Ka3` resolver dispatch, `T-Cx4Jf1` logging only); `cli.py` is split explicitly between
`T-Ov9Bt5` (`ao hotspots`) and `T-Cx4Jf1` (`--isolation`, `ao prune`).

| Task | Owns | Days | Requirements |
|---|---|---|---|
| `T-Gt4Pw8-git-porcelain` | `isolation/git.py` + the shared git test fixtures | 2 | FR-2, FR-6, NFR-4 |
| `T-Sc7Rm2-isolation-schema-models` | `models.py`, `spec.py`, `workflow.schema.json`, `runstate.py` (state only) | 2 | FR-1, NFR-5 |
| `T-Wk3Nv6-worktree-lifecycle` | `isolation/paths.py`, `isolation/worktrees.py` | 2.5 | FR-2, FR-3, FR-4, FR-14 |
| `T-Ib5Qy9-integrator-core` | `isolation/integrator.py`, `isolation/locks.py` | 3 | FR-5, FR-6, FR-8 |
| `T-En8Hd4-engine-isolation-wiring` | `engine.py`, `artifacts.py`, `runstate.py`, `executors/` | 3 | FR-4, FR-9, FR-12, FR-13, NFR-2, NFR-3 |
| `T-Rm2Lx7-mechanical-resolvers` | `isolation/resolvers.py` | 2.5 | FR-7 (T1) |
| `T-Lr6Ka3-llm-resolver-and-rerun` | `isolation/escalation.py`, `merge-resolve.md` | 3 | FR-7 (T2-T4), FR-8 |
| `T-Ov9Bt5-overlap-scheduling-hotspots` | `scheduling/overlap.py`, `isolation/hotspots.py`, `ao hotspots` | 2.5 | FR-10, FR-11 |
| `T-Cx4Jf1-cli-config-prune-observability` | `cli.py`, `project_config.py`, events, `status.json`, dashboard column | 2 | FR-14, FR-15 |
| `T-Tp7Zs2-instructions-and-templates` | conflict-friendly rules + `routed-runner` wiring | 1.5 | FR-16, NFR-6 |
| `T-Ee3Mn8-e2e-and-review` | tests only + security/review passes | 3 | all, esp. NFR-1/2/3 |
| `T-Dr5Yq6-docs-refresh` | `docs-md/` reconciliation + ticket sync | 1 | post-implementation |

**Sprint plan.** Two 2-week sprints, team size 3, 40% overhead:
`Gross = 3*10*8 = 240 h` → `Net = 144 h` → `Commitment = 100.8-122.4 h` = **12.6-15.3 dev-days/sprint**.
Sprint 1 (foundation + core integration) = **12.5 d**; Sprint 2 (ladder, scheduling, surface,
verification) = **15.5 d**; total **28 d** against 25.2-30.6 available. Named descope candidates for
Sprint 2, in order: `T-Tp7Zs2`, then `T-Ov9Bt5`.

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
   `extra_roots` is producer-restricted, the run-state store is un-widened.
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
  branch. This epic touches neither, and does not edit `meta/ROADMAP.md`, `CLAUDE.md`,
  `meta/learnings*.md`, existing ADRs, `src/`, or `../ao-runner-finplan`.
- Not blocked. Five user decisions are recorded with recommended defaults already applied (HLD §20);
  the integration target and the default-verify behaviour should be confirmed before
  `T-Ib5Qy9-integrator-core` merges, since they are the hardest to reverse.

## Next actions
1. User confirms or overrides HLD §20's five decisions.
2. Optional early gate (mirroring E-GIytcL's): `reviewer` + `dev-security` against the HLD/ADR/tickets
   **before** any implementation, focused on §7.3 (path-guard widening) and §8 (integration protocol).
3. Start Sprint 1 with `T-Gt4Pw8-git-porcelain` and `T-Sc7Rm2-isolation-schema-models` in parallel.
