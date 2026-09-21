# TASK: T-0qMDfU-skills-restructure

## Metadata
- Task ID: `T-0qMDfU-skills-restructure`
- Epic ID: `E-DOiDqE-workflow-authoring-skill`
- Owner: dev-epic
- Created: 2026-09-21
- Last Updated: 2026-09-21
- Status: Done
- Estimate: < 1 day

## Requirements Mapping
- Requirement IDs: FR-C2-1

## Description
`.claude/skills/` currently holds only `repo-intel`. Decide, proportionately, whether the new
`workflow-authoring` skill needs its own subdirectory (yes, matching `repo-intel`'s own
`<skill>/SKILL.md` layout), whether a top-level skills README/index is warranted for two skills
(likely not — keep proportionate, don't over-engineer a registry), and update CLAUDE.md's
"Skills reference" table to list the new skill.

## Acceptance Criteria
1. `.claude/skills/workflow-authoring/SKILL.md` lives in its own subdirectory, matching the
   `repo-intel` convention.
2. `CLAUDE.md`'s "Skills reference" table gains a row for the new skill (or a documented reason
   it's intentionally omitted).
3. No unwarranted registry/index scaffolding added for two skills (explicit "not needed"
   decision recorded if a README is skipped).

## Risks
- None significant — small, additive doc change.

## Dependencies
- `T-buzXEz-author-skill` (the skill must exist before CLAUDE.md references it).

## Pseudocode / Algorithm
```text
N/A — documentation/structure task.
```

## Schemas / Interface Notes
- Interface / API: N/A
- Spec / data schema: N/A
- Triggers / events: N/A
- Artifacts (inputs/outputs by path): `.claude/skills/workflow-authoring/`, `CLAUDE.md`.

## Handoff Boundary
- Upstream: `T-buzXEz-author-skill`.
- Downstream: none.

## Artifacts
- Docs/comments: `meta/tickets/E-DOiDqE-workflow-authoring-skill/T-0qMDfU-skills-restructure/`
- Large outputs: none.
