# ADR-0013 — Per-task git worktree isolation with squash+rebase integration and soft overlap-aware assignment

- Status: **Proposed** (design complete, implementation not started)
- Date: 2026-09-06
- Deciders: Avadhoot Divekar (user — decisions D1, D3, D8, D9 and the ladder shape were stated by the
  user and are recorded, not re-litigated), Claude (architect role)
- Related: **[ADR-0007](ADR-0007-parallel-task-execution.md)** (opt-in parallel execution — this ADR
  closes the write-conflict gap ADR-0007 explicitly accepted) · [ADR-0003](ADR-0003-settings-precedence-policy.md)
  (invocation-chain precedence — `--isolation` rides it) · [ADR-0006](ADR-0006-per-agent-config-over-run-level-flags.md)
  (run-level vs per-agent knobs) · [ADR-0011](ADR-0011-untrusted-workspace-content-rendering.md)
  (path-guard lineage) · design [`task-isolation-hld.md`](../task-isolation-hld.md) · epic
  [`E-Wk9Tz3-task-isolation`](../../meta/tickets/E-Wk9Tz3-task-isolation/EPIC.md) ·
  [`meta/ROADMAP.md`](../../meta/ROADMAP.md) §3.4 / §4
- Reserved elsewhere: ADR-0014 is claimed by a concurrent scheduler/cron epic and is **not** touched
  by this record.

## Context

ADR-0007 shipped opt-in parallel execution with a stated, accepted gap:

> "With `max_parallel > 1`, co-scheduled tasks are not checked for overlapping outputs — keeping them
> disjoint is the spec author's job."

`engine.py:304-305` computes `repo_paths` once per run and hands the identical dict to every worker.
There is no per-task working directory, branch, or conflict detection anywhere in `src/`.

The gap has failed in the field. In `../ao-runner-finplan` (one clone, one `epic/<id>` branch, up to
101 injected tasks, `max_parallel: 4`), a live run produced real contention on
`src/server/fin_rust/src/apis/accounts.rs` — a genuine hotspot (23 lifetime commits, 18 in six
months) — and survived only via a self-heal `git stash`, which that repo's own
`PARALLEL_DEVELOPMENT_GUIDELINES.md` §6.4 forbids because the stash stack is shared across every
concurrent agent.

More expensively, the gap is being **paid for in lost parallelism**: their breakdown agent adds
cross-task `depends_on` edges purely to avoid collisions (14 of 15 fan-out tasks in one epic, 15 of
20 in another), with `tasks.md` stating outright that a phase ordering exists only because the tasks
"touch overlapping core Rust files". The prize is not "fewer conflicts" — it is letting the breakdown
agent stop serializing work it only serialized out of fear.

Six decisions were coupled enough to record together.

---

## D1 — One git worktree per task per repository, branch `ao/<run_id>/<task_id>`, based at the integration head at dispatch time

**Decision.** An `isolation: worktree` task runs in a private checkout created by
`git worktree add -b ao/<run_id>/<task_id> <path> <integration_head>`, with worktrees stored **outside
every working tree** under `$AO_STATE_DIR/worktrees/<workspace_key>/<run_id>/<task_id>/<repo_key>`.
Several `RepoRef`s that resolve into the same git repository share one worktree.

**Why worktrees and not clones or containers.** A worktree shares the object store, so creation is
cheap and, critically, materializes **tracked files only** — an ignored 105 GB build directory is
never duplicated. A clone per task would duplicate objects; a container per task is a different and
much larger trust/ops boundary (ROADMAP §3.1) that this epic deliberately does not open.

