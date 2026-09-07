# ADR-0013 — Per-task git worktree isolation with squash+rebase integration and soft overlap-aware assignment

- Status: **Proposed — amended 2026-09-07 after two pre-implementation review gates** (design complete;
  implementation of `T-Gt4Pw8`/`T-Sc7Rm2` started 2026-09-07). Gate outcomes: reviewer *APPROVE WITH
  CHANGES* (6 Blocking / 11 Major / 7 Minor), dev-security *conditional pass* (2 Blocking / 3 Major).
  **No decision below was overturned by either gate**; D1-D7 stand as written. The amendments are
  D8 (new, the multi-run policy the reviewer found missing), three security controls folded into the
  Consequences, and the citation correction in Context. Per-finding dispositions live in
  [`task-isolation-hld.md`](../task-isolation-hld.md) §24.
- Date: 2026-09-06
- Deciders: Avadhoot Divekar (user — decisions D1, D3, D8, D9 and the ladder shape were stated by the
  user and are recorded, not re-litigated), Claude (architect role)
- Related: **[ADR-0007](ADR-0007-parallel-task-execution.md)** (opt-in parallel execution — this ADR
  closes the write-conflict gap ADR-0007 explicitly accepted) · [ADR-0003](ADR-0003-settings-precedence-policy.md)
  (invocation-chain precedence — `--isolation` rides it as a **fill-in default** for tasks that declare no `isolation`, never as an override of an explicit per-task value; the separate `--no-isolation` kill switch is the only thing that overrides authored intent, and it logs what it overrode) · [ADR-0006](ADR-0006-per-agent-config-over-run-level-flags.md)
  (run-level vs per-agent knobs) · [ADR-0011](ADR-0011-untrusted-workspace-content-rendering.md)
  (path-guard lineage) · design [`task-isolation-hld.md`](../task-isolation-hld.md) · epic
  [`E-Wk9Tz3-task-isolation`](../../meta/tickets/E-Wk9Tz3-task-isolation/EPIC.md) ·
  [`meta/ROADMAP.md`](../../meta/ROADMAP.md) §3.4 / §4
- Reserved elsewhere: ADR-0014 is claimed by a concurrent scheduler/cron epic and is **not** touched
  by this record.

## Context

ADR-0007 shipped opt-in parallel execution **without** any write-conflict mechanism. To be precise
about the record (R-10 — an earlier draft of this ADR presented the following as a verbatim ADR-0007
quotation, and it is not): ADR-0007's Consequences section discusses only executor thread-safety and
log-line interleaving; neither it nor `parallel-execution-hld.md` contains the words "overlap",
"conflict", "collide", "disjoint" or "spec author". The characterization that keeping co-scheduled
tasks' outputs disjoint is the spec author's responsibility is **`meta/ROADMAP.md` §4's**, written
after the fact:

> "With `max_parallel > 1`, co-scheduled tasks are not checked for overlapping outputs — keeping them
> disjoint is the spec author's job (ADR-0007). A live run has already produced real file contention."
> — `meta/ROADMAP.md` §4

The substance is unaffected: `max_parallel > 1` genuinely ships with no write-conflict detection,
confirmed directly at `engine.py:304-305`.

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
(HLD §7.3): the widening lives in a separate per-task wrapper (`isolation/view.py::
IsolatedArtifactView`) rather than in `LocalFsArtifactStore`, whose own guard is unchanged; the
wrapper's roots are supplied only by `WorktreeManager`, never by a spec, manifest or agent,
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

---

## D8 — One workspace, one isolation-active run: a per-workspace run lock, default-degrade

**Decision.** A run that activates isolation claims a `WorkspaceRunLock`
(`$AO_STATE_DIR/runlocks/<workspace_key>.lock`, `flock` + a `{run_id, pid, boot_id}` payload, reclaimed
when the recorded pid is dead) before it creates any ref, and holds it for the life of the run.
`integration.workspace_lock` selects what a second run does when the lock is held by a live run:
**`require`** (default) — do not activate isolation; degrade to `isolation: none` with
`integration.degraded reason="workspace_locked:<holder>"`; **`skip_sync`** — isolate and integrate, but
never fast-forward the shared checkout (only correct when every task in the workflow is isolated;
`ao validate` warns); **`off`** — no protection, warned at startup.

