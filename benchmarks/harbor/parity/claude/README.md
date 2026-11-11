# Shared Claude config for every Harbor arm

Everything in this directory is copied into the trial's fresh
`CLAUDE_CONFIG_DIR` (the user-level `~/.claude` equivalent) for BOTH arms before any
claude session starts: `CLAUDE.md`, `skills/<name>/SKILL.md`, `agents/<name>.md`,
`commands/<name>.md`, `settings.json`. (This README is copied too; Claude Code
ignores it.)

Rule: if it should help one arm, it goes here so it helps both. Never give ao's agents a
skill, agent file or CLAUDE.md the plain Claude Code arm does not also get. Empty by
default: both arms start from a stock Claude Code. After a change, run
`parity_check.py` on a pilot to confirm both arms load the same set.
