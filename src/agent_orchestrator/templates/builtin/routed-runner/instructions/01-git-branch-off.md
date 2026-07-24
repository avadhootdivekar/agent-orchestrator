# Stage 1: Git branch-off — verify a safe working branch

## Your role
You are the git-operator agent. You verify (and if needed, create) the branch that this
entire run will build on. Every later stage commits to this branch, so if the git
state is unsafe, the whole workflow must stop **here**.

## Inputs
- `prompt.md` — the user's prompt (its path tells you the run id: `prompt.md` lives
  directly inside the run directory, so the run directory's own name is `<epic-id>`).

## Output
- `git-go-ahead.md` at the exact output path provided — **written ONLY if every check
  passes**. If any check fails, do NOT write this file (its absence fails the workflow,
  by design). Instead write `git-abort.md` in the same directory explaining the failure.

## Where to operate
All git commands run inside the target repository — its path is given in your prompt's
Repos line (shown below as `<repo>`). Use `git -C <repo> ...` or `cd <repo>` first.

## Procedure

1. **Snapshot state (read-only).**
   - `git -C <repo> fetch origin --prune`
   - `git -C <repo> branch --show-current` — if empty (detached HEAD) → **ABORT**.
   - `git -C <repo> status --porcelain`

2. **Dirty-tree check.** If `status --porcelain` shows any staged or modified tracked
   files → **ABORT** (do not stash, do not commit — a human must decide what to do with
   that work). Untracked files alone do not block: list them in the report and continue.

3. **If the current branch is `main` or `master`:**
   a. Compare with its remote: `git -C <repo> rev-list --left-right --count origin/<branch>...<branch>`.
      - Local ahead of or diverged from origin → **ABORT** (never build on unpushed main).
      - Local behind → `git -C <repo> pull --ff-only`.
   b. Branch off: create `epic/<epic-id>` (using the run id derived from the prompt.md
      path), check it out, and `git -C <repo> push -u origin epic/<epic-id>`.

4. **If the current branch is already a non-main branch:**
   a. If it has an upstream (`git -C <repo> rev-parse --abbrev-ref @{upstream}` succeeds):
      - behind only → `git -C <repo> pull --ff-only`
      - ahead only → `git -C <repo> push` (local commits are our own work; sync them)
      - **diverged (both ahead and behind) → ABORT.** Never rebase/merge/force to "fix" it.
   b. If it has NO upstream, decide whether the remote branch was deleted or never existed:
      - If `git -C <repo> config branch.<name>.merge` is set (it *used to track* a remote
        branch) and that remote ref no longer exists after the fetch --prune → the remote
        branch was **deleted on origin** → **ABORT** (this branch is stale/abandoned).
      - Otherwise (never had an upstream) → `git -C <repo> push -u origin <branch>`.

5. **Bottom line to certify:** we end on a non-main branch, with no uncommitted tracked
   changes, not diverged from origin, and the branch exists on origin — so all later
   stages can commit + push freely and the branch can eventually merge to main.

## Report format

`git-go-ahead.md` (success only):
- Verdict: **GO**
- Branch name + whether it was created by this run or pre-existing
- HEAD SHA
- Remote sync state (up to date / pulled N commits / pushed N commits)
- Untracked files present (list or "none")
- Every git command run, with its relevant output

`git-abort.md` (failure only — and do NOT write git-go-ahead.md):
- Verdict: **ABORT**
- Which check failed, the exact command output proving it
- Precisely what a human must do before re-running the workflow
