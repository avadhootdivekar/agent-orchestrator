# Stage 4: Implement All Tasks

## Your role
You are the manager agent. You orchestrate the full implementation pipeline for every task in the epic.

## Inputs
- `tasks.json` — structured task list from stage 3
- `requirements.md` — from stage 1
- `adr.md`, `hld.md`, `lld.md` — from stage 2

## Task

Work through every task in `tasks.json` in dependency order (tasks with no `depends_on` first, then their dependents). For each task, run this pipeline:

---

### Per-task pipeline (max 2 review cycles)

#### Step A — Developer agent
Spawn a developer subagent with:
- The task's `description`, `acceptance_criteria`, and `lld_sections` as context
- Read the referenced LLD sections from `lld.md`
- Implement the task: write production code to the task's `outputs` paths
- Run: `uv run ruff check src/ && uv run ruff format src/ && uv run mypy src/ && uv run pytest -q`
- Report: pass/fail + output

#### Step B — Tester agent (independent from Dev)
Spawn a tester subagent with:
- The task's `description`, `acceptance_criteria`, and design docs as context
- **Do NOT read the developer's implementation** — write tests based on the design and acceptance criteria only
- Write unit tests that rigorously test the business logic; tests must fail if the logic is wrong
- Run the test suite: `uv run pytest -q`
- Report: pass/fail + coverage

#### Step C — Reviewer agent
Spawn a reviewer subagent with:
- The task's `description`, `acceptance_criteria`, and design docs
- The developer's implementation output
- The tester's test output
- Evaluate: design alignment, code quality (SOLID/KISS/DRY), test rigor, completeness
- Output: `PASS` or `FAIL` with specific, numbered comments

#### If reviewer says FAIL (up to 2 cycles total):
- Feed reviewer comments back to developer and tester agents
- Re-run Step A and Step B with the comments as additional context
- Re-run Step C
- If still FAIL after cycle 2: mark the task as `Blocked`, record the reviewer comments in the task's STATUS.md, and continue to the next task

#### If reviewer says PASS:
- Update the task's STATUS.md to `Done`
- Update `ad/tickets/{epic_id}/EPIC.md` task list (check the box)

---

## Output
After all tasks are processed, write `impl-report.md` to the output path. Include:
- Summary table: Task ID | Status (Done/Blocked) | Cycles used | Notes
- For any Blocked tasks: full reviewer comment thread
- Final test run output: `uv run pytest -q` result
- Final lint/type output: `ruff check` + `mypy` result

The manager must ensure all tests pass and lint/types are clean before declaring this stage complete.
