# /git-commit-push — Safe stage, commit, and push helper

When this command is invoked, execute the following steps **in order**. Stop and report at each gate before proceeding.

---

## Step 1: Branch safety check

Run:
```bash
git rev-parse --abbrev-ref HEAD
```

- If the result is `master` or `main`: **STOP**. Report: "Currently on master/main. Cannot commit directly. Please switch to or create a feature branch." Do NOT create a branch automatically — wait for explicit user instruction.
- If on any other branch: proceed to Step 2.

---

## Step 2: Remote sync check

Run:
```bash
git fetch origin 2>&1; git status -sb 2>&1
```

Evaluate the result:
- If the remote branch **does not exist** (e.g. "no upstream configured" or remote ref missing): **STOP** and ask: "Remote branch does not exist for this branch. Push as new branch, or cancel?"
- If local and remote have **diverged** (local has commits remote doesn't AND remote has commits local doesn't): **STOP** and ask: "Local and remote have diverged. Options: (1) Rebase onto remote, (2) Cancel. Do NOT force-push." Wait for explicit user choice.
- If remote is **ahead only** (local is behind remote, no local commits beyond remote): **STOP** and ask: "Remote is ahead — pull first to avoid conflicts? Or cancel?"
- If local is **ahead only** or **in sync**: proceed to Step 3.

---

## Step 3: Inspect and stage changes

Run:
```bash
git status 2>&1
```

For each file in the output:
- **Known temp/generated files to gitignore instead of staging**: `*.swp`, `*.tmp`, files named `Untitled`, `__pycache__/`, `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/`, coverage/report output (auto-generated artifacts). Add any such files/patterns to `.gitignore` if not already there.
- **Sensitive files** (`.env`, `*.key`, `*credentials*`, `*secret*`): skip and warn the user.
- **All other modified/new files** relevant to the current work: stage them.

Stage files selectively. Never use `git add -A` or `git add .` blindly. Prefer `git add <specific-files>`.

After staging, run `git diff --cached --stat` to confirm what will be committed.

---

## Step 4: Commit

Write a concise commit message that:
- Summarizes **what** changed and **why** (not just what files were touched)
- Follows the repository's existing commit message style (check `git log --oneline -5` first)

Use a heredoc to pass the message:
```bash
git commit -m "$(cat <<'EOF'
<commit message here>

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Step 5: Push (no force)

Run:
```bash
git push origin HEAD
```

**NEVER** use `--force` or `--force-with-lease`. If the push is rejected:
- Report the exact error.
- Ask the user how to proceed.
- Do NOT attempt to force-push.

---

## Summary output

After completing all steps, report a one-line summary: branch name, number of files committed, and whether the push succeeded.
