# TASK: T-Ep8Lq6-ao-epic-plus-subject

## Metadata
- Task ID: `T-Ep8Lq6-ao-epic-plus-subject`
- Epic ID: `E-Bt4Xk9-complex-benchmark-tiers`
- Owner: developer agent
- Created: 2026-07-22
- Last Updated: 2026-07-22
- Status: Draft
- Estimate: 1.5 days

## Requirements Mapping
- FR-8 (harder 4-agent `ao_workflow` subject variant for medium/large tiers)

## Description
Author a harder `ao_workflow` subject, `ao-epic-plus`: a 4-agent **plan → implement → review → fix** DAG (vs the shipped `ao-epic` 2-agent implement→verify). This is the subject expected to *earn back* its overhead on the harder medium/large tiers via a higher solve rate — the differentiator claim `dev-core` couldn't test. No framework code change: the `ao_workflow` subject is already generic; this is purely new committed workflow/agents/reposet templates + a subject config. Must stay **uniform-model** (avoid the known model-clobber defect): no agent sets its own `model`; the subject's `model` flows via `AO_MODEL`.

## File ownership (exclusive — all NEW)
- `benchmarks/subjects/ao-epic-plus/workflow.json` — NEW (4-task DAG).
- `benchmarks/subjects/ao-epic-plus/agents.json` — NEW (planner/developer/reviewer/fixer; no per-agent `model`).
- `benchmarks/subjects/ao-epic-plus/reposet.json` — NEW (single primary repo; placeholders rewritten per-run, like `ao-epic`).
- `benchmarks/subjects/ao-epic-plus/instructions/{plan,implement,review,fix}.md` — NEW (human-readable role docs, mirrored into `agents.json` prompt_templates).
- `benchmarks/subjects/ao-epic-plus-sonnet.json` — NEW subject config (model `claude-sonnet-5`).
- (optional) `benchmarks/subjects/ao-epic-plus-haiku.json` — NEW (cheap smoke).
- (read-only) `benchmarks/subjects/ao-epic/*` as the reference shape.

## Inputs / Outputs
- Inputs: a bench task's `repo/INSTRUCTION.md` (materialized by workspace.py).
- Outputs: a validated `ao_workflow` subject that runs a 4-agent DAG; cost/tokens via the reused core plumbing (already handled by `AoWorkflowSubject`).

## Acceptance Criteria
1. `ao-bench validate --subject benchmarks/subjects/ao-epic-plus-sonnet.json` → OK (type `ao_workflow`).
2. The workflow is a 4-task DAG: `plan` (no deps) → `implement` (deps plan) → `review` (deps implement) → `fix` (deps review), each `instruction: "repo/INSTRUCTION.md"` (the as-built `ao_workflow` constraint), inputs/outputs wired as artifacts (`output/*.md`), all agents `working_dir: "repo"`, `permission_mode: bypassPermissions`.
3. **Uniform-model:** no agent in `agents.json` sets `model`; the subject's `model` flows via `AO_MODEL` (grep-asserted) — sidesteps the model-override-clobber defect (learnings).
4. `agents.json` prompt_templates and `instructions/*.md` are kept in sync (the engine renders the prompt_template strings; the .md files are the human-readable mirror) — documented in the folder.
5. **Opt-in real run (gated):** one `dev-medium` task × `ao-epic-plus-sonnet` completes end-to-end, produces exactly one run_id, and `run.json` records real cost/tokens (reuse-of-core-plumbing proof) — asserted opt-in, not in CI.
6. Reviewer/fixer roles are instructed to run the repo's own visible tests and to NOT modify grading/check scripts (same guardrail as `ao-epic`).

## Risks
- More agents = more cost + wall-clock; this is the point (it must earn it via solve rate on hard tasks), but keep prompts tight so a *trivial* task doesn't 4× the bill for no gain — the medium/large tiers are where it's meant to run, not `dev-core`.
- Artifact wiring: `review` must consume `implement`'s output and `fix` must consume `review`'s findings, so the DAG actually adds a verify/repair loop rather than four independent turns. Wire `inputs`/`outputs` explicitly.

## Pseudocode / Algorithm
```text
# workflow.json
tasks:
  - {id: plan,      agent: planner,   instruction: repo/INSTRUCTION.md, outputs: [output/plan.md],      depends_on: []}
  - {id: implement, agent: developer, instruction: repo/INSTRUCTION.md, inputs: [output/plan.md],
                                                                         outputs: [output/impl.md],      depends_on: [plan]}
  - {id: review,    agent: reviewer,  instruction: repo/INSTRUCTION.md, inputs: [output/impl.md],
                                                                         outputs: [output/review.md],    depends_on: [implement]}
  - {id: fix,       agent: fixer,     instruction: repo/INSTRUCTION.md, inputs: [output/review.md],
                                                                         outputs: [output/fix.md],       depends_on: [review]}
# agents.json: planner/developer/reviewer/fixer, executor claude_cli, NO model field, working_dir repo,
#              bypassPermissions, prompts mirror instructions/*.md.
# ao-epic-plus-sonnet.json: {version, id: ao-epic-plus-sonnet, type: ao_workflow,
#   workflow: ao-epic-plus/workflow.json, reposets: ao-epic-plus/reposet.json, agents: ao-epic-plus/agents.json,
#   model: claude-sonnet-5, max_turns: 30}
```

## Schemas / Interface Notes
- Uses the existing `ao_workflow` subject + core workflow/agents/reposet schemas unchanged (no framework edit).
- Triggers/events: workflow `triggers: [{type: manual}]`. Artifacts: `output/*.md` inside the per-run workspace.

## Handoff Boundary
- Upstream: none (fully independent — Wave A parallel-safe).
- Downstream: PLAN medium/large runs; T-Cm9Tb4 recipes may reference it. No dependency on any code task.

## Artifacts
- Docs/comments: this folder. Large outputs: none (templates are small committed JSON/MD).
