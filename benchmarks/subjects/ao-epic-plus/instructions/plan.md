# ao-epic-plus subject: planner agent (plan)

This file documents the `planner` agent's role in the `ao-epic-plus` bench subject's
4-task **plan -> implement -> review -> fix** pipeline (epic `E-Bt4Xk9`, FR-8). It is
**not** wired as `workflow.json`'s `plan` task's `instruction` path: the `ao_workflow`
bench subject runs against a disposable, per-run workspace (`AO_WORKSPACE_ROOT` = the
bench task's ephemeral copy -- see `bench/subjects.py`'s `AoWorkflowSubject`), and every
workflow-relative artifact path (including `task.instruction`) resolves against *that*
workspace, not this committed `benchmarks/subjects/ao-epic-plus/` directory. So `plan`'s
actual `instruction` field is `repo/INSTRUCTION.md` -- the bench task's own instruction,
already materialized into the workspace by `bench/workspace.py`'s `materialize_workspace`
before the subject runs. The content below is mirrored into `agents.json`'s
`planner.prompt_template` (the one string the engine actually renders into the agent's
prompt) -- **`agents.json`'s `prompt_template` is authoritative; keep this file in sync
with it if either changes.**

## Role

1. Read `INSTRUCTION.md` in the repo root -- it describes the bench task's required
   change (a bugfix, a small feature, a refactor, or a test-writing task).
2. Do **not** make any code changes yet -- this is a planning-only step.
3. Produce a short, concrete implementation plan: the files you expect to touch, the
   approach, and any edge cases or risks worth calling out.
4. Write the plan to the declared output path.

This step exists so the `implement` task starts from a considered approach rather than
improvising from scratch -- the differentiator this 4-agent pipeline is meant to test
against the 2-agent `ao-epic` subject on harder, multi-file tasks.
