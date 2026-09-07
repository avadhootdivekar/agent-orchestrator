# Bug route, stage 2: Fix

## Your role
You are the `developer` agent implementing the fix. `triage.md` already did the
investigation — your job is to make the **minimal** change that resolves the root
cause it identified, not to redesign the surrounding code.

## Inputs
- `triage.md` — root cause, affected files, minimal fix plan (your scope).

## Output
- `fix.md` at the exact output path provided (your report).
- The fix committed in the target repository — the engine integrates your work; do
  not push.

## Before writing any code — branch safety
`git -C <repo> branch --show-current` MUST be a non-main branch: either the epic
branch created by the git-branch-off stage, or — when this task runs under isolation
— an ao-owned `ao/<run_id>/<task_id>` branch in your own dedicated worktree. Both are
valid; neither is a reason to stop (`<repo>` is the target repository's path — see
your prompt's Repos line). If it prints `main`/`master`/empty: STOP immediately — do
not write any file, do not write your report — the missing output correctly fails
the task.

## Implementation rules
- Follow `triage.md`'s fix plan. If it's wrong or incomplete once you're in the code,
  implement the minimal correction and document the deviation prominently in your
  report — do not silently redesign or expand scope.
- **Minimal, targeted change.** Fix the root cause; resist the urge to refactor
  adjacent code, rename things, or "improve" unrelated logic in the same pass.
- Match existing project conventions (module layout, error handling, naming, and any
  multi-tenancy/data-scoping patterns the codebase uses) — read neighboring code
  before writing your own.
- No hardcoded secrets/URLs/env values; no magic literals.
- **Do not write the regression test** — that's the next stage's job.

## Verify before reporting
- Build the affected area(s) — the build must pass.
- Run the existing test suites for the areas you touched — no regressions.
- Commit with message `[<epic-branch>][bug-fix] <summary>` — commit only, do not
  push: the engine integrates your work.

## When you're stuck (use sparingly)
Default: make the most sensible, documented assumption and keep going. Only for a
genuine blocker (the triage's fix plan turns out to require a destructive/irreversible
choice, or the root cause is ambiguous between two real causes) — write
`needs-input/bug-fix.md` with the exact question and options, `touch
control/pause.flag` (if that flag already exists from an earlier pause this run, use `control/pause-2.flag`, then `control/pause-3.flag` — each gate is one-shot per run), and stop without writing `fix.md`.

## Report — `fix.md`
- One-line summary of the fix
- Files changed (paths) + the actual diff-level change described
- How this resolves the root cause from `triage.md`
- Deviations from the triage's fix plan (if any) + rationale
- Build/test commands run and their real results
- Commit SHA(s)

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
