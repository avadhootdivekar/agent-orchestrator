# ao-epic subject: tester agent (verify)

This file documents the `tester` agent's role in the `ao-epic` bench subject's minimal
2-task pipeline (Q1 resolution, `docs-md/benchmarking-framework-hld.md` §18). As with
`implement.md` (see that file for the full explanation), it is **not** wired as
`workflow.json`'s `verify` task's `instruction` path — that task's actual `instruction`
is `repo/INSTRUCTION.md` (the bench task's own instruction, resolved against the
per-run ephemeral workspace). The content below is mirrored into `agents.json`'s
`tester.prompt_template`; keep them in sync if either changes.

## Role

1. Re-run the repo's own test suite (`uv run pytest -q` or `python3 -m pytest -q`) to
   confirm the change the `developer` agent made (see its report, the `verify` task's
   input) actually satisfies `INSTRUCTION.md` in the repo root.
2. If the tests pass, write a short PASS report to the declared output path.
3. If the tests fail, make the minimal fix needed to get them passing, re-run the
   tests, and report what was fixed.

Do not modify `INSTRUCTION.md` or any grading/check script already present in the repo
(e.g. `check.py`, `check_tests.py`) — the bench grader, not this workflow, is the
actual judge of whether the task is solved.
