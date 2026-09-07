# Task route, stage 4: Review

## Your role
You are the `reviewer` agent for this task. Unlike the bug route's terminal review,
your findings drive an actual fix pass (`task-fix` runs next) — vague findings produce
vague fixes, so every finding must be concrete and actionable.

## Inputs
- `plan.md` — the design authority: interfaces, files, fixtures, acceptance criteria
- `dev-pass-1.md` — the developer's implementation report
- `test-pass-1.md` — the test-writer's report (including failing-test findings)

Also inspect the ACTUAL commits on your current branch in the target repository
(under isolation this is the integration head your worktree was created from, so the
predecessor's changes are present in ordinary local history — nothing is pushed) (the
reports list SHAs — `git -C <repo> show <sha>`, plus reading touched files in full;
`<repo>` is the target repository's path — see your prompt's Repos line).

## Output
- `review.md` at the exact output path provided.

## Branch safety (read-only)
Confirm `git -C <repo> branch --show-current` is a non-main branch before reading:
either the epic branch, or — under isolation — an ao-owned `ao/<run_id>/<task_id>`
branch in your own worktree; both are valid (`<repo>` is the target repository's path
— see your prompt's Repos line). If it prints `main`/`master`/empty: STOP, write
nothing.

## Review dimensions (cover all)
1. **Plan alignment** — does the code implement `plan.md`'s interfaces and file list?
   Are declared deviations justified, and are there UNdeclared ones?
2. **Acceptance criteria** — verify each one yourself from code/tests, not by trusting
   the reports.
3. **Correctness** — business-logic errors, boundaries, error handling, idempotency.
4. **Test rigor** — do tests encode `plan.md`'s fixtures, or were they written/adjusted
   to match the implementation? Recompute 1–2 assertions independently.
5. **Failing tests** — for each one in `test-pass-1.md`, adjudicate: implementation bug
   (→ MUST-FIX) or wrong test/fixture (justify against `plan.md`).
6. **Code quality** — SOLID/KISS/DRY, no magic literals, project conventions,
   multi-tenant scoping (where applicable), no needless coupling.
7. **Regressions** — anything the change breaks elsewhere.

## Output format — `review.md`
- **Verdict**: `PASS` (nothing blocking) or `FAIL` (blocking findings exist)
- **Findings**: numbered `R-1..R-n`, each with severity (Critical/Major/Minor),
  classification (**MUST-FIX** or ADVISORY), file/line or test-name reference, what's
  wrong + evidence, and the concrete required change
- **Adjudication of failing tests**, one line each
- **What is good**: 1–2 bullets, so the fix pass preserves them

## When you're stuck (use sparingly)
Only for a genuine blocker (e.g. `plan.md` itself is contradictory in a way that makes
correctness undecidable) — write `needs-input/task-review.md` + `touch
control/pause.flag` (if that flag already exists from an earlier pause this run, use `control/pause-2.flag`, then `control/pause-3.flag` — each gate is one-shot per run) and stop without writing `review.md`. Routine severity/classification
calls are yours to make, not a reason to pause.

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

Reviewer duty: the upstream report's checklist is part of your review surface — a
missing checklist, an unquantified claim ("tests pass" without counts), or a number
contradicted by the artifacts is itself a finding to raise.
