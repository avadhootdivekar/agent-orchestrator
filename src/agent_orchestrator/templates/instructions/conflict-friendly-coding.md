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
