# Conflict-friendly coding rules

Add this file via the **existing** `general_instructions` mechanism (`.ao/config.yaml`,
`AO_GENERAL_INSTRUCTIONS`, `--general-instruction`, or a workflow spec's own
`general_instructions:`) so every task in the workspace sees it, regardless of agent or
instruction. These rules make most collisions **mechanical before they happen** — the point
is that a later union/rebase resolves cleanly instead of conflicting.

1. **New file over hub edit.** Prefer a new module, new test file, or new fixture over
   editing a file other tasks are also likely to touch.
2. **Registrations are append-only.** One entry per line, trailing newline. Never reorder,
   never renumber, never re-sort an existing list. This is what makes a mechanical union
   resolver correct.
3. **No drive-by reformatting.** No import re-sorting, no unrelated renames, no
   whitespace-only hunks. A formatting-only change in a shared file turns a free merge
   into a conflict.
4. **Keep the diff focused.** Touch only the files your task needs. Do not commit build
   output or lockfiles you did not intentionally change.
5. **Additive-first data/model changes.** Prefer optional fields, default arguments, and
   `#[serde(default)]`-style additions so a sibling task's code still compiles/runs
   against your change.
6. **Worktree hygiene.** You are on a dedicated branch in a dedicated worktree: commit
   freely, but never `git checkout` another branch, never `git stash`, never `git push`,
   and never `rebase`/`reset` history the engine created.
7. **Report a `touches` mismatch.** If your task's real file set differs from its
   `touches` hint, say so in your output — the hint is advisory, and correcting it helps
   the next run schedule better.

## Auto-commit and secrets

The engine auto-commits everything **staged** in your worktree at task end — do not rely
on `.gitignore` to keep something out of a commit. Do not write secret material into a
worktree at all; `integration.commit_denylist` is a backstop against a stray untracked
path, not a guarantee.

## Before you enable isolation on a repository (read this once, per repo)

This section is for whoever turns isolation **on** for a workspace, not for the task agent.

**What the engine suppresses.** Every git command the engine runs carries
`-c core.hooksPath=<an always-empty directory>`, so no repository-local hook — `pre-commit`,
`post-checkout`, `commit-msg`, `post-rewrite` — ever fires from an engine-issued call. This
was verified by planting six hooks in a fixture repo: a plain `git commit` fired eight hook
invocations, and the engine's `worktree_add` + `add_all` + `commit` fired zero. The engine
also cannot run `push`, `fetch`, `pull`, `clone`, `remote` or `credential`; those
subcommands are rejected at the single choke point, alias spellings included.

**What it does not suppress — a documented limitation, not an oversight.** The engine does
**not** neutralize `filter.*` or `merge.*` drivers configured through git attributes. A
repository that has `git-lfs`, `git-crypt`, `nbstripout` or any similar tool bootstrapped
installs a `filter.<name>.clean` / `.smudge` **command into git config** plus a `filter=`
attribute in a tracked `.gitattributes`; `git rebase` likewise honours a `merge=<driver>`
attribute and runs `merge.<driver>.driver` from config. The engine executes those commands
automatically — **once per task, unattended, and concurrently across worktrees**. In the
same fixture, engine `worktree_add` ran the configured smudge filter and `add_all` ran the
clean filter four times, while hooks stayed silent throughout.

**Why this is bounded.** The command always comes from **git config**, never from tracked
content alone, so cloning a hostile repository cannot inject one. The exposure is exactly
the case the hook suppression was written for: a repository the operator has already
bootstrapped by running someone's setup step.

**What to do about it.** Do not run a repository under isolation if its configured filter
or merge drivers are untrusted, or if they are expensive enough that running them once per
task across N concurrent worktrees is a problem. If you need isolation on such a repository,
unconfigure the driver for the duration, or keep the affected stages at
`isolation: "none"` so they run in the shared checkout where the driver already ran once.

**Two settings that are not free-form.**

- `refs/heads/ao/**` and `refs/ao/**` are **reserved for the engine**. Do not create,
  move or delete branches or refs there by hand, and do not point cleanup tooling at them:
  a branch found in that namespace is treated as the current run's own crash-recovered
  work and is re-attached, never deleted. Use `ao prune` to reclaim them.
- `AO_WORKTREE_ROOT` (and `.ao/config.yaml`'s `isolation.state_dir`) **must not resolve
  inside the workspace root.** Worktrees live outside every working tree by design: that is
  what lets the per-task artifact guard treat "inside my worktree" and "inside the shared
  workspace" as different places. Point the worktree root inside the workspace and the
  guard stops separating them — one task could then read another task's worktree by path,
  and the isolation you configured would not be there. The engine now **refuses** an
  overlapping configuration outright, with an error naming the resolved worktree root, the
  workspace root, and which setting produced it; the reverse overlap (a worktree root that
  *contains* the workspace, such as `AO_WORKTREE_ROOT=/`) is refused for the same reason.
  Pick a location on a different tree — the default,
  `~/.local/state/ao/worktrees/...`, is already correct.
