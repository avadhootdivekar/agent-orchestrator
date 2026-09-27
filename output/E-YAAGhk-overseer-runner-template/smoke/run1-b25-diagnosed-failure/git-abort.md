# Git branch-off — run `o-3mjumg-smoke1`

## Verdict: **ABORT**

`git-go-ahead.md` was intentionally **not** written.

## Which check failed

The target repository has **no `origin` remote**, and in fact no remotes at all. Several required steps depend on `origin`, so none of them can be done:

- Step 0 `git fetch origin --prune`: fails.
- Mainline detection `git rev-parse --verify origin/main`: fails. The fallback is `master`, but `origin/master` doesn't exist either.
- Procedure 3a (compare `master` with `origin/master` to rule out unpushed or diverged main): can't be checked.
- Procedure 3b (`git push -u origin overseer/o-3mjumg-smoke1`): can't be done.
- Bottom line 5 ("the branch exists on origin" so later waves and `final-push` can push freely) can't be certified.

Every other check passed: HEAD is on a named branch (`master`), and the working tree is clean with no tracked changes and no untracked files.

This is **not** a policy/reality conflict. `branch_policy` is `""`, which means the default auto-detect policy. The failure is purely environmental: there is no remote.

No branch was created and nothing was modified. The repo is exactly as it was found.

## Commands run and output (repo = `workspace/toy-repo`)

```
$ git -C toy-repo remote -v
(no output — no remotes configured)

$ git -C toy-repo fetch origin --prune
fatal: 'origin' does not appear to be a git repository
fatal: Could not read from remote repository.

Please make sure you have the correct access rights
and the repository exists.
(exit 128)

$ git -C toy-repo branch --show-current
master

$ git -C toy-repo status --porcelain --untracked-files=all
(no output — clean, no untracked files)

$ git -C toy-repo rev-parse --verify origin/main
fatal: Needed a single revision
(exit 128)

$ git -C toy-repo rev-parse --abbrev-ref --symbolic-full-name @{u}
fatal: no upstream configured for branch 'master'
(exit 128)

$ git -C toy-repo config branch.master.merge
(no output, exit 1 — branch never tracked a remote)

$ git -C toy-repo rev-parse HEAD
c94a0abc0d85b84e1d225fea9d4c12095f17b014

$ git -C toy-repo branch -a
* master
```

## What a human must do before re-running

1. Give `toy-repo` an `origin` remote that contains its mainline. For a local smoke test, a bare repo is enough:
   ```
   git init --bare /path/to/toy-repo-origin.git
   git -C toy-repo remote add origin /path/to/toy-repo-origin.git
   git -C toy-repo push -u origin master
   ```
2. Keep the working tree clean, on `master`, not ahead of `origin/master`.
3. Run `ao resume` for this run. `git-branch-off` will then create and push `overseer/o-3mjumg-smoke1`.
