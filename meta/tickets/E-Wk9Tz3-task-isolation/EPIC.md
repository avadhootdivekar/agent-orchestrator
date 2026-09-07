# EPIC: E-Wk9Tz3-task-isolation

## Metadata
- Epic ID: `E-Wk9Tz3-task-isolation`
- Title: Per-task git worktree isolation with squash+rebase integration and soft overlap-aware task assignment
- Owner: architect (agent) — implementation owner TBD
- Created: 2026-09-06
- Last Updated: 2026-09-06
- Status: Draft

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

- [ ] `T-Gt4Pw8-git-porcelain` — `isolation/git.py`: the single typed, timeout-bounded git surface — **2 d** — deps: none
- [ ] `T-Sc7Rm2-isolation-schema-models` — models, JSON Schema, run state, cross-validation — **2 d** — deps: none
- [ ] `T-Wk3Nv6-worktree-lifecycle` — `isolation/paths.py` + `worktrees.py`: naming, `effective_path`, create/reuse/release/reconcile/GC — **2.5 d** — deps: T-Gt4Pw8, T-Sc7Rm2
- [ ] `T-Ib5Qy9-integrator-core` — `isolation/integrator.py` + `locks.py`: squash/rebase/verify/CAS under lock — **3 d** — deps: T-Gt4Pw8, T-Wk3Nv6
- [ ] `T-En8Hd4-engine-isolation-wiring` — engine dispatch/worker/settle, isolated artifact view, `TaskContext.env`, barriers, checkout sync, NFR-2 gate — **3 d** — deps: T-Sc7Rm2, T-Wk3Nv6, T-Ib5Qy9
- [ ] `T-Rm2Lx7-mechanical-resolvers` — `isolation/resolvers.py`: rerere / union / regenerate (T1) — **2.5 d** — deps: T-Gt4Pw8, T-Ib5Qy9
- [ ] `T-Lr6Ka3-llm-resolver-and-rerun` — `isolation/escalation.py` + resolver-mode dispatch (T2/T3) — **3 d** — deps: T-En8Hd4, T-Rm2Lx7
- [ ] `T-Ov9Bt5-overlap-scheduling-hotspots` — `scheduling/overlap.py` + `isolation/hotspots.py` + `ao hotspots` — **2.5 d** — deps: T-Sc7Rm2
- [ ] `T-Cx4Jf1-cli-config-prune-observability` — `--isolation`, config block, `ao prune` GC, events, `status.json`, dashboard column — **2 d** — deps: T-Wk3Nv6, T-Ib5Qy9, T-En8Hd4
- [ ] `T-Tp7Zs2-instructions-and-templates` — conflict-friendly rules, `merge-resolve.md`, `routed-runner` contract/template wiring — **1.5 d** — deps: T-Sc7Rm2
- [ ] `T-Ee3Mn8-e2e-and-review` — git fixtures, e2e via `CliRunner`, NFR-2 gate, security review of the path-guard widening — **3 d** — deps: all above
- [ ] `T-Dr5Yq6-docs-refresh` — reconcile `docs-md/` + ADR-0013 status against the as-built implementation — **1 d** — deps: T-Ee3Mn8

## Sprint plan

**Parameters** (project standard): 2-week sprints, 5-day weeks, 40% overhead, developers with
<4 years experience. **Team size: 3** — justified because the epic decomposes into three genuinely
parallel lanes after the two foundation tasks (git/worktree lane, engine lane, scheduling/templates
lane), and a smaller team would push this past three sprints for a change that is blocking ROADMAP
§3.4.

```
GrossHoursPerSprint      = 3 * 10 * 8              = 240 h
NetFocusHoursPerSprint   = 240 * 0.60              = 144 h
CommitmentHoursPerSprint = 144 * (0.70 .. 0.85)    = 100.8 .. 122.4 h
                                                   = 12.6 .. 15.3 developer-days @ 8 h
Two sprints                                        = 25.2 .. 30.6 developer-days
```

| Sprint | Tasks | Days | Fit |
|---|---|---|---|
| **1 — foundation + core integration** | T-Gt4Pw8 (2), T-Sc7Rm2 (2), T-Wk3Nv6 (2.5), T-Ib5Qy9 (3), T-En8Hd4 (3) | **12.5** | Inside the 12.6-15.3 band (at the floor — deliberate: T-Ib5Qy9 and T-En8Hd4 are the two hardest tasks and should not be crowded) |
| **2 — ladder, scheduling, surface, verification** | T-Rm2Lx7 (2.5), T-Lr6Ka3 (3), T-Ov9Bt5 (2.5), T-Cx4Jf1 (2), T-Tp7Zs2 (1.5), T-Ee3Mn8 (3), T-Dr5Yq6 (1) | **15.5** | At the top of the band. **Named descope candidates, in order: `T-Tp7Zs2` (templates can ship a sprint later without blocking anything) then `T-Ov9Bt5` (soft hints are a preference, not a correctness requirement)** |
| | **Total** | **28.0** | vs 25.2-30.6 available — ~92% of the ceiling |

Sprint 1 exit criterion: an isolated task can run and land end to end at T0/T1-free, with the NFR-2
gate green. Sprint 2 exit criterion: the full ladder, the soft scheduler, the operator surface, and
the e2e/security gate.

## Risks and Dependencies

Full register in the HLD §21. The five that shape the plan:

- **R1 — cold rebuilds per worktree** for heavy toolchains (the consumer measured a 105 GB Rust
  `target/` against ~18-21 GB free). Worktrees copy *tracked files only*, so the ignored directory is
  never duplicated; the cost is a cold build. Mitigated by `isolation.env` (shared `CARGO_TARGET_DIR`,
  which cargo file-locks), by keeping heavy stages `isolation: none`, and by per-repo opt-in.
- **R2 — the LLM resolver merges plausibly but wrongly.** Verify runs after resolution; T2 is capped
  at one attempt; one commit per task keeps the result reviewable.
- **R4 — the artifact path-guard widening.** `T-Ee3Mn8` carries a mandatory security pass over exactly
  that change; `extra_roots` is producer-restricted and never spec- or agent-supplied.
- **R6 — `should_skip` + ephemeral worktrees stranding work on resume.** The consumer sets
  `skip_if_outputs_exist: true` on every fan-out entry and keeps artifacts outside the repo, so an
  artifact can exist while the code never landed. Handled by an explicit rule (integration-aware
  `should_skip`) with its own acceptance criterion in `T-En8Hd4`.
- **Concurrency with other epics.** A scheduler/cron epic owns `docs-md/scheduler-triggers-hld.md`
  and ADR-0014 on this branch. This epic touches neither. `engine.py` is edited by exactly one task
  (`T-En8Hd4`) plus two narrowly-scoped edits (`T-Lr6Ka3`, `T-Cx4Jf1`) that must read the merged code
  rather than re-derive from this design.

## Decisions needed from the user

Recommended defaults are already applied in the design; see HLD §20 for the full table.
1. Integration target: ao-owned `ao/<run>/integration` ref with on-demand checkout sync (recommended)
   vs. integrating straight into the checked-out branch.
2. Default verify: free structural check (recommended) vs. requiring a real build/test command.
3. Whether T2 (the paid LLM resolver) is on by default (recommended: yes, capped at 1).
4. Heavy-build policy: shared build cache via `isolation.env`, unisolated heavy stages, or both
   (recommended: both, documented).
5. Consumer adoption in `../ao-runner-finplan` — tracked in that repo, not here.

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