**Why this was missing and why it matters.** `meta/ROADMAP.md` §3.4 and the sibling scheduler epic
(ADR-0014, `scheduler-triggers-hld.md`) both explicitly defer this question *to this ADR* — "keep the
scheduler's per-workspace cap at 1 for isolated workflows until the isolation epic states a multi-run
policy" — and the first version of this design never stated one. The gap is narrow but real: **landing
is already safe** under concurrency (the per-repo `IntegrationLock` plus the `update-ref` CAS serialize
two runs' `integrate()` calls, and each run has its own integration ref), but D5's checkout sync is a
**working-tree mutation of one shared physical checkout** with no lock, no CAS and no awareness that
another run has a different integration branch. Two runs' barrier-time syncs can interleave partial
checkouts, or fast-forward the tree to the other run's head under a task about to read it.

**Why lock the whole run rather than the fast-forward.** Locking only the FF would make the sync atomic
but leave the window between it and the non-isolated task that reads the tree — that task would still
see another run's head appear mid-execution. Owning the workspace for the life of the run is the only
granularity at which D5's guarantee is actually true.

**Why degrade rather than fail.** A second run failing outright would make the isolation feature a
foot-gun for anyone who runs two workflows in one workspace, which is legal today. Degrading gives the
*first* run every guarantee and the second exactly today's behaviour — no silent corruption, no new
failure mode. `strict: true` upgrades the degrade to a failure for operators who want that.

**Interlock.** `E-Sc9Rt4` needs no change: `require` is precisely the behaviour ADR-0014 assumed when
it capped per-workspace concurrency at 1. This ADR turns that assumption into enforcement, so a cap
violation or a manual second `ao run` degrades cleanly instead of racing.

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
- **Security controls that are structural, not advisory** (added after the security gate). Three
  controls are enforced by construction rather than by prompt or convention, because each guards a
  place where **the engine** — not a workflow author — is the actor:
  (1) every engine-issued git call carries `core.hooksPath=<empty dir>` plus a prompt-free,
  signature-free environment, so a repo's bootstrapped hooks cannot fire unattended once per task
  across a ~100-task run; (2) the T2 resolver's `disallowed_tools` are **force-injected** (unioned with
  whatever the named agent declares) and its push path is neutralized via environment, because it is a
  new engine-triggered dispatch fed raw conflict content that nobody reviewed — a larger input surface
  than any existing dispatch, where the author chose the inputs; (3) the engine's auto-commit screens
  **untracked** paths against a secret denylist and refuses by default, because "it only lands on an
  ao-owned branch" was never the whole story — that branch is fast-forwarded into the user's real
  checkout and is expected to be pushed by a later task.
- **Negative / accepted.** Integration is serialized per repository and can become the bottleneck if
  `verify_command` is expensive (measured via `integration.merged duration_ms`; speculative
  integration is a named follow-on). Cold rebuilds per worktree are a real cost for heavy toolchains
  (D7 recipe). Cross-repo landing is not atomic (rebase+verify all, then CAS each; only an I/O failure
  between two CAS calls can land one repo and not another — reported as `integration.partial`).
  `rerere` learns across runs, so a bad-but-verified resolution can persist (`resolvers.rerere: false`
  is the escape hatch) — and with the **default** structural verify a replay is functionally
  unreviewed, which the docs now say plainly and `tier_counts` makes visible. Two concurrent runs in one
  workspace mean the second one silently loses isolation (D8) rather than sharing it. The first
  non-isolated barrier can fail on a chronically dirty checkout; the design names the colliding paths
  instead of guessing, and deliberately declines to stash the operator's uncommitted work. The artifact path guard is widened, which is why `T-Ee3Mn8-e2e-and-review`
  carries a mandatory security pass over exactly that change.
- **Follow-ons unblocked.** `isolation: copy`; an `integration_conflicts` breaker condition (its
  counters ship with this epic); speculative integration; learning `touches` from observed diffs; a
  dashboard conflict view; adaptive `max_parallel` driven by observed conflict rate.
