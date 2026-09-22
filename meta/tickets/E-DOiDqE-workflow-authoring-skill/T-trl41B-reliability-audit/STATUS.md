# STATUS

- ID: `T-trl41B-reliability-audit`
- Updated At: `2026-09-21`
- State: Done
- Owner: dev-epic

## This update
C0 audit complete. Research fork surveyed `meta/learning-compact.md` (full), `meta/ROADMAP.md`
(full), grepped `meta/learnings.md` (993 lines) and all `REVIEW*.md`/`STATUS.md` under
`meta/tickets/` (29 keyword-matched files) for decomposition/routing/parallelism/deadlock
patterns, and read `E-gd8m4x-granular-task-decomposition`, `E-Wk9Tz3-task-isolation`,
`E-IasNXu-parallel-execution`, `E-rc7k2v-run-control-routing-breakers` in targeted depth.

**Bucket A (skill-guidance findings, fed into `T-buzXEz-author-skill`):**
1. Task-sizing precedent: `E-gd8m4x-granular-task-decomposition`'s step-contract rules (one
   focused change per step, ~single module, ≤5 files, sized to finish inside an `effort=medium`
   turn budget, hard cap + floor) — reused as the skill's task-sizing anchor.
2. Routers need a real sink per branch (`meta/learnings.md`
   `LRN-20260710-route-sink-required-per-branch`) — `ao validate` requires each route to own a
   terminal task inside its exclusive cone.
3. Unknown-N fan-out needs a fixed-id aggregator, even at N=0
   (`LRN-20260704-dynamic-fanout-fixed-aggregator`).
4. `emit_tasks: true` tasks must set `skip_if_outputs_exist: false` or they silently fail to
   re-inject on a resumed/re-run workflow (`meta/learnings.md`, ~line 302).
5. Parallel write conflicts are the spec author's job with `isolation: none` + `max_parallel > 1`
   (`meta/ROADMAP.md` §4) — corroborated by project memory's real finplan `apis/accounts.rs`
   file-contention incident.
6. Cache-cost vs. parallelism trade-off (Epic B) — parallel tasks sharing a prompt prefix all pay
   full cache-miss cost simultaneously.
7. Isolation caveat: repo `filter`/`merge` git-attribute drivers are not suppressed under
   isolation (`meta/ROADMAP.md` §4 / `E-Wk9Tz3` close-out) — a one-line caution worth including.

**Bucket B (real gaps, spun off, NOT fixed in this epic):**
1. **`E-Grpp0X-injected-task-dag-validation-gap`** (new) — `engine.py::_inject` never re-runs
   `spec.py::cross_validate`'s "unknown depends_on" rule against runtime-injected
   (`emit_tasks`) `TaskSpec` objects; `dag.py::build_dag` silently tolerates an unknown dep with
   a phantom adjacency entry rather than raising. Independently re-verified against current code
   in this session (not taken on the originating 2026-07-04 learning's word alone) — see that
   epic's STATUS.md Evidence for exact line citations.
2. **`E-hbQnU2-isolation-housekeeping-followups`** — found already written, uncommitted, on disk
   at the start of this run (a prior interrupted Epic C attempt under different ticket IDs that
   never committed its own epic scaffolding). Content verified accurate; adopted, with its
   "spun off from" back-reference corrected to this run's actual ticket IDs. Four small isolation
   -subsystem cleanups (worktree-porcelain `-z` parsing, `_pid_alive`/boot-id triplication, a
   possible T2-resolver dispatch-window race, missing `GitRepo.diff_patch()` public method), all
   already-accepted-with-rationale in `docs-md/task-isolation-hld.md` but never ticketed.
3. Global `--model` clobber and E-Wk9Tz3's H-2/H-3/S-1 security findings were checked and
   confirmed ALREADY tracked (ROADMAP §4 / ADR-0003 / the epic's own closing STATUS) — no
   duplicate spin-off filed for these.

By: dev-epic · Role: developer · Date: 2026-09-21 · Comment: Audit complete; both real findings
ticketed, neither implemented (out of this epic's scope per its own rules).

## Evidence
- Fork output (bucket A/B lists above), independently re-verified for the emit_tasks/depends_on
  finding against `src/agent_orchestrator/{engine,dag,spec}.py` (see
  `meta/tickets/E-Grpp0X-injected-task-dag-validation-gap/STATUS.md` for exact citations).
- Spin-off tickets: `meta/tickets/E-Grpp0X-injected-task-dag-validation-gap/`,
  `meta/tickets/E-hbQnU2-isolation-housekeeping-followups/` (adopted/reconciled).

## Risks / Blockers
- None. Time-boxed audit complete; coverage notes recorded above (not exhaustive — targeted at
  DAG/decomposition/parallel/isolation/routing-relevant material, per the task's own scope).

## Next actions
1. None for this task — Done. Findings feed `T-buzXEz-author-skill`.
