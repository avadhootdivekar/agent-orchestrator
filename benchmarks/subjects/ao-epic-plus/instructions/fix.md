# ao-epic-plus subject: fixer agent (fix)

This file documents the `fixer` agent's role in the `ao-epic-plus` bench subject's
4-task **plan -> implement -> review -> fix** pipeline (epic `E-Bt4Xk9`, FR-8). As with
`plan.md` (see that file for the full explanation of why), it is **not** wired as
`workflow.json`'s `fix` task's `instruction` path -- that task's actual `instruction` is
`repo/INSTRUCTION.md` (the bench task's own instruction, resolved against the per-run
ephemeral workspace). The content below is mirrored into `agents.json`'s
`fixer.prompt_template`; **`agents.json`'s `prompt_template` is authoritative; keep this
file in sync with it if either changes.**

## Role

1. Read the `review` task's output (this task's declared input) -- the reviewer
   agent's findings -- and `INSTRUCTION.md` in the repo root.
2. If the findings report any issues, make the minimal code changes needed to resolve
   them.
3. Run the repo's own visible test suite (`uv run pytest -q` or `python3 -m pytest -q`)
   and confirm it passes -- whether or not you made changes.
4. Write a short report to the declared output path describing what (if anything) you
   fixed and the final test result.

Do not modify `INSTRUCTION.md` or any grading/check script already present in the repo
(e.g. `check.py`, `check_tests.py`) -- the bench grader, not this workflow, is the
actual judge of whether the task is solved. This is the pipeline's verify/repair loop:
`review` catches what `implement` missed, and `fix` closes the loop.
