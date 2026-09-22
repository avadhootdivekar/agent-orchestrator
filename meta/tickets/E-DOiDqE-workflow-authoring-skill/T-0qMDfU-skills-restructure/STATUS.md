# STATUS

- ID: `T-0qMDfU-skills-restructure`
- Updated At: `2026-09-21`
- State: Done
- Owner: dev-epic

## This update
`.claude/skills/workflow-authoring/SKILL.md` created in its own subdirectory, matching
`repo-intel`'s `<skill>/SKILL.md` layout — no restructuring of `repo-intel` itself needed.
No top-level skills README/index added: two skills doesn't warrant a registry (proportionate,
explicit "not needed" decision, matching this task's own acceptance criteria). `CLAUDE.md`'s
"Skills reference" table gained one row pointing at the new skill.

## Evidence
- `.claude/skills/` now contains `repo-intel/SKILL.md` and `workflow-authoring/SKILL.md`, each
  in its own directory.
- `CLAUDE.md` diff: added one row to the "Skills reference" table (line ~165) — verified by
  reading the file before and after the edit.
- No README/index file added — decision recorded here rather than silently skipped.

## Risks / Blockers
- None.

## Next actions
1. None — task complete.
