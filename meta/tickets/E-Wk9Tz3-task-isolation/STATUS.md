# STATUS

- ID: `E-Wk9Tz3-task-isolation`
- Updated At: 2026-09-06
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

## Evidence
- `docs-md/task-isolation-hld.md` — 1874 lines.
- `docs-md/adr/ADR-0013-per-task-git-isolation-and-rebase-integration.md` — 269 lines.
- 12 task folders under `meta/tickets/E-Wk9Tz3-task-isolation/`, each with `TASK.md` + `STATUS.md`.
- Field evidence incorporated: consumer hotspot data (`apis/accounts.rs` — 23 lifetime / 18
  six-month commits), the collision-avoidance `depends_on` counts (14/15 and 15/20), the 105 GB
  `target/` vs ~18-21 GB free disk constraint, the 4106-entry dirty checkout, and the already-leaked
  `worktree-agent-*` branches + a stray `.worktrees/full-test-*` — each of which changed a design
  decision (D4 ref-based landing, D7 build-cache carve-out, FR-14 GC, D5 `should_skip` rule).

## Risks / Blockers
- Not blocked. Five user decisions are recorded with recommended defaults (EPIC.md "Decisions needed
  from the user"); items 1 and 2 should be confirmed before `T-Ib5Qy9-integrator-core` merges.
- Sprint 2 is planned at ~top-of-band capacity; `T-Tp7Zs2` then `T-Ov9Bt5` are the named descope
  candidates.

## Next actions
1. User confirms (or overrides) the five recorded decisions — especially the integration target and
   the default verify behaviour.
2. Start Sprint 1: `T-Gt4Pw8-git-porcelain` and `T-Sc7Rm2-isolation-schema-models` in parallel (no
   dependencies between them).
3. Optional early gate before `T-Ib5Qy9` merges: `reviewer` + `dev-security` pass over the HLD's
   §7.3 path-guard widening and §8 integration protocol, mirroring the E-GIytcL early-gate pattern.

---
- By: architect · Role: architect · Date: 2026-09-06 · Comment: Design package delivered; epic ready
  for implementation planning. Execution-readiness gate in HLD §19 answers PASS on all four
  questions.
