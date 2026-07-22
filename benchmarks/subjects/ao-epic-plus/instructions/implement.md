# ao-epic-plus subject: developer agent (implement)

This file documents the `developer` agent's role in the `ao-epic-plus` bench subject's
4-task **plan -> implement -> review -> fix** pipeline (epic `E-Bt4Xk9`, FR-8). As with
`plan.md` (see that file for the full explanation of why), it is **not** wired as
`workflow.json`'s `implement` task's `instruction` path -- that task's actual
`instruction` is `repo/INSTRUCTION.md` (the bench task's own instruction, resolved
against the per-run ephemeral workspace). The content below is mirrored into
`agents.json`'s `developer.prompt_template`; **`agents.json`'s `prompt_template` is
authoritative; keep this file in sync with it if either changes.**

## Role

1. Read `INSTRUCTION.md` in the repo root and the `plan` task's output (this task's
   declared input) -- the plan the `planner` agent produced.
2. Make the necessary code changes to satisfy the instruction, following the plan (you
   may deviate from it if it turns out to be wrong -- note why in your report if so).
3. Run the repo's own test suite (`uv run pytest -q` or `python3 -m pytest -q`,
   whichever is available) and confirm it passes before finishing.
4. Write a short report summarizing the change and the test result to the declared
   output path.

Do not modify `INSTRUCTION.md` or any grading/check script already present in the repo
(e.g. `check.py`, `check_tests.py`) -- the bench grader, not this workflow, is the
actual judge of whether the task is solved.
