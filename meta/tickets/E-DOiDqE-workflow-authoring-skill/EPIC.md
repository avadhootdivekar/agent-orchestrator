# EPIC: E-DOiDqE-workflow-authoring-skill

## Metadata
- Epic ID: `E-DOiDqE-workflow-authoring-skill`
- Title: AO workflow-authoring skill (Epic C of the cost/perf/hooks/skills thread)
- Owner: dev-epic
- Created: 2026-09-21
- Last Updated: 2026-09-21
- Status: In Progress

## Summary
- Goal: Author a Claude Code skill under `.claude/skills/` that teaches an agent how to decompose
  a complex task into a well-formed `ao` DAG / workflow spec — task sizing, routing vs. static
  task lists, isolation/`max_parallel` trade-offs (including Epic B's caching/parallelism
  interaction), where `pre_hook`/`post_hook` (Epic A) and `ao report-outcomes --grade` (Epic B)
  fit, and common failure modes to avoid. Grounded in the actual current spec schema
  (`specs/workflow.schema.json`) and CLI (`src/agent_orchestrator/`), not stale HLD claims, and
  in real usage evidence from the sibling `ao-runner-*` consumer repos (read-only survey).
- Scope In: one new skill (`.claude/skills/workflow-authoring/SKILL.md`), `.claude/skills/`
  restructuring/index as proportionate, a worked-example workflow spec validated against the
  schema, a C0 reliability/failure-mode audit (time-boxed scoping input) with any real
  unrelated bug/gap spun into its own ticket.
- Scope Out: importing generic personal-productivity Claude skills from sibling repos (explicitly
  locked out during scoping); any engine/core code changes (this epic is docs/skill/example-spec
  only, unless C0 finds something requiring a narrowly-scoped fix — tracked separately, not
  folded in here); cross-repo sync of this skill into `ao-runner-*` repos.

## Requirements

### MVP (must-have — end-to-end functional, each with a verification method)
- FR-C0-1 (Functional): Time-boxed audit of `meta/learnings.md`, `meta/learning-compact.md`,
  past ticket `REVIEW*.md`/`STATUS.md`, and `meta/ROADMAP.md` §4 for known unrecoverable-failure
  patterns in workflow-spec authoring/decomposition. Verification: findings enumerated in this
  epic's context doc with citations, separated into skill-guidance items vs. real
  bugs/gaps-needing-tickets.
- FR-C1-1 (Functional): `.claude/skills/workflow-authoring/SKILL.md` covers task sizing, routing/
  `emit_tasks` vs. static task lists, isolation/`max_parallel` (incl. cache-cost trade-off),
  hooks/grading placement, and common failure modes — every schema/CLI claim verified against
  current code. Verification: manual cross-check pass listing each claim's grounding file:line;
  peer review via `reviewer` agent.