**Why the base is the integration head at *dispatch* time, not at run start.** A task must see the
work of the predecessors it depends on. Basing at dispatch time makes dependency visibility automatic
(with D5's readiness rule) and keeps each rebase as short as possible.

**Why outside the workspace.** Worktrees under `<workspace_root>/.ao/worktrees/` would place N full
copies of the source inside the tree that agents search — every `rg`/glob in the main checkout would
match N duplicates, `git status` would be permanently noisy, and a non-isolated task's `git add -A`
could sweep them in. The price is one narrow, producer-restricted widening of the artifact path guard
(HLD §7.3): `extra_roots` is supplied only by `WorktreeManager`, never by a spec, manifest or agent,
and `resolve()` still runs before the containment test so traversal defence is unchanged.

**Why one worktree per *repository*, not per `RepoRef`.** The real consumer lists `core=./fin_plan`
and `docs=./fin_plan/docs-md` — one git repository, two refs. Grouping by
`rev-parse --git-common-dir` is the only correct interpretation, and it also yields the deterministic
lock ordering D3 needs.

---

## D2 — Isolation is opt-in per workflow and per task; non-git and old-git degrade, never fail

**Decision.** `WorkflowDefaults.isolation` defaults to `none`; `TaskSpec.isolation` defaults to
`inherit`. A run whose repos are not git repositories, or whose `git` is older than 2.30, logs
`integration.degraded` once and runs exactly as today. `isolation.strict: true` turns the degrade
into a hard failure. Structural tasks (`emit_tasks`, router, loop-gate) are forced to `none` — they
are already serial barriers (ADR-0007 D4) and typically need the shared checkout (`git-branch-off`).

**Why.** ADR-0007's own regression discipline: the default path must be byte-identical to today, and
that must be a *testable gate* (the pre-epic engine suite passing unedited), not a claim. Degrading
rather than failing matters because a reposet can legitimately mix a git repo with a plain directory.

---

## D3 — Integration is squash → rebase → verify → compare-and-swap fast-forward, serialized per repository

**Decision.** On success the task's branch is squashed to exactly one commit
(`git commit-tree <tip^{tree}> -p <base>`), rebased onto the current integration head, verified, and
landed with `git update-ref <integration_ref> <new> <expected_old>` — an atomic compare-and-swap.
A per-repository lock (in-process `threading.Lock` + a cross-process `flock` on a companion
`ao-integration.lock`) serializes the whole sequence; locks are acquired in sorted repo-key order.

**Why squash.** One commit per task makes the rebase a single-commit replay, so a conflict is resolved
**once** instead of once per intermediate agent commit (agent branches routinely carry a dozen noisy
commits). It also makes "revert one task" a one-commit operation and makes the landing tree exactly
the tree that verify tested.

**Why rebase and not a merge commit.** Evaluated seriously, as requested:

| | Squash + rebase (chosen) | Merge commit (`merge --no-ff`) |
|---|---|---|
| History | Linear, one commit per task, trivially bisectable | Non-linear; every agent's intermediate commits enter the log |
| Conflict work | One resolution per task | One per replayed commit unless squashed anyway |
| Landing race | `update-ref` CAS — atomic, cross-process, cannot half-apply | Needs a working tree or `merge-tree` + commit; more moving parts |
| Verify fidelity | Verify runs on exactly the tree that lands | Same, if merged in a worktree |
| Undo one task | `git revert <one commit>` | `git revert -m 1 <merge>` — marginally better for a *tangled* task |
| Provenance | Base recorded in trailers (`AO-Base`) | Base preserved structurally |

Rebase wins on every axis that matters at 100-task scale; the merge commit's only edge (structural
provenance) is recovered by trailers. `integration.strategy: "merge"` is reserved in the schema and
rejected by cross-validation so the door stays open without shipping two code paths.

**Why CAS instead of `git merge --ff-only` into a checked-out branch.** The integration branch is
never checked out (D4), so landing is a ref move: it cannot fail because a working tree is dirty, it
cannot half-apply, and it detects a concurrent writer (a second `ao` process) as a clean retryable
error rather than as corruption.

---

## D4 — The integration branch is an ao-owned ref that is never checked out; the user's checkout is fast-forwarded on demand

**Decision.** At the first isolated dispatch, ao creates `refs/heads/ao/<run_id>/integration` at the
primary repo's current `HEAD` and records it in `RunState`. Nothing checks that branch out. The
user's main checkout is fast-forwarded to the integration head **only** at barrier points: immediately
before a non-isolated task dispatches, and once at run end (`integration.sync_checkout: on_demand`).
Consequently, a non-isolated task in an isolation-active run is a **serial barrier** (D5).

**Why not integrate straight into the checked-out branch.** The consumer's checkout carries 4106
`status --porcelain` entries right now, and their `git-branch-off` agent aborts on a dirty tree. A
design that requires a clean working tree to land each of 100 tasks would fail on the machine it is
being built for. Ref moves are indifferent to dirt; the working-tree update is deferred to the two
moments where nothing else is running.

