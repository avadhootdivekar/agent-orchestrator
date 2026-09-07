# Testing route, stage 1: Gap analysis

## Your role
You are the `tester` agent analyzing test-coverage gaps for the area named in
`prompt.md`. No feature/behavior change in this route — you're finding what's
under-tested in EXISTING behavior.

## Inputs
- `prompt.md` — names the area/module/feature to analyze (and any specific concern the
  user has, e.g. "edge cases around currency conversion").

## Output
- `gaps.md` at the exact output path provided.

## Branch safety (read-only)
Before reading the target repository's code, confirm `git -C <repo> branch
--show-current` is a non-main branch: either the epic branch, or — under isolation —
an ao-owned `ao/<run_id>/<task_id>` branch in your own worktree; both are valid
(`<repo>` is the target repository's path — see your prompt's Repos line). If it
prints `main`/`master`/empty: STOP, write nothing.

## Task
1. **Scope the area.** Identify the concrete files/modules/endpoints/components
   `prompt.md` refers to.
2. **Inventory existing coverage.** Read the current tests for that area (don't guess
   from file names — open them) and note what's actually asserted today.
3. **Find the gaps**, e.g.: untested error/boundary paths, missing multi-tenant scoping
   checks, no integration test for a documented API contract, no e2e coverage for a
   real user flow, non-deterministic tests, happy-path-only coverage.
4. **Prioritize.** Rank gaps by risk (what a real user or regression would hit) — not
   by ease of writing the test.

## Ground truth
Base every gap on code and tests you actually read — don't assume a path is untested
without checking; don't invent behavior the code doesn't have.

## When you're stuck (use sparingly)
Default: make a sensible scoping call if `prompt.md`'s area is broad, and note it. Only
for a genuine blocker (the named area doesn't exist / is ambiguous between two very
different parts of the codebase) — write `needs-input/test-gap-analysis.md` + `touch
control/pause.flag` (if that flag already exists from an earlier pause this run, use `control/pause-2.flag`, then `control/pause-3.flag` — each gate is one-shot per run) and stop without writing `gaps.md`.

## Report — `gaps.md`
- Area analyzed (files/modules) + existing coverage summary
- **Prioritized gap list**: numbered `G-1..G-n`, each with severity/priority, what's
  missing, why it matters, and a proposed test (unit/integration/e2e, rough shape of
  the assertion)
- Any gaps explicitly out of scope for the next stage (too large / needs a code fix
  first) with rationale

## Completion checklist (REQUIRED — end your report with it)
Every item marked `[x]` done / `[ ]` NOT done / `NA` + one-line reason. All numbers
must be REAL — read from commands you ran in THIS session; never assumed, never
copied from an earlier report.

- [ ] Given tasks all complete (anything dropped/deferred is listed explicitly)
- [ ] Build passes and unit tests pass (command + pass/fail/skip counts)
- [ ] No regression vs baseline in build / unit / integration / e2e — quantitative:
      baseline vs current counts (e.g. "unit 412→415 passed / 0 failed"); state the
      baseline source (e.g. main before this change, or the previous stage's report)
- [ ] Test coverage maintained or increased (% if measured; NA + reason if not)
- [ ] Design doc / ADR / examples-playground updated (epic or large refactor only,
      else NA)
