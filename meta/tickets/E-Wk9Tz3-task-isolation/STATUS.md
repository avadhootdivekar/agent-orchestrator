# STATUS

- ID: `E-Wk9Tz3-task-isolation`
- Updated At: 2026-09-07
- State: Draft
- Owner: architect (agent)

## This update
- Architecture package complete and design-only. Written: `docs-md/task-isolation-hld.md`
  (requirements, landscape survey, HLD, LLD for 11 modules with pseudocode/interfaces/edge cases,
  schema deltas, mermaid block/state/sequence diagrams for the happy path and every conflict tier,
  resume/crash/cancel paths, test plan, acceptance matrix, readiness gate, risks, open questions),
  `docs-md/adr/ADR-0013-per-task-git-isolation-and-rebase-integration.md` (7 decisions + alternatives
  + consequences, referencing ADR-0007), `docs-md/ai-epics/E-Wk9Tz3-task-isolation.md`, this epic
  ticket, and 12 task tickets.
- **No implementation, no commits.** `src/`, `meta/ROADMAP.md`, `CLAUDE.md`, `meta/learnings*.md`,
  existing ADRs and `../ao-runner-finplan` are untouched.
- Design was validated against the real code (`engine.py` @ 2204 lines, `models.py`, `artifacts.py`,
  `runstate.py`, `cli.py`, `specs/*.schema.json`, `templates/builtin/routed-runner/`) and against the
  real consumer's specs, agents, breakdown contract, git history and parallel-development guidelines.

- **2026-09-07 — two review gates completed and fully incorporated (Phases 1 and 2).**
  `REVIEW-design-2026-09-07.md` (reviewer: APPROVE WITH CHANGES — 6 Blocking, 11 Major, 7 Minor) and
  `REVIEW-security-design-2026-09-07.md` (dev-security: conditional pass — 2 Blocking, 3 Major,
  3 Minor, 3 Info). **All 8 Blocking and all 14 Major findings are dispositioned**; per-finding
  outcomes with the location of each fix are in HLD §24 "Review dispositions". No ADR-0013 decision
  (D1-D7) was overturned.
  - **Phase 1** (before development started): `T-Gt4Pw8` and `T-Sc7Rm2` amended for S-1, R-6, R-23 and
    every schema/model field the later fixes need, so the schema lands once. Those two tickets are now
    **frozen** — development is under way against them.
  - **Phase 2** (this update): every remaining finding across the HLD, ADR-0013 and the other 10
    tickets; two new tasks split out of `T-En8Hd4` to keep the 3-day cap; ADR-0007 misquote corrected;
    ADR-0013 **D8** added (the multi-run policy `meta/ROADMAP.md` §3.4 and ADR-0014 both defer here).
  - **Plan changed: 12 tasks / 28 days / 2 sprints -> 14 tasks / 33 days / 3 sprints.** New:
    `T-Ac6Vd9-requeue-accounting` (2 d, R-1 + R-21), `T-Wl2Bq7-workspace-run-lock` (1.5 d, R-4 + R-12).
    Re-estimated: `T-Wk3Nv6` 2.5->3, `T-Cx4Jf1` 2->2.5, `T-Tp7Zs2` 1.5->2. Sprint 3 is deliberately
    under-committed (6.5 d) as the remediation budget for the late gate.
  - The single most consequential finding was **R-19**: `_run_with_retries` computes six of the seven
    remappable path categories internally from the shared store, so as originally pseudocoded an
    "isolated" task would still have read and written the shared checkout while ao created worktrees
    nothing used. It is now `T-En8Hd4`'s first acceptance criterion.

## Evidence
- `docs-md/task-isolation-hld.md` — 1874 lines.
- `docs-md/adr/ADR-0013-per-task-git-isolation-and-rebase-integration.md` — 269 lines.
- 14 task folders under `meta/tickets/E-Wk9Tz3-task-isolation/`, each with `TASK.md` + `STATUS.md`
  (12 original + `T-Ac6Vd9-requeue-accounting` and `T-Wl2Bq7-workspace-run-lock`, both created by the
  Phase-2 review pass).
- Two review reports in this folder, unedited by the epic:
  `REVIEW-design-2026-09-07.md`, `REVIEW-security-design-2026-09-07.md`.
- Field evidence incorporated: consumer hotspot data (`apis/accounts.rs` — 23 lifetime / 18
  six-month commits), the collision-avoidance `depends_on` counts (14/15 and 15/20), the 105 GB
  `target/` vs ~18-21 GB free disk constraint, the 4106-entry dirty checkout, and the already-leaked
  `worktree-agent-*` branches + a stray `.worktrees/full-test-*` — each of which changed a design
  decision (D4 ref-based landing, D7 build-cache carve-out, FR-14 GC, D5 `should_skip` rule).

## Risks / Blockers
- Not blocked; both review gates confirmed the first two tasks could start immediately, and they have.
- Carried from the gates: R-12 (the first barrier's fast-forward on a ~4106-entry dirty checkout — the
  most likely first-adoption failure; diagnostics added, stash-and-restore declined, `T-Ee3Mn8`
  measures it) and R13 (five tasks now edit `engine.py` — mitigated by a fixed edit order and a
  read-the-merged-file rule on every dependent ticket).
- Not blocked. Five user decisions are recorded with recommended defaults (EPIC.md "Decisions needed
  from the user"); items 1 and 2 should be confirmed before `T-Ib5Qy9-integrator-core` merges.
- Sprint 2 is planned at ~top-of-band capacity; `T-Tp7Zs2` then `T-Ov9Bt5` are the named descope
  candidates.

## Next actions
1. Development is under way on `T-Gt4Pw8` and `T-Sc7Rm2` (frozen at Phase 1). `T-Wk3Nv6` and
   `T-Ib5Qy9` can start as soon as those merge.
2. User confirms (or overrides) the five recorded decisions — especially the integration target and
   the default verify behaviour.
3. The early gate is **done** (both reviews above) — the remaining verification is `T-Ee3Mn8`'s late
   gate, which now verifies the *implementation* of S-1..S-6 rather than re-reviewing the design.

---
- By: architect · Role: architect · Date: 2026-09-06 · Comment: Design package delivered; epic ready
  for implementation planning. Execution-readiness gate in HLD §19 answers PASS on all four
  questions.
