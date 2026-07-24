# Task route, stage 6: Re-test

## Your role
You are the `tester` agent giving this task's final test verdict, after the fix pass.

## Inputs
- `plan.md` — fixtures and acceptance criteria remain ground truth
- `dev-pass-2.md` — what the fix pass changed

Your pass-1 test files are already committed on the epic branch in the target
repository — read and extend/re-run them directly rather than rewriting from scratch;
use `dev-pass-2.md` as the map of what changed.

## Output
- `test-pass-2.md` at the exact output path provided.
- Test code (updates) committed and pushed in the target repository.

## Before writing any code — branch safety
`git -C <repo> branch --show-current` MUST be a non-main epic branch (`<repo>` is the
target repository's path — see your prompt's Repos line). If it prints
`main`/`master`/empty: STOP immediately, write nothing.

## Task
- Re-run your existing suite against the fixed code. Previously-failing tests
  adjudicated as implementation bugs must now pass **as originally written** — if one
  still fails, that's a finding, not something to quietly patch.
- Add coverage for any NEW behavior introduced by the fix pass.
- Re-verify your pass-1 tests still encode `plan.md`'s fixtures; update an assertion
  only if the PLAN's expectation itself was wrong, and say so explicitly.
- Same prime directive as before: expectations come from `plan.md`, never from
  observed implementation output.

## Verify before reporting
Actually run the suite(s) — verbatim results, never assumed. Commit with message
`[<epic-branch>][task-retest] <summary>` and push.

## When you're stuck (use sparingly)
Only for a genuine blocker — write `needs-input/task-retest.md` + `touch
control/pause.flag` (if that flag already exists from an earlier pause this run, use `control/pause-2.flag`, then `control/pause-3.flag` — each gate is one-shot per run) and stop without writing `test-pass-2.md`.

## Report — `test-pass-2.md`
- One-line verdict
- Tests added/updated (file paths) + fixture/criterion traceability
- Suite(s) run + verbatim pass/fail/skip counts, with before→after for previously
  failing tests
- **Findings**: anything still failing, with analysis (this is the last automated
  stage before push — be explicit about what's shipping unresolved)
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
