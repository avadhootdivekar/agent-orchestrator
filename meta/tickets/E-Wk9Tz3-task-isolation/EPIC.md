# EPIC: E-Wk9Tz3-task-isolation

## Metadata
- Epic ID: `E-Wk9Tz3-task-isolation`
- Title: Per-task git worktree isolation with squash+rebase integration and soft overlap-aware task assignment
- Owner: architect (agent) — implementation owner TBD; manager (delivery close-out, 2026-09-11)
- Created: 2026-09-06
- Last Updated: 2026-09-11
- Status: **Done (2026-09-11) — all 14 tasks Done, committed, and independently re-verified.**
  `T-Cx4Jf1` (Parts A+B) and `T-Ee3Mn8` (both rework halves) each got an independent re-verification
  pass at close-out since their own `STATUS.md`s had gone stale relative to the merged code; one real
  regression found by that process (`ao resume` after T4 discarding an operator's hand-resolution) was
  fixed and pinned with a new test before closing. `T-Dr5Yq6` pass 2 reconciled the HLD's event
  contract and closed out all five previously-open "known-defective as shipped" rows. Full-suite gates
  at close: `pytest -q` 3831 passed / 8 skipped / 0 failed; `ruff` clean; `mypy src` 4 pre-existing
  `_version.py` errors (unchanged baseline).

## Summary
- **Goal**: Close the write-conflict gap ADR-0007 accepted and `meta/ROADMAP.md` §3.4/§4 carries.
  Each parallel task runs in its own git worktree on its own branch; results land on a run-scoped
  integration branch by *squash → rebase → verify → compare-and-swap fast-forward* under a per-repo
  lock; conflicts are repaired by a cost-ordered ladder (free git auto-merge → free mechanical
  resolvers → one bounded LLM merge-resolver → one re-run on the fresh base → operator). Task
  assignment prefers low-overlap co-scheduling using **soft** `touches` hints and hotspot data, and
  **never** withholds a slot because of overlap.
- **Why now**: a live run in `../ao-runner-finplan` produced real contention on
  `src/server/fin_rust/src/apis/accounts.rs`, survived only by a self-heal `git stash` that their own
  parallel-development guidelines forbid. More expensively, their breakdown agent is buying safety
  with serialization — 14 of 15 fan-out tasks in one epic carry a cross-task `depends_on` added
  purely to avoid file collisions. The prize is letting decomposition stop doing that.
- **Scope In**: `TaskSpec.isolation` / `TaskSpec.touches`; `WorkflowDefaults.isolation`;
  `WorkflowSpec.integration` / `WorkflowSpec.scheduling`; a new `isolation/` package
  (`git.py`, `paths.py`, `worktrees.py`, `integrator.py`, `locks.py`, `resolvers.py`,
  `escalation.py`, `hotspots.py`); `scheduling/overlap.py`; engine dispatch/settle wiring;
  a per-task isolated artifact view; `TaskContext.env` + executor env overlay; `RunState`
  integration state; `specs/workflow.schema.json`; `cli.py` (`--isolation`, `ao hotspots`,
  `ao prune` worktree GC); `.ao/config.yaml` `isolation:` block; two shipped instruction assets;
  `routed-runner` template wiring; tests + docs.
- **Scope Out**: `isolation: copy` for non-git workspaces (enum reserved) · merge-commit integration
  strategy (reserved, validation-rejected) · speculative/parallel integration · an
  `integration_conflicts` breaker condition · cross-repo atomic landing · container/VM sandboxing
  (ROADMAP §3.1) · a dashboard conflict UI · **any edit to `../ao-runner-finplan`** (read-only
  reference; consumer adoption is a ticket in that repo) · `meta/ROADMAP.md`, `CLAUDE.md`,
  `meta/learnings*.md` and existing ADRs (owned elsewhere) · `docs-md/scheduler-triggers-hld.md` /
  ADR-0014 (concurrent epic).

## Requirements

Full text in [`docs-md/task-isolation-hld.md`](../../../docs-md/task-isolation-hld.md) §3.

| ID | Requirement | Task |
|---|---|---|
| FR-1 | `isolation: none\|worktree\|inherit` per task, `defaults.isolation` per workflow; unchanged specs behave byte-identically | T-Sc7Rm2, T-En8Hd4 |
| FR-2 | Worktree per isolated task, branch `ao/<run_id>/<task_id>`, based at the integration head at dispatch, stored outside every working tree | T-Wk3Nv6 |
| FR-3 | `RepoRef`s resolving into the same git repository share one worktree; distinct repos get their own | T-Wk3Nv6 |
| FR-4 | Deterministic `effective_path` remap of every path handed to an isolated task; ao-reserved prefixes stay shared | T-Wk3Nv6, T-En8Hd4 |
| FR-5 | Auto-commit the worktree at task end; commit message built from ids/paths only (NFR-1) | T-Ib5Qy9 |
| FR-6 | squash → rebase → verify → CAS fast-forward, serialized per repository (thread lock + `flock`) | T-Ib5Qy9 |
| FR-7 | Conflict ladder T0 auto → T1 mechanical → T2 LLM resolver → T3 rerun → T4 fail; configurable, observable, budget-accounted | T-Rm2Lx7, T-Lr6Ka3 |
| FR-8 | A verify step before every landing; free structural default; failure enters the ladder at T3 | T-Ib5Qy9, T-Lr6Ka3 |
| FR-9 | A dependent dispatches only after its predecessors are **integrated** | T-En8Hd4 |
| FR-10 | `touches` soft hints + hotspot weighting reorder the ready set deterministically and never withhold a slot | T-Ov9Bt5 |
| FR-11 | `ao hotspots` derives churn + observed-conflict hotspots; fed to the scheduler and the breakdown agent | T-Ov9Bt5 |
| FR-12 | Non-git / old-git degrade to `isolation: none` with a warning (`strict` opts into hard failure) | T-En8Hd4 |
| FR-13 | Non-isolated tasks are barriers in an isolation-active run; the main checkout is fast-forwarded on demand | T-En8Hd4 |
| FR-14 | Worktrees/branches/refs are GC'd after success, on `ao prune`, and by an idempotent reconcile | T-Wk3Nv6, T-Cx4Jf1 |
| FR-15 | `worktree.*` / `integration.*` events, `RunState` fields and `status.json` keys make the path traceable | T-Cx4Jf1 |
| FR-16 | Ship conflict-friendly coding rules + `merge-resolve` instruction; wire the `routed-runner` breakdown contract for `touches`/`isolation` | T-Tp7Zs2 |
| NFR-1 | The orchestrator core never reads artifact/instruction/conflict **contents** | all |
| NFR-2 | `max_parallel == 1` + `isolation: none` byte-identical to today — the pre-epic engine suite passes unedited (**blocking gate**) | T-En8Hd4, T-Ee3Mn8 |
| NFR-3 | Single-writer engine core preserved (ADR-0007 D3): only workers touch git, only the main thread mutates `RunState` | T-En8Hd4 |
| NFR-4 | Every git step bounded and idempotent; a crash mid-integration is mechanically recoverable | T-Wk3Nv6, T-Ib5Qy9 |
| NFR-5 | Additive schema/state only; old state loads, new state degrades gracefully | T-Sc7Rm2 |
| NFR-6 | Disk cost bounded and documented (worktrees copy tracked files only; cold-rebuild recipe) | T-Dr5Yq6, T-Tp7Zs2 |

## Task List

Dependency order. Every task is <= 3 days and owns a disjoint file set.

- [x] `T-Gt4Pw8-git-porcelain` — `isolation/git.py`: the single typed, timeout-bounded git surface — **2 d** — deps: none
- [x] `T-Sc7Rm2-isolation-schema-models` — models, JSON Schema, run state, cross-validation — **2 d** — deps: none
- [x] `T-Wk3Nv6-worktree-lifecycle` — `isolation/paths.py` + `worktrees.py` + `view.py` + shared `xdg.py`: naming, `effective_path`, the per-task `IsolatedArtifactView`, create/reuse/release/reconcile/GC — **3 d** — deps: T-Gt4Pw8, T-Sc7Rm2
- [x] `T-Ib5Qy9-integrator-core` — `isolation/integrator.py` + `locks.py`: squash/rebase/verify/CAS under lock — **3 d** — deps: T-Gt4Pw8, T-Wk3Nv6
- [x] `T-En8Hd4-engine-isolation-wiring` — engine dispatch/worker/settle, isolated artifact view, `TaskContext.env`, barriers, checkout sync, NFR-2 gate — **3 d** — deps: T-Sc7Rm2, T-Wk3Nv6, T-Ib5Qy9
- [x] `T-Rm2Lx7-mechanical-resolvers` — `isolation/resolvers.py`: rerere / union / regenerate (T1) — **2.5 d** — deps: T-Gt4Pw8, T-Ib5Qy9
- [x] `T-Lr6Ka3-llm-resolver-and-rerun` — `isolation/escalation.py` + resolver-mode dispatch (T2/T3) — **3 d** — deps: T-En8Hd4, T-Rm2Lx7
- [x] `T-Ov9Bt5-overlap-scheduling-hotspots` — `scheduling/overlap.py` + `isolation/hotspots.py` + `ao hotspots` — **2.5 d** — deps: T-Sc7Rm2
- [x] `T-Ac6Vd9-requeue-accounting` — cycle-keyed budget ledger + accumulate-before-requeue + cycle-keyed transcript capture (R-1, R-21) — **2 d** — deps: T-Sc7Rm2, T-En8Hd4
- [x] `T-Wl2Bq7-workspace-run-lock` — per-workspace isolation run lock + sync diagnostics (R-4, R-12) — **1.5 d** — deps: T-Wk3Nv6, T-En8Hd4
- [x] `T-Cx4Jf1-cli-config-prune-observability` — `--isolation`, config block, `ao prune` GC, events, `status.json`, dashboard column — **2.5 d** — deps: T-Wk3Nv6, T-Ib5Qy9, T-En8Hd4
- [x] `T-Tp7Zs2-instructions-and-templates` — conflict-friendly rules, `merge-resolve.md`, `routed-runner` contract/template wiring, removal of the `git push` directives from six instruction files — **2 d** — deps: T-Sc7Rm2
- [x] `T-Ee3Mn8-e2e-and-review` — git fixtures, e2e via `CliRunner`, NFR-2 gate, security review of the path-guard widening — **3 d** — deps: all above
- [x] `T-Dr5Yq6-docs-refresh` — reconcile `docs-md/` + ADR-0013 status against the as-built implementation — **1 d** — deps: T-Ee3Mn8

## Sprint plan

**Parameters** (project standard): 2-week sprints, 5-day weeks, 40% overhead, developers with
<4 years experience. **Team size: 3.**

```
GrossHoursPerSprint      = 3 * 10 * 8              = 240 h
NetFocusHoursPerSprint   = 240 * 0.60              = 144 h
CommitmentHoursPerSprint = 144 * (0.70 .. 0.85)    = 100.8 .. 122.4 h
                                                   = 12.6 .. 15.3 developer-days @ 8 h
Three sprints                                      = 37.8 .. 45.9 developer-days
```

**Revised after the 2026-09-07 review gates: 14 tasks, 33 developer-days, three sprints** (was 12
tasks / 28 days / two sprints). The growth is +2 tasks carved out of `T-En8Hd4` to keep every task
within the 3-day cap (`T-Ac6Vd9` 2 d, `T-Wl2Bq7` 1.5 d) plus +1.5 days of re-estimates
(`T-Wk3Nv6` +0.5, `T-Cx4Jf1` +0.5, `T-Tp7Zs2` +0.5).

| Sprint | Tasks | Days | Fit |
|---|---|---|---|
| **1 — foundation + core integration** | T-Gt4Pw8 (2), T-Sc7Rm2 (2), T-Wk3Nv6 (3), T-Ib5Qy9 (3), T-En8Hd4 (3) | **13.0** | Inside the 12.6-15.3 band. Unchanged in shape; `T-Wk3Nv6` grew by the `IsolatedArtifactView` and the shared XDG helper |
| **2 — ladder, accounting, multi-run, scheduling** | T-Ac6Vd9 (2), T-Wl2Bq7 (1.5), T-Rm2Lx7 (2.5), T-Lr6Ka3 (3), T-Ov9Bt5 (2.5), T-Tp7Zs2 (2) | **13.5** | Inside the band. `T-Ac6Vd9` and `T-Wl2Bq7` both depend on a merged `T-En8Hd4`, so they cannot start earlier |
| **3 — surface, verification, docs** | T-Cx4Jf1 (2.5), T-Ee3Mn8 (3), T-Dr5Yq6 (1) | **6.5** | **Deliberately under the floor.** `T-Ee3Mn8` is the late gate over five tasks' worth of `engine.py` edits and a full security-implementation pass; the slack is the remediation budget for what that gate finds. Under-committing a verification sprint is a choice, not an oversight |
| | **Total** | **33.0** | vs 37.8-45.9 available across three sprints |

Sprint 1 exit criterion: an isolated task runs in its worktree (R-19 proven on the real `TaskContext`)
and lands end to end at T0/T1, with the NFR-2 gate green. Sprint 2 exit: the full ladder, correct
cost/token accounting across requeues, the multi-run policy enforced, and the soft scheduler wired at
its call site. Sprint 3 exit: operator surface, the finding-driven test matrix (HLD §17.5) complete,
and docs reconciled against as-built.

**`engine.py` edit order** (five tasks, fixed sequence, each narrow):
`T-En8Hd4` (main wiring) → `T-Ac6Vd9` (settle accounting + cycle-keyed capture) → `T-Wl2Bq7`
(sync + run lock) → `T-Lr6Ka3` (resolver/rerun dispatch) → `T-Cx4Jf1` (event emission only). Every
later ticket reads the **merged** file and each publishes its hook points in `STATUS.md`.

## Risks and Dependencies

Full register in the HLD §21. The five that shape the plan:

- **R1 — cold rebuilds per worktree** for heavy toolchains (the consumer measured a 105 GB Rust
  `target/` against ~18-21 GB free). Worktrees copy *tracked files only*, so the ignored directory is
  never duplicated; the cost is a cold build. Mitigated by `isolation.env` (shared `CARGO_TARGET_DIR`,
  which cargo file-locks), by keeping heavy stages `isolation: none`, and by per-repo opt-in.
- **R2 — the LLM resolver merges plausibly but wrongly.** Verify runs after resolution; T2 is capped
  at one attempt; one commit per task keeps the result reviewable.
- **R4 — the artifact path-guard widening.** `T-Ee3Mn8` carries a mandatory security pass over exactly
  that change. As built (`T-Wk3Nv6`), the widening is a separate per-task wrapper
  (`isolation/view.py::IsolatedArtifactView`) and `LocalFsArtifactStore` gained no `extra_roots`
  parameter; the wrapper's roots are producer-restricted and never spec- or agent-supplied.
- **R6 — `should_skip` + ephemeral worktrees stranding work on resume.** The consumer sets
  `skip_if_outputs_exist: true` on every fan-out entry and keeps artifacts outside the repo, so an
  artifact can exist while the code never landed. Handled by an explicit rule (integration-aware
  `should_skip`) with its own acceptance criterion in `T-En8Hd4`.
- **R-4 (new, from the review gate) — concurrent runs in one workspace.** Landing was always safe
  (per-repo lock + CAS), but D5's checkout fast-forward was an unlocked working-tree mutation. Resolved
  by ADR-0013 **D8**: a per-workspace `WorkspaceRunLock`, with `integration.workspace_lock: "require"`
  (default) degrading a second run to `isolation: none` rather than racing. `E-Sc9Rt4` needs no change —
  `require` is exactly what ADR-0014 assumed when it capped per-workspace concurrency at 1.
- **R-12 (new) — the first barrier's `git merge --ff-only` on a chronically dirty checkout.** The
  target consumer currently shows ~4106 `status --porcelain` entries. Mitigated with collision-naming
  diagnostics and a run-start pre-flight; stash-and-restore was **declined** (it mutates the operator's
  uncommitted work). `T-Ee3Mn8` measures the real collision rate.
- **Concurrency with other epics.** A scheduler/cron epic owns `docs-md/scheduler-triggers-hld.md`
  and ADR-0014 on this branch. This epic touches neither. `engine.py` is now edited by **five** tasks
  in the fixed order given in the sprint plan, each narrowly scoped and each required to read the
  merged file rather than re-derive from this design (carried as risk R13 in the HLD).

## Decisions needed from the user

Recommended defaults are already applied in the design; see HLD §20 for the full table.
1. Integration target: ao-owned `ao/<run>/integration` ref with on-demand checkout sync (recommended)
   vs. integrating straight into the checked-out branch.
2. Default verify: free structural check (recommended) vs. requiring a real build/test command.
3. Whether T2 (the paid LLM resolver) is on by default (recommended: yes, capped at 1).
4. Heavy-build policy: shared build cache via `isolation.env`, unisolated heavy stages, or both
   (recommended: both, documented).
5. Consumer adoption in `../ao-runner-finplan` — tracked in that repo, not here.

## Review gates (2026-09-07)

Two pre-implementation gates ran against the design package before any code was written. Both are
recorded in this folder and neither is edited by the epic:
[`REVIEW-design-2026-09-07.md`](REVIEW-design-2026-09-07.md) — reviewer, **APPROVE WITH CHANGES**
(6 Blocking, 11 Major, 7 Minor); [`REVIEW-security-design-2026-09-07.md`](REVIEW-security-design-2026-09-07.md)
— dev-security, **conditional pass** (2 Blocking, 3 Major, 3 Minor, 3 Info).

**No decision in ADR-0013 D1-D7 was overturned.** The amendments are: one new decision (D8, the
multi-run policy the reviewer found missing), three security controls made structural rather than
advisory (hook suppression, force-injected resolver tool policy, screened auto-commit), a citation
correction (R-10 — ADR-0013 attributed to ADR-0007 a sentence that is actually `meta/ROADMAP.md` §4's),
two new tasks, and 32 amended or added acceptance criteria across the ticket set.

Per-finding dispositions — **fixed / accepted-with-rationale / deferred**, each with the location of
the fix — are recorded once, in [`docs-md/task-isolation-hld.md`](../../../docs-md/task-isolation-hld.md)
§24 "Review dispositions". `T-Dr5Yq6-docs-refresh` re-verifies every *Fixed* row against the merged
code before the epic closes.

## Links
- Design doc: [`docs-md/task-isolation-hld.md`](../../../docs-md/task-isolation-hld.md)
- Decision record: [`docs-md/adr/ADR-0013-per-task-git-isolation-and-rebase-integration.md`](../../../docs-md/adr/ADR-0013-per-task-git-isolation-and-rebase-integration.md)
- Epic narrative: [`docs-md/ai-epics/E-Wk9Tz3-task-isolation.md`](../../../docs-md/ai-epics/E-Wk9Tz3-task-isolation.md)
- Predecessor: [`ADR-0007`](../../../docs-md/adr/ADR-0007-parallel-task-execution.md) · [`parallel-execution-hld.md`](../../../docs-md/parallel-execution-hld.md)
- Output artifacts (if any): none planned (code + docs only)

---
- By: architect · Role: architect · Date: 2026-09-06 · Comment: Epic created from the user's stated
  design intent (worktree-per-task, squash+rebase integration, soft overlap hints, tiered conflict
  ladder). HLD, ADR-0013 and 12 task tickets written; no code changed. Five decisions are recorded
  under "Decisions needed from the user" with recommended defaults already baked into the design so
  implementation is not blocked on them — items 1 and 2 should be confirmed before
  `T-Ib5Qy9-integrator-core` merges, since they are the hardest to reverse afterwards.