- FR-C2-1 (Functional): `.claude/skills/` restructured only as proportionate (subdirectory for
  the new skill; CLAUDE.md's skills table updated). Verification: `ls .claude/skills/` +
  CLAUDE.md diff reviewed.
- FR-C3-1 (Functional): One worked-example workflow spec under `specs/examples/` demonstrating
  the skill's guidance, schema-valid. Verification: `ao validate --workflow <path> ...` run for
  real, exit 0, output captured.
- NFR-C1-1 (Non-functional, spec ergonomics/accuracy): No skill claim may be copied from an HLD
  without checking current code — HLDs can drift during implementation (explicit epic
  instruction). Verification: each interface claim in the skill cites the current source file it
  was checked against.
- NFR-C0-1 (Non-functional, scope discipline): Any real bug/gap C0 finds that is unrelated to
  skill-authoring is ticketed separately, never fixed inline in this epic. Verification: spin-off
  ticket ID(s) recorded in this doc's Evidence section, or explicit "none found" statement.

### Non-MVP (deferred)
- Interactive skill tooling / CLI integration (e.g., an `ao new --from-skill` scaffolding path).
  Later validation: a follow-up epic exercising it against a real `ao new` invocation.
- Syncing/publishing this skill into the sibling `ao-runner-*` repos. Later validation: explicit
  user request + a separate cross-repo change (this epic's sibling-repo access is read-only by
  design).

### Stretch (not required to ship)
- A self-improvement note on the skill (mirroring `repo-intel/SKILL.md`'s "if you find this doc
  inaccurate, update it") — nice-to-have discoverability, not required for the skill to function.

## Task List
- [x] `T-trl41B-reliability-audit` — C0 audit (learnings/tickets/roadmap survey; spin-off ticket
  if warranted). **Done** — 3 spin-offs filed: `E-Grpp0X-injected-task-dag-validation-gap`
  (new gap, independently re-verified), `E-hbQnU2-isolation-housekeeping-followups`
  (found independently by a **concurrently-running second `dev-epic` session** working this
  same epic in this same working directory — see Risks/Dependencies note below — content
  verified accurate and adopted, not a "prior interrupted attempt" as first assumed), and
  `E-5I8azA-nfr2-gate-stale-exception` (pre-existing red baseline test, unrelated to Epic C).
- [x] `T-buzXEz-author-skill` — C1 author the workflow-authoring skill. **Done** —
  `.claude/skills/workflow-authoring/SKILL.md`.
- [x] `T-0qMDfU-skills-restructure` — C2 `.claude/skills/` layout + CLAUDE.md table update.
  **Done.**
- [ ] `T-PLsJdO-worked-example` — C3 worked example spec + real schema validation

## Risks and Dependencies
- Sibling repos (`ao-runner-finplan` etc.) are live, concurrently-modified by other sessions —
  survey must be read-only, single-pass, no polling/diffing across time. Mitigated by delegating
  to a research-only fork with explicit read-only instructions.
- HLD drift risk (Epics A/B docs may not exactly match landed code) — mitigated by NFR-C1-1's
  explicit re-verification requirement.
- No CI job runs `ao validate` over every file under `specs/examples/` automatically (confirmed —
  no such glob-based test found); the new example's schema-validity is verified once, by hand, in
  this epic and recorded as evidence, not guaranteed by a regression gate. Recorded as an accepted
  gap, not silently assumed away.
- **Confirmed operational anomaly, not just a theoretical risk: a second, independent `dev-epic`
  session ran this exact epic concurrently, sharing this same repo checkout/branch/working
  directory** (evidenced by: two separately-scaffolded epic trees found on disk at nearly
  identical stages, `ps aux` showing multiple long-running top-level `claude` processes under
  different `--remote-control` session names, and this session's own `E-hbQnU2` spin-off ticket
  being found and edited by the other session mid-run). Reconciled by consolidating onto this
  epic (`E-DOiDqE-*`, adopted as canonical since it was found further along at the point of
  collision) and discarding the other session's duplicate epic scaffold
  (`E-lBessP-workflow-authoring-skill`, deleted before it was ever committed — no data lost).
  **This should be flagged to the user/orchestrating system as a likely accidental duplicate
  agent dispatch** — not an Epic C defect, but worth checking upstream (did two orchestrating
  sessions both kick off Epic C?). No corruption occurred because the collision was caught before
  either session wrote to the same deliverable *file* (only ticket-tree docs collided; each
  session's file-level writes, e.g. `SKILL.md`, `CLAUDE.md`, happened after reconciliation).

## Links
- Design doc: `docs-md/ai-epics/E-DOiDqE-workflow-authoring-skill.md`
- Related HLDs: `docs-md/task-lifecycle-hooks-hld.md` (Epic A), `docs-md/cost-caching-optimization-hld.md`
  (Epic B), `docs-md/granular-task-decomposition-hld.md` (task-sizing precedent),
  `docs-md/parallel-execution-hld.md`, `docs-md/task-isolation-hld.md`
- Output artifacts: `specs/examples/` (worked example), `.claude/skills/workflow-authoring/`