**Why lazily, at first isolated dispatch, not at run start.** The consumer's `git-branch-off` task
creates and checks out `epic/<id>` as the *first* task of the run. Capturing the base at run start
would branch from the wrong commit. Capturing it at first isolated dispatch means ao inherits whatever
branch the workflow's own git stage established — no new branch-naming convention is imposed, and the
existing stage needs no change.

**Consequence.** The engine never pushes. Publishing the integration branch stays an explicit agent
task (`90-final-push.md`-style), which keeps the "never force-push, never push main" agent contract
intact and out of the engine.

---

## D5 — A dependent dispatches only after its predecessors are *integrated*, and `should_skip` requires integration too

**Decision.** `_ready_ids` treats a predecessor as settled only when its integration state is
`integrated` (or it was never isolated). `RunStateStore.should_skip` gains the same condition for
isolated tasks.

**Why readiness.** A worktree is based at the integration head; if a dependent were dispatched while
its predecessor's work were still in a private branch, it would be based on a tree missing that work,
guaranteeing a conflict the design exists to avoid.

**Why `should_skip`.** This is a real, sharp hazard, not a theoretical one. The consumer sets
`skip_if_outputs_exist: true` on **every** fan-out entry, and their artifacts live outside the git
repo (the runner root is not a repo), so an artifact can exist while the corresponding code was never
integrated. Without this rule a resumed run would skip a task whose code is stranded on an abandoned
branch — silently losing work. "Done" must mean "landed".

---

## D6 — Conflicts are handled by a cost-ordered ladder; escalation reuses the existing retry/requeue machinery rather than injecting DAG nodes

**Decision.** T0 git auto-merge → T1 deterministic mechanical resolvers (`rerere` replay, union merge
for registry-style globs, regenerate for lockfiles) → T2 a bounded LLM merge-resolver → T3 re-run the
task on the fresh base with its previous diff attached → T4 fail to the operator. Every integration
also runs a **verify** step before landing; a verify failure enters the ladder at T3.

**T2 and T3 are extra attempts of the same task**, dispatched through the normal path with a
substituted agent + instruction — **not** injected DAG nodes.

**Why the ladder.** The metric the user named is "how easy is the rebase", not lines of code.
Mechanical conflicts are acceptable and should cost nothing; only genuinely semantic ones should cost
an agent call. Most agent-generated collisions are append-only registry edits, regenerated lockfiles,
or a resolution this repository has already seen once — all free.

**Why not inject DAG nodes for T2/T3.** Injection is a DAG reshape, which ADR-0007 D4 makes a serial
barrier and which would stall every sibling for a bookkeeping operation. Reusing the requeue path
(the same mechanism quota, 429 and self-heal already use) means the budget gate, cost accumulation
into `cumulative_cost_usd`, and both the `task_cost_usd` and `run_cost_usd` breakers apply to conflict
spending **with zero new plumbing** — and a task that conflicts pathologically trips its own budget
cap, which is exactly the safety behaviour wanted.

**Why verify at all, and why after the rebase.** Git detects only textual conflicts; two tasks can
each pass alone and break together. Every CI merge queue solves this the same way — test the
*post-merge* tree, and eject what fails. The default verify is a free structural check (leftover
conflict markers, `git diff --check`); `integration.verify_command` opts into a real build/test. A
required real command would put a cold build on the critical path of every task and make isolation
unusable out of the box.

**Why a real repair loop instead of pure ejection.** This is the differentiator versus every merge
queue and every branch-per-agent product surveyed: they can only reject a candidate and wait for a
human. We can attempt a bounded, budgeted repair first, and only then eject.

---

## D7 — `touches` is a soft scheduling hint, never a gate; isolation isolates source, not build caches

**Decision.** `TaskSpec.touches: [glob]` is advisory, may be absent, incomplete or wrong. A pure,
deterministic `rank_wave(ready, touches, hotspots, n)` *prefers* co-scheduling non-overlapping tasks
within `max_parallel` and **always fills every available slot**. At `n == 1` it is provably a no-op,
preserving ADR-0007 D1/D6. Hotspots are derived from git churn plus previously-observed conflicts and
also fed to the breakdown agent as a declared input.

