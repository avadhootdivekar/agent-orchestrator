# ao-epic subject: developer agent (implement)

This file documents the `developer` agent's role in the `ao-epic` bench subject's
minimal 2-task pipeline (Q1 resolution, `docs-md/benchmarking-framework-hld.md` §18).
It is **not** wired as `workflow.json`'s `implement` task's `instruction` path: the
`ao_workflow` bench subject runs against a disposable, per-run workspace
(`AO_WORKSPACE_ROOT` = the bench task's ephemeral copy — see `bench/subjects.py`'s
`AoWorkflowSubject`), and every workflow-relative artifact path (including
`task.instruction`) resolves against *that* workspace, not this committed
`benchmarks/subjects/ao-epic/` directory. So `implement`'s actual `instruction` field
is `repo/INSTRUCTION.md` — the bench task's own instruction, already materialized into
the workspace by `bench/workspace.py`'s `materialize_workspace` before the subject
runs. The content below is mirrored into `agents.json`'s `developer.prompt_template`
(the one string the engine actually renders into the agent's prompt) — keep them in
sync if either changes.

## Role

1. Read `INSTRUCTION.md` in the repo root — it describes the bench task's required
   change (a bugfix, a small feature, a refactor, or a test-writing task).
2. Make the necessary code changes in the repo to satisfy it.
3. Run the repo's own test suite (`uv run pytest -q` or `python3 -m pytest -q`,
   whichever is available) and confirm it passes before finishing.
4. Write a short report summarizing the change and the test result to the declared
   output path.

Do not modify `INSTRUCTION.md` or any grading/check script already present in the repo
(e.g. `check.py`, `check_tests.py`) — the bench grader, not this workflow, is the
actual judge of whether the task is solved.
