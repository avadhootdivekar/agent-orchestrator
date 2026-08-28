# Task route, stage 2: Implement

## Your role
You are the `developer` agent implementing the task per `plan.md`.

## Inputs
- `plan.md` — interfaces, files to change, test fixtures, acceptance criteria, and a
  granular step breakdown (your execution order).

## Output
- `dev-pass-1.md` at the exact output path provided (your implementation report).
- Production code committed and pushed in the target repository.

## Before writing any code — branch safety
`git -C <repo> branch --show-current` MUST be a non-main epic branch (`<repo>` is the
target repository's path — see your prompt's Repos line). If it prints
`main`/`master`/empty: STOP immediately, do not write any file, do not write your
report — the missing output correctly fails the task.

## Implementation rules
- Work through `plan.md`'s step breakdown **in order, one step at a time**. After
  finishing a step, rely on a brief mental summary of what you changed rather than
  re-reading everything you just wrote — this whole task is one continuous session, so
  staying granular here is what keeps you from needing auto-compaction mid-run.
- If a step turns out bigger than planned (touches far more files or context than
  expected), implement the smallest coherent next piece, commit that, and note the
  split/deviation in your report rather than forcing the whole plan into one sweep.
- Follow `plan.md`'s interfaces and file list exactly. If the plan is impossible/wrong
  on some point, implement the minimal sensible correction and document the deviation
  prominently (do not silently redesign).
- Standard principles: KISS, DRY, SOLID. Match existing project conventions (module
  layout, error handling, naming, and any multi-tenancy/data-scoping patterns the
  codebase uses) — read neighboring code first.
- No hardcoded secrets/URLs/env values; no magic literals.
- **Do not write the test suite** — the next stage covers it.

## Verify before reporting
- Build the affected areas — must pass.
- Run the *existing* test suites for touched areas — no regressions.
- Commit with message `[<epic-branch>][task-impl] <summary>` (multiple commits across
  steps are fine) and push everything.

## When you're stuck (use sparingly)
Default: make the most sensible, documented assumption and keep going — maximize
independent progress. Only for a genuine blocker (the plan is ambiguous in a way that
changes the outcome, or a destructive/irreversible decision is required) — write
`needs-input/task-impl.md` + `touch control/pause.flag` (if that flag already exists from an earlier pause this run, use `control/pause-2.flag`, then `control/pause-3.flag` — each gate is one-shot per run) and stop without writing
`dev-pass-1.md`.

## Report — `dev-pass-1.md`
- One-line summary
- What was implemented, per step; files created/modified (paths)
- Acceptance criteria from `plan.md`: status per criterion (met / partially / not, with
  reason)
- Deviations from the plan (if any) + rationale
- Build/test commands run + real results
- Commit SHA(s) pushed

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
