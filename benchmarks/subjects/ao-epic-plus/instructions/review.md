# ao-epic-plus subject: reviewer agent (review)

This file documents the `reviewer` agent's role in the `ao-epic-plus` bench subject's
4-task **plan -> implement -> review -> fix** pipeline (epic `E-Bt4Xk9`, FR-8). As with
`plan.md` (see that file for the full explanation of why), it is **not** wired as
`workflow.json`'s `review` task's `instruction` path -- that task's actual `instruction`
is `repo/INSTRUCTION.md` (the bench task's own instruction, resolved against the per-run
ephemeral workspace). The content below is mirrored into `agents.json`'s
`reviewer.prompt_template`; **`agents.json`'s `prompt_template` is authoritative; keep
this file in sync with it if either changes.**

## Role

1. Read `INSTRUCTION.md` in the repo root and the `implement` task's output (this task's
   declared input) -- the developer agent's report.
2. Independently re-run the repo's own **visible** test suite yourself (`uv run pytest
   -q` or `python3 -m pytest -q`) to confirm the change actually satisfies
   `INSTRUCTION.md`, and inspect the change for correctness, missed edge cases, and
   style issues.
3. Write your findings to the declared output path: state PASS or FAIL, and list any
   specific issues found (or write "no issues" if none) -- this is the `fix` task's
   input.

Do not modify `INSTRUCTION.md` or any grading/check script already present in the repo
(e.g. `check.py`, `check_tests.py`) -- your job is to judge the change, not to alter
what judges it. The bench grader (not this workflow) is the actual, final judge of
whether the task is solved.
