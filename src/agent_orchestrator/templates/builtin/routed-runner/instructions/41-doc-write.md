# Documentation route, stage 2: Write

## Your role
You are the `developer` agent writing the documentation per `plan.md`.

## Inputs
- `plan.md` — scope, target files, outline, and the source-of-truth mapping per section.

## Output
- `draft.md` at the exact output path provided (your summary report).
- The actual documentation files created/updated, committed in the target repository
  — the engine integrates your work; do not push.

## Before writing anything — branch safety
`git -C <repo> branch --show-current` MUST be a non-main branch: either the epic
branch created by the git-branch-off stage, or — when this task runs under isolation
— an ao-owned `ao/<run_id>/<task_id>` branch in your own dedicated worktree. Both are
valid; neither is a reason to stop (`<repo>` is the target repository's path — see
your prompt's Repos line). If it prints `main`/`master`/empty: STOP immediately, do
not write any file — the missing output correctly fails the task.

## Task
- Write/update exactly the target files `plan.md` names, following its outline.
- For every factual claim (an API shape, a config option, a flow, a default value):
  verify it against the actual current code before writing it down. If `plan.md`'s
  outline assumed something that isn't true in the code, document the real behavior
  and note the correction in your report — never write what you wish were true.
- Match the surrounding doc's existing tone, heading style, and structure
  (`docs-md/` conventions if that's the target).
- This route is docs-only: do not change any behavior-carrying code. If writing
  accurate docs surfaces a bug or gap, note it in your report as a finding — do not fix
  it here.

## Verify before reporting
Re-read what you wrote once against the live code, section by section — this is your
own accuracy pass before the review stage's independent one. Commit with message
`[<epic-branch>][doc-write] <summary>` — commit only, do not push: the engine
integrates your work.

## When you're stuck (use sparingly)
Default: make the most sensible, documented assumption and keep going. Only for a
genuine blocker (you cannot verify a claim `plan.md` requires without access you don't
have) — write `needs-input/doc-write.md` + `touch control/pause.flag` (if that flag already exists from an earlier pause this run, use `control/pause-2.flag`, then `control/pause-3.flag` — each gate is one-shot per run) and stop without
writing `draft.md`.

## Report — `draft.md`
- One-line summary
- File paths created/modified
- Corrections made vs. `plan.md`'s outline (if the real code differed from what was
  planned) + rationale
- Any behavior gaps/bugs noticed while verifying claims (report only, don't fix)
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
