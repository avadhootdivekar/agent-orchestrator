# TASK: T-Kd2Wp5-skill-cost-hygiene-section

## Metadata
- Task ID: `T-Kd2Wp5-skill-cost-hygiene-section`
- Epic ID: `E-Vt6Lp2-template-cost-hygiene`
- Owner: dev-epic
- Created: 2026-09-21
- Last Updated: 2026-09-21
- Status: Draft
- Estimate: < 1 day

## Requirements Mapping
- Requirement IDs: FR-D2-1

## Description
Add a new "Cost & context hygiene" section to `.claude/skills/workflow-authoring/SKILL.md`,
matching the skill's existing "Quick decision guide" checklist tone (a table row or short
sub-bullets, not a long essay), covering exactly the 4 points the epic specifies:
1. Cache hit rate is a ratio, not a cost measure — cite the real 232-turn/42.5M-cache-read-token
   example briefly (already grounded via this epic's own investigation, not re-derived).
2. Set `--autocompact` explicitly in `command_template` — point at
   `T-Hn4Rq8-agents-recommended-asset`'s `agents.recommended.json` mechanism as the concrete
   lever, with the chosen default and its justification pointer (full justification lives in the
   design doc, not duplicated here).
3. Front-load stable, static reference content (named file paths in the task instruction) over
   letting the agent discover it via exploratory tool calls — static content caches at ~0.1x,
   exploratory turns are turn-unique and never get that relief.
4. Package/context-boundary hygiene — package-scoped `CLAUDE.md` (Claude Code loads the nearest
   one up the tree) vs. a monolithic, ever-growing workspace-root one (real example: 45KB in
   production) that gets pulled into every task's context regardless of relevance. Explicitly
   note this is a target-codebase architecture decision, not something `ao` enforces.

Link to `docs-md/cost-caching-optimization-hld.md` for Epic B's full caching-audit findings rather
than duplicating them — this section is workflow-authoring guidance (the skill's charter), not a
restatement of that HLD.

## Acceptance Criteria
1. New `## Cost & context hygiene` section exists in `SKILL.md`, placed consistently with the
   skill's existing section ordering/tone (after "Isolation & parallelism" given the caching
   cross-reference already there, before "Hooks & grading" — a natural reading order; final
   placement decided during implementation, not prescribed rigidly here).
2. All 4 points present, each actionable (a concrete "do X" instruction, not just description).
3. Real numbers (232 turns, ~42.5M cache-read tokens, ~10K→~280K token growth) cited briefly,
   attributed as "a real production transcript" (matching how Epic B's own HLD cites its own
   evidence without over-specifying source identity).
4. Links to `docs-md/cost-caching-optimization-hld.md` (Epic B) and the new
   `docs-md/template-cost-hygiene-hld.md` (this epic) rather than duplicating their content.
5. No claim in this section contradicts what `T-Hn4Rq8` actually ships (roles covered, exact
   filename, exact default value) — cross-checked by hand against that task's final state.

## Risks
- None significant — this is an additive documentation section with no code/schema surface.

## Dependencies
- Reads `T-Hn4Rq8-agents-recommended-asset`'s final mechanism/filename/default value (should
  land first, or be drafted in lockstep since both are done by the same session).

## Pseudocode / Algorithm
```text
N/A — documentation task.
```

## Schemas / Interface Notes
- Interface / API: N/A.
- Spec / data schema: N/A — references `agents.recommended.json`'s shape by pointer, doesn't
  redefine it.
- Triggers / events: N/A.
- Artifacts (inputs/outputs by path): `.claude/skills/workflow-authoring/SKILL.md` (edited).

## Handoff Boundary
- Upstream: `T-Hn4Rq8-agents-recommended-asset` (mechanism this section points at).
- Downstream: none within this epic.

## Artifacts
- Docs/comments: `meta/tickets/E-Vt6Lp2-template-cost-hygiene/T-Kd2Wp5-skill-cost-hygiene-section/`
- Large outputs: none.
