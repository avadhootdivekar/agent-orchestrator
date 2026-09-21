# TASK: T-buzXEz-author-skill

## Metadata
- Task ID: `T-buzXEz-author-skill`
- Epic ID: `E-DOiDqE-workflow-authoring-skill`
- Owner: dev-epic
- Created: 2026-09-21
- Last Updated: 2026-09-21
- Status: Done
- Estimate: < 2 days

## Requirements Mapping
- Requirement IDs: FR-C1-1, NFR-C1-1

## Description
Author `.claude/skills/workflow-authoring/SKILL.md`, a Claude Code skill teaching an agent how to
decompose a complex task into a well-formed `ao` DAG / workflow spec. Follow the frontmatter/
structure convention established by `.claude/skills/repo-intel/SKILL.md` (read first — don't
invent a new shape). Content must cover, each grounded in current `specs/workflow.schema.json`
and `src/agent_orchestrator/` code (verified, not assumed from HLDs, which can drift during
implementation):
- Task sizing — grounded in artifact-boundary clarity and retry/resume granularity, referencing
  this project's own `docs-md/granular-task-decomposition-hld.md` context-bounded-session
  precedent and CLAUDE.md's "tasks ≤3 days" architect-scale reference, not an arbitrary number.
- When to use routing/`emit_tasks` (dynamic expansion) vs. a static task list.
- When to use task isolation (`isolation: worktree`) vs. not, and `max_parallel` — including
  Epic B's documented caching/parallelism trade-off (parallel tasks sharing a prompt prefix all
  pay full cache-miss cost simultaneously).
- Where `pre_hook`/`post_hook` (Epic A) and `ao report-outcomes --grade` (Epic B) fit — when to
  reach for them vs. not.
- Common failure modes to avoid, informed by `T-trl41B-reliability-audit`'s findings.
Ground the guidance in real usage evidence from the sibling `ao-runner-*` repos (read-only
survey, delegated to a research fork) — real task granularity, real routing/isolation/
`max_parallel` configuration in practice — not guesswork.

## Acceptance Criteria
1. `.claude/skills/workflow-authoring/SKILL.md` exists with valid frontmatter matching the
   repo-intel convention (`name`, `description`).
2. Every schema/CLI/field claim in the skill is checked against current
   `specs/workflow.schema.json` / `src/agent_orchestrator/` source, not copied verbatim from an
   HLD.
3. Content sections present: task sizing, routing/`emit_tasks` vs. static, isolation/
   `max_parallel` trade-offs (incl. caching interaction), hooks/grading placement, common failure
   modes.
4. `reviewer` (and `architect` for the end-to-end flow sanity check) pass requested per the
   dev-epic early-gate requirement; outcome recorded.

## Risks
- HLD drift: Epic A/B HLDs could describe a design that diverged during implementation —
  mitigated by re-checking every claim against current `models.py`/`spec.py`/`cli.py`/
  `specs/workflow.schema.json`.

## Dependencies
- `T-trl41B-reliability-audit` (failure-mode input) and the sibling-repo survey fork (usage
  evidence) — both should complete before this task's content is finalized, though drafting can
  start in parallel.

## Pseudocode / Algorithm
```text
N/A — documentation task.
```

## Schemas / Interface Notes
- Interface / API: N/A (skill is instructional markdown, not code).
- Spec / data schema (JSON/YAML): references `specs/workflow.schema.json` fields verbatim/by
  citation, does not redefine them.
- Triggers / events: N/A
- Artifacts (inputs/outputs by path): writes `.claude/skills/workflow-authoring/SKILL.md`.

## Handoff Boundary
- Upstream: `T-trl41B-reliability-audit` (failure-mode findings), sibling-repo survey fork
  (usage-pattern evidence).
- Downstream: `T-PLsJdO-worked-example` (the worked example must be internally consistent with
  what this skill teaches); `T-0qMDfU-skills-restructure` (CLAUDE.md table entry points at this
  skill).

## Artifacts
- Docs/comments: `meta/tickets/E-DOiDqE-workflow-authoring-skill/T-buzXEz-author-skill/`
- Large outputs: `.claude/skills/workflow-authoring/SKILL.md` (the skill itself, not a "large
  output" in the `output/` sense, but the task's primary artifact).
