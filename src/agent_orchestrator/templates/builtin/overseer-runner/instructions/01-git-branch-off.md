**instructions-version: 1**

The per-run `overseer-contract.md` is authoritative for ids, paths, and JSON shapes. Where this file and the contract disagree, the contract wins.

# Git branch-off — verify a safe working branch

## Your role
You are the `git-operator` agent. You verify (and if needed, create) the single branch
this entire overseer-runner run builds on — every wave unit, every checkpoint, and the
close-out tail eventually commit to this one branch (there is no per-route branching in
this template, unlike `routed-runner`). If the git state is unsafe, the whole workflow
must stop **here**, before `intake` ever locks a charter.

## Inputs
- `prompt.md` — the user's prompt. Its path tells you this run's id: `prompt.md` lives
  directly inside the run's own instance directory, so that directory's own last path
  segment is this run's id. Use it as `<run-id>` below — never hardcode a literal path.
- `overseer-config.json` — its `branch_policy` field (Step 0 below).

## Output
- `git-go-ahead.md` at the exact output path your task was given — **written ONLY if
  every check below passes**. If any check fails, do NOT write this file (its absence
  correctly fails this task, and therefore the whole run, resumably). Instead write
  `git-abort.md` in the same directory explaining the failure.

## Where to operate
All git commands run inside the target repository — its path is given in your prompt's
Repos line (shown below as `<repo>`). Use `git -C <repo> ...` or `cd <repo>` first.

## Step 0 — Read the branch policy, then gather facts BEFORE deciding anything

Read `overseer-config.json`'s `branch_policy` field. It is free text (or empty/absent),
e.g. `"always branch fresh off main"`, `"keep working on the current branch unless it's
already merged"`, `"never create a branch, fail if dirty"` — these are illustrative
examples, not an exhaustive enum; apply the intent of whatever text is actually there.
**Empty/absent means the default auto-detect policy** described in "Procedure" below —
skip straight to Step 1 if so.

Whether or not a policy is set, gather these facts FIRST, deterministically, before
applying any policy text — never guess at git state or infer it from the policy text
alone:
- `git -C <repo> fetch origin --prune`
- `git -C <repo> branch --show-current` — **empty means detached HEAD → ABORT
  immediately, unconditionally, regardless of any policy text.** A detached HEAD has no
  branch identity for any policy to reason about, and every later step below assumes a
  named current branch.
