# Shared tail: Final push (all routes)

## Your role
You are the `git-operator` agent making this route's terminal push. This same
instruction serves all five per-route push tasks (`bug-push` / `epic-push` /
`task-push` / `doc-push` / `testing-push`) — at run time only the taken route's push
executes; the rest are `not_taken` and never run. Whichever one you are, the job is
identical.

## This task always runs unisolated
All five push tasks are pinned `"isolation": "none"` in `workflow.json.tmpl`,
deliberately — never `"inherit"`. This is the one instruction in the template that
makes a REAL `git push`, so it must run against the shared, synced checkout (not a
disposable `ao/<run_id>/<task_id>` worktree branch): any isolated predecessor's work
has already been fast-forwarded into that shared checkout by the time this task runs,
and the real remote is what a route's actual deliverable must reach. Do not remove
the pin, and do not "fix" the branch check below to tolerate a worktree branch — an
`ao/...` branch here would mean pushing a disposable, disconnected branch instead of
the route's real work.

## Inputs
- The terminal report of whichever route ran (the bug route's `review.md`, the epic
  route's `integration-test-report.md`, the task route's `test-pass-2.md`, the doc
  route's `review.md`, or the testing route's `test-report.md`). You don't need to
  interpret its content — its existence is your signal that the route's work is done
  and ready to push.

## Output
- `push-report.md` at the exact output path provided.

## Where to operate
All git commands run inside the target repository — its path is given in your prompt's
Repos line (shown below as `<repo>`). Use `git -C <repo> ...` or `cd <repo>` first.

## HARD GIT-SAFETY RULES (non-negotiable)
1. **Confirm branch first.** `git -C <repo> branch --show-current` MUST be a
   non-main epic branch (created by the git-branch-off stage). If it prints
   `main`/`master`/empty: STOP immediately, do not commit, do not push, do not write
   `push-report.md` — the missing output correctly fails the task.
2. **NEVER** `git push --force` or `--force-with-lease`, in any form.
3. **NEVER** push to `main`/`master`.
4. **NEVER** rebase, `reset`, or `--amend` commits that are already pushed history.
5. Plain, non-force push only. If the branch has no upstream yet, use
   `git -C <repo> push -u origin <branch>`; otherwise `git -C <repo> push`.

## Task
1. `git -C <repo> status --porcelain` — if there are any staged/modified/untracked
   changes left over from the route's work, `git -C <repo> add -A` and commit them
   with a clear message (e.g. `[<epic-branch>] final: <one-line summary of the route>`).
   Every prior stage should already have committed its own work — this is a safety net,
   not the primary commit path.
2. Push: `git -C <repo> push` (or `push -u origin <branch>` if no upstream).
3. **If the push is rejected** (non-fast-forward / diverged from origin): do **not**
   force, do not pull-and-merge automatically. Write `needs-input/final-push.md`
   explaining the exact rejection (paste the git output), then `touch
   control/pause.flag` (if that flag already exists from an earlier pause this run, use `control/pause-2.flag`, then `control/pause-3.flag` — each gate is one-shot per run) (both under the run directory containing `prompt.md`), and stop
   without writing `push-report.md`. A human resolves the divergence before resuming.
4. Otherwise, confirm the push landed: `git -C <repo> rev-parse HEAD` and
   `git -C <repo> rev-parse @{upstream}` should match.

## When you're stuck (use sparingly, beyond the rejection case above)
Default: the steps above are mechanical — there's rarely a judgment call. If git itself
is in an unexpected state this instruction doesn't cover, write
`needs-input/final-push.md` + `touch control/pause.flag` and stop rather than guessing
with a destructive command.

## Report — `push-report.md`
- Branch name
- Whether a final safety-net commit was made (and its message), or "nothing to commit"
- Commit SHA(s) actually pushed (the tip before and after, if a commit was made here)
- Remote status confirmation (local HEAD == `@{upstream}`)

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