Separately: isolation isolates **source**, not build artifacts. `isolation.env` injects per-repo
environment (e.g. a shared `CARGO_TARGET_DIR`) into isolated tasks and verify commands.

**Why soft.** Nobody can declare an exact file set with certainty before doing the work, and the
consumer's own experience proves the failure mode of pretending otherwise: their breakdown agent turns
uncertainty into `depends_on` edges, trading away the parallelism this epic is buying. A hint that
costs nothing when wrong is used; a declaration that blocks when wrong is gamed.

**Why the build-cache carve-out is a decision and not an oversight.** The consumer measured a 105 GB
Rust `target/` against ~18-21 GB free disk and concluded worktree-per-agent was unaffordable. That
measurement is about duplicating build output, which `git worktree` does not do — but the *cold
rebuild* per worktree is real. Sharing the target dir is safe because cargo takes its own file lock
on it (concurrent builds serialize rather than corrupt), and the collision their guidelines actually
warn about is a *source* collision, which isolation removes. Shipping the recipe explicitly, plus the
option to leave heavy build stages `isolation: none` (they are barriers anyway), is more honest than
pretending the disk question does not exist.

---

## Alternatives considered

- **Hard overlap gating** (refuse to co-schedule tasks with overlapping declared files) — **rejected**
  by the user's explicit requirement and on merit: it demands certainty nobody has, and its failure
  mode is silent serialization.
- **Copy-based isolation for every workspace** (`cp -a` per task) — **deferred to non-MVP**. It works
  for non-git workspaces but has no cheap integration story: there is no three-way merge, no `rerere`,
  no CAS. The enum value is reserved.
- **One clone per task** — rejected: duplicates the object store for no benefit over a worktree.
- **Container/VM per task** — rejected for this epic: a different and much larger trust boundary
  (ROADMAP §3.1), and orthogonal to the write-conflict problem.
- **Integrate on the main thread inside `_settle_completed_task`** — rejected: verify can take
  minutes, and blocking the single-writer thread would stall every other completion and every new
  dispatch. Integration runs on the worker that ran the task, guarded by the per-repo lock; only the
  `IntegrationResult` crosses back to the main thread, preserving ADR-0007 D3 exactly.
- **A dedicated integrator thread with a queue** — rejected: it would need to mutate `RunState`,
  breaking the single-writer invariant, for no gain over the per-repo lock.
- **Speculative/optimistic parallel integration (Zuul-style)** — rejected for MVP as large scope; the
  serialized lock is the merge-queue baseline and is correct first.
- **Persistent integration worktree** (checkout the integration branch somewhere ao owns) — rejected:
  it would make the branch checked out, forbidding the CAS ref move that makes landing race-free, and
  it adds a third working tree to keep consistent.

## Consequences

- **Positive.** The ROADMAP §3.4 / §4 gap closes. Breakdown agents can drop collision-avoidance
  `depends_on` edges, which is where the real speedup lives. A failed integration leaves an
  inspectable git state (named worktree, named branch, recorded base/squash, captured verify log)
  instead of a shared checkout in an unknown state. Conflict spend is bounded by the budget system
  that already exists.
- **Negative / accepted.** Integration is serialized per repository and can become the bottleneck if
  `verify_command` is expensive (measured via `integration.merged duration_ms`; speculative
  integration is a named follow-on). Cold rebuilds per worktree are a real cost for heavy toolchains
  (D7 recipe). Cross-repo landing is not atomic (rebase+verify all, then CAS each; only an I/O failure
  between two CAS calls can land one repo and not another — reported as `integration.partial`).
  `rerere` learns across runs, so a bad-but-verified resolution can persist (`resolvers.rerere: false`
  is the escape hatch). The artifact path guard is widened, which is why `T-Ee3Mn8-e2e-and-review`
  carries a mandatory security pass over exactly that change.
- **Follow-ons unblocked.** `isolation: copy`; an `integration_conflicts` breaker condition (its
  counters ship with this epic); speculative integration; learning `touches` from observed diffs; a
  dashboard conflict view; adaptive `max_parallel` driven by observed conflict rate.
