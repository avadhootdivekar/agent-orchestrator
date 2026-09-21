# STATUS

- ID: `T-buzXEz-author-skill`
- Updated At: `2026-09-21`
- State: In Progress (content drafted; early-gate review pending)
- Owner: dev-epic

## This update
**Concurrency note**: this exact epic/task was independently run by two concurrent `dev-epic`
sessions sharing this working directory (same repo checkout, same branch, unrelated to any
sibling-repo consideration — evidently two live top-level Claude Code sessions both dispatched
against the same Epic C prompt). Discovered when this session's own spin-off ticket
(`E-hbQnU2-isolation-housekeeping-followups`) was found reconciled/rewritten on disk mid-run by
the other session. Resolved by consolidating onto this epic/task tree (`E-DOiDqE-*`, found more
complete at the point of collision: 4 tasks already scaffolded, C0 done with 3 spin-off tickets,
a baseline pytest already captured) and discarding this session's own duplicate epic scaffold
(`E-lBessP-workflow-authoring-skill`, deleted, never committed). Flagged to the user in the
epic's final handoff as an operational anomaly worth checking (possible accidental duplicate
agent dispatch), not silently absorbed.

`.claude/skills/workflow-authoring/SKILL.md` written by this session, grounded directly in
`specs/workflow.schema.json`/`specs/agents.schema.json`/`src/agent_orchestrator/cli.py` (field
names and CLI surface re-verified at authoring time — see Evidence) and in
`T-trl41B-reliability-audit`'s completed findings (task sizing, `emit_tasks` gotchas, routing
sink requirement, parallel-write-conflict warning, caching/parallelism trade-off). Covers:
task sizing, static-vs-`emit_tasks`/routing, isolation + `max_parallel` (incl. the
`exclude_dynamic_system_prompt_sections` tie-in for isolated workflows), `pre_hook`/`post_hook`
+ `ao report-outcomes --grade` placement, and a "common failure modes" checklist. Does not
import or restate any sibling-repo skill content (none of the surveyed sibling repos were found
to have an orchestrator-authoring skill of their own).

## Evidence
- `.claude/skills/workflow-authoring/SKILL.md` exists, frontmatter matches `repo-intel/SKILL.md`'s
  `name`/`description` convention.
- Field/CLI names re-verified against current source at authoring time: `TaskSpec.emit_tasks`/
  `task_manifest_path`/`isolation`/`touches`/`pre_hook`/`post_hook`/`join`/`depends_on`,
  `hook`/`hookRef`/`router`/`route`/`scheduling` `$defs` (all read directly from
  `specs/workflow.schema.json`); `AgentSpec.exclude_dynamic_system_prompt_sections`
  (`specs/agents.schema.json`); `ao validate`/`ao report-outcomes --grade` CLI surface
  (`src/agent_orchestrator/cli.py` lines ~875, ~1675); confirmed `max_parallel` is a
  CLI/env/config setting, NOT a workflow-spec field (`cli.py`, `project_config.py` — grepped,
  no `max_parallel` in `specs/workflow.schema.json`).
- Caching/parallelism trade-off text grounded in `docs-md/cost-caching-optimization-hld.md` §1.5
  (ADR-0015) and its §1.4 `exclude_dynamic_system_prompt_sections` discussion, read in full.

## Early gate complete (2026-09-21)
Both `reviewer` and `architect` passes run against the finished skill (`approve with changes`,
no rework needed). Actionable findings incorporated: fixed the cache-scope overclaim on
`exclude_dynamic_system_prompt_sections` (now states it covers one of two documented
determinants, not both — `cost-caching-optimization-hld.md` §1.4), added the structural-task
`isolation: worktree` no-op warning, fixed a dangling doc citation, added the `--grade`
full-spec-required note, and disclosed the worked example's static-only scope explicitly rather
than silently. Full findings + disposition recorded in `EPIC.md`'s "Early gate" section (not
duplicated here).

## Risks / Blockers
- None. Task complete, reviewed, findings incorporated.

## Next actions
1. None — task complete.
