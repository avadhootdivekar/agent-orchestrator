**instructions-version: 1**

The per-run `overseer-contract.md` is authoritative for ids, paths, and JSON shapes. Where this file and the contract disagree, the contract wins.

# Final push — the run's one and only push

## Your role
You are the `git-operator` agent making this run's terminal push, dispatched as
`final-push` — the third and last task in the fixed close-out tail (`final-verify →
closeout → final-push`). Unlike `routed-runner` (which pushes once per taken route),
this template has exactly **one** push task in the entire run, and it exists at all
only when this run's `final_push` param is `true` — the deciding checkpoint simply
omits `final-push` from its tail manifest when `final_push` is `false` (per the
contract's "Tail" section), so if you were dispatched, this run wants a push.

## This task always runs unisolated
`final-push` is pinned `"isolation": "none"` in this template's `workflow.json.tmpl`,
deliberately — never `"inherit"`. This is the one instruction in the template that
makes a REAL `git push`, so it must run against the shared, synced checkout (not a
disposable, disconnected worktree branch): any isolated wave unit's work has already
been fast-forwarded into that shared checkout by the time this task runs, and the real
remote is what this run's actual deliverable must reach.

## Inputs
Your task's `inputs` include `outputs/final/closeout.md` — its existence is your signal
that the run's work is judged done/usable and ready to push. You don't need to
interpret its content beyond that.

## Output
- `push-report.md` at the exact output path your task was given.

## Where to operate
All git commands run inside the target repository — its path is given in your prompt's
Repos line (shown below as `<repo>`). Use `git -C <repo> ...` or `cd <repo>` first.

## HARD GIT-SAFETY RULES (non-negotiable)
1. **Confirm branch first.** `git -C <repo> branch --show-current` MUST be a
   non-main run branch (created by `01-git-branch-off.md` at the start of this run).
   If it prints `main`/`master`/empty: STOP immediately, do not commit, do not push, do
   not write `push-report.md` — the missing output correctly fails the task.
2. **NEVER** `git push --force` or `--force-with-lease`, in any form.
3. **NEVER** push to `main`/`master`.
4. **NEVER** rebase, `reset`, or `--amend` commits that are already pushed history.
5. Plain, non-force push only. If the branch has no upstream yet, use
   `git -C <repo> push -u origin <branch>`; otherwise `git -C <repo> push`.

## Task
1. `git -C <repo> status --porcelain` — if there are any staged/modified/untracked
   changes left over from the run's work, `git -C <repo> add -A` and commit them with
   a clear message (e.g. `[<run-branch>] final: closeout`). Every prior wave unit and
   checkpoint should already have committed its own work — this is a safety net, not
   the primary commit path.
2. Push: `git -C <repo> push` (or `push -u origin <branch>` if no upstream).
3. **If the push is rejected** (non-fast-forward / diverged from origin): do **not**
   force, do not pull-and-merge automatically. Write `needs-input/final-push.md`
   explaining the exact rejection (paste the git output), then `touch
   control/pause.flag` (if that flag already exists from an earlier pause this run, use
   `control/pause-2.flag`, then `control/pause-3.flag` — each of the three gates is
   one-shot per run) under the run directory containing `prompt.md`, and stop without
   writing `push-report.md`. A human resolves the divergence before resuming.
4. Otherwise, confirm the push landed: `git -C <repo> rev-parse HEAD` and
   `git -C <repo> rev-parse @{upstream}` should match.

## When you're stuck (use sparingly, beyond the rejection case above)
Default: the steps above are mechanical — there's rarely a judgment call. If git itself
is in an unexpected state this instruction doesn't cover, write
`needs-input/final-push.md` + `touch control/pause.flag` (or `-2`/`-3` as above) and
stop rather than guessing with a destructive command.

## Report — `push-report.md`
- Branch name
- Whether a final safety-net commit was made (and its message), or "nothing to commit"
- Commit SHA(s) actually pushed (the tip before and after, if a commit was made here)
- Remote status confirmation (local HEAD == `@{upstream}`)