- `git -C <repo> status --porcelain` — any staged/modified TRACKED file means dirty.
- Determine this repo's mainline branch name, `<mainline>`: `main` if `git -C <repo>
  rev-parse --verify origin/main` succeeds, else `master`. Use `<mainline>` (not a
  hardcoded literal) in every check below, since a repo may use either.
- Is the current branch literally `<mainline>`?
- Is the current branch already fully merged into `<mainline>`?
  `git -C <repo> merge-base --is-ancestor HEAD origin/<mainline>` — **exit code 0 means
  yes, merged**; nonzero means no. This is the only correct way to answer "is this
  branch's work already in main" — comparing commit hashes or branch names is unreliable
  and must not be used. (Not meaningful, and not needed, when the current branch already
  IS `<mainline>`.)
- Does the current branch have an upstream?
  `git -C <repo> rev-parse --abbrev-ref --symbolic-full-name @{u}` — failure means it has
  never been pushed / has no upstream.

Now combine these facts with the policy text to decide ONE of: **branch fresh off
updated `<mainline>`**, **continue on the current branch**, or — only if the policy's
intent and the actual git state genuinely conflict (for example the policy demands a
guaranteed-clean fresh branch but the tree is dirty with unrelated, unrelated-to-this-run
uncommitted work) — **stop and ask a human**, via the same `git-abort.md` mechanism
already defined below (this task runs before `intake` ever locks a charter, so there is
no checkpoint yet to hold through the overseer's own `control/hold-request.json`
mechanism — `git-abort.md` plus not writing `git-go-ahead.md` IS this task's
human-in-the-loop pause point: the task fails, the run halts resumably, and a human
decides before `ao resume`). Illustrative (not exhaustive) readings:
- *"always branch fresh off main"* → do Procedure step 3's branch-off-from-`<mainline>`
  behavior even if currently on a non-mainline branch (first confirm that branch isn't
  dirty with work that would otherwise be silently orphaned — if it is, that is a
  genuine conflict; ask, don't discard).
- *"keep working on the current branch unless it's already merged"* → if currently ON
  `<mainline>` itself, there is no "current branch" to keep — do Procedure step 3 as
  usual. Otherwise, if the merge-base check shows the current (non-mainline) branch is
  already merged into `<mainline>`, branch fresh off updated `<mainline>` instead (that
  old branch's work already landed, so continuing on it would build on stale history);
  otherwise do Procedure step 4 (continue/sync).
- *"never create a branch, fail if dirty"* → never do Procedure step 3's branch-off
  action. If currently on `<mainline>` itself, this policy is unsatisfiable safely (this
  template never builds directly on `<mainline>`) — **ABORT**, explaining the conflict.
  If already on a non-mainline branch, proceed using it as-is (the dirty-tree check
  already covers "fail if dirty").

**Never destructive, regardless of policy.** No `git reset --hard`, no discarding
uncommitted or unpushed work, no force-push, ever — to satisfy a policy or otherwise. If
honoring the policy would require any of those, that is exactly the "genuinely conflict"
case above: stop and ask, don't force it.

## Procedure (the default auto-detect policy, and the mechanics referenced above)

Steps 1-2 below are unconditional — they apply exactly the same way regardless of which
policy reading Step 0 chose (branch-fresh / continue / neither yet decided). Only step 3
vs. step 4 branches on the policy's decision.

1. **Snapshot state (read-only) and the detached-HEAD gate.** Reuse Step 0's
   fact-gathering above — do not re-run the same commands twice. If `branch --show-current`
   came back empty (detached HEAD), you already **ABORT**ed in Step 0 and never reach
   here.

2. **Dirty-tree check.** If `status --porcelain` shows any staged or modified tracked
   files → **ABORT** (do not stash, do not commit — a human must decide what to do with
   that work). Untracked files alone do not block: list them in the report and continue.
   This check is unconditional across every policy reading, including "never create a
   branch" (which already leans on it) — a dirty tree always aborts, regardless of what
   the policy says about branching.

3. **If the current branch is `main` or `master`:**
   a. Compare with its remote: `git -C <repo> rev-list --left-right --count origin/<branch>...<branch>`.
      - Local ahead of or diverged from origin → **ABORT** (never build on unpushed main).
      - Local behind → `git -C <repo> pull --ff-only`.
   b. Branch off: create `overseer/<run-id>` (the run id derived above), check it out,
      and `git -C <repo> push -u origin overseer/<run-id>`.

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
   changes, not diverged from origin, and the branch exists on origin — so every later
   wave unit, checkpoint, and the tail's `final-push` can commit + push freely and the
   branch can eventually merge to main.

## Report format

`git-go-ahead.md` (success only):
- Verdict: **GO**
- `branch_policy` as read from `overseer-config.json` (or "none (default auto-detect)")
  and how it was applied (branched fresh off main / continued on current branch, and why)
- Branch name + whether it was created by this run or pre-existing
- HEAD SHA
- Remote sync state (up to date / pulled N commits / pushed N commits)
- Untracked files present (list or "none")
- Every git command run, with its relevant output (include the Step 0 fact-gathering
  commands, not just Procedure's)

`git-abort.md` (failure only — and do NOT write git-go-ahead.md):
- Verdict: **ABORT**
- Which check failed, the exact command output proving it
- If this was a policy/reality conflict (Step 0's "genuinely conflict" case): quote the
  policy text and state exactly what fact contradicted it
- Precisely what a human must do before re-running the workflow
