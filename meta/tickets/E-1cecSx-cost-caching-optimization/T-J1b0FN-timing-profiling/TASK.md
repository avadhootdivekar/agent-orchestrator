# TASK: T-J1b0FN-timing-profiling

## Metadata
- Task ID: `T-J1b0FN-timing-profiling`
- Epic ID: `E-1cecSx-cost-caching-optimization`
- Owner: delegated `developer` agent
- Created: 2026-09-21
- Last Updated: 2026-09-21
- Status: `Done` (B2.1 Done + B2.2 Done — see STATUS.md)
- Estimate: `1-2 days`

## Requirements Mapping
- Requirement IDs: FR-B2-1, FR-B2-2

## Description
See `docs-md/cost-caching-optimization-hld.md` §2. Two deliverables:
1. Run-level top-N slowest tasks — pure function over already-persisted `RunState`
   (`TaskRunState.started_at`/`.ended_at`), same derivation `models.py::compute_run_active_seconds`
   already uses per-task, unsummed and sorted.
2. Within-task activity-type breakdown — feasibility CONFIRMED (see design doc §2.2: real
   captured transcripts have per-event ISO timestamps on `assistant`/`user` events). Parse
   `transcript.jsonl` via the EXISTING `claude_cli.py::parse_transcript_events` (do not
   re-implement JSONL parsing), bucket elapsed time between events by tool-name-derived activity
   category (table in design doc §2.2).

Both surfaced via a new `ao report timing <run_id> [--top N] [--task TASK_ID]` CLI command and
wired into the dashboard's existing expandable per-task detail view (coordinate with
`T-h1KdlK` — that task owns the actual dashboard frontend change; this task's job is the backend
pure functions + CLI command + making the data available to the dashboard backend response).

## Acceptance Criteria
1. New `src/agent_orchestrator/reporting.py`: `top_n_slowest_tasks(state: RunState, n: int) ->
   list[TaskDuration]` and `task_activity_breakdown(transcript_path: str) ->
   ActivityBreakdown` (exact type shapes at implementer's discretion, but must be plain
   dataclasses/pydantic models, documented, and covered by unit tests).
2. `cache_effectiveness` helper is OWNED by `T-h1KdlK`, not this task — do not duplicate; if
   convenient, this task may add a shared small "per-task detail" data-building helper in
   `reporting.py` that `T-h1KdlK` composes with, but must not block on that task landing first.
3. New `ao report timing <run_id> [--top N] [--task TASK_ID]` CLI subcommand in `cli.py`
   (additive; does not modify any existing command).
4. Unit tests: top-N slowest against a synthetic multi-task `RunState` (fixed timestamps,
   deterministic ordering, tie-breaking documented); activity breakdown against a small
   fixture `transcript.jsonl` (can adapt a trimmed real one from
   `playground/.tmp/bench/2026-07-22-*/`, or synthesize one — either way, cover: file-edit,
   build-or-test (Bash with a test keyword), shell-other (Bash without one), search-or-read,
   thinking-or-text-only turns).
5. `ruff`/`mypy` clean on new/touched files.
6. Do NOT touch `engine.py`, `models.py`, `isolation/*`, or `hooks.py` — this task is additive,
   read-only against `RunState`/transcript files on disk.

## Risks
- Activity-breakdown heuristic is turn-level, not per-parallel-tool-call — documented as an
  approximation in the design doc and the function's own docstring; do not oversell precision
  in the CLI output text either.

## Dependencies
- None for the backend; `T-h1KdlK` consumes this task's output for the dashboard frontend.

## Pseudocode / Algorithm
See design doc §2.1-§2.2 for the exact derivation and activity-category table.

## Schemas / Interface Notes
- Interface: two new pure functions in `reporting.py`; one new CLI subcommand.
- Artifacts (inputs): `RunState` (in-memory/loaded via `RunStateStore`), `transcript.jsonl`
  (path already recorded on `TaskRunState.output_artifact_path`).
- Artifacts (outputs): CLI stdout report; no new persisted file required for MVP.

## Handoff Boundary
- Upstream: none.
- Downstream: `T-h1KdlK` (dashboard surfacing), `T-UJElTR` (e2e demonstration).

## Artifacts
- Docs/comments: `meta/tickets/E-1cecSx-cost-caching-optimization/T-J1b0FN-timing-profiling/`
- Design doc: `docs-md/cost-caching-optimization-hld.md` §2

## Completion note (B2.2)
- `task_activity_breakdown(transcript_path) -> ActivityBreakdown` added to `reporting.py`,
  reusing `claude_cli.py::parse_transcript_events` unchanged (this task's own job is only the
  file read + the elapsed-time-to-category bucketing). Category set is a `Literal[...]`
  (`ActivityCategory`); every tool-name set/keyword list/tie-break order from design doc §2.2's
  table is a named module-level constant (`_FILE_EDIT_TOOL_NAMES`, `_SEARCH_OR_READ_TOOL_NAMES`,
  `_BASH_TOOL_NAME`, `_BUILD_OR_TEST_KEYWORDS`, `_CATEGORY_PRIORITY`) — no inline/magic literals.
  The approximation (turn-level granularity; elapsed-time-to-next-event heuristic folding model
  latency into an adjacent bucket) is documented explicitly in the function's own docstring and
  in `ao report-timing --help`'s `--task` option text, never oversold as exact profiling.
- Committed fixture: `tests/fixtures/transcript_activity_breakdown.jsonl` (hand-built, fully
  redacted/synthetic — no real repo paths or content — modeled on a real captured transcript's
  exact event shape, confirmed by inspecting one under `playground/.tmp/bench/...` before
  writing it). Covers every row of the design doc's table: a `file-edit` (`Edit`), a
  `build-or-test` `Bash` (`pytest ...`), a `shell-other` `Bash` (`ls -la ...`), a `search-or-read`
  (`Read`), and `thinking-or-text` turns (a pure-`thinking` turn, a final text-only turn, plus
  the `user`/`system`/`result` events that carry no `tool_use` block).
- `ao report-timing --task <task_id>` wired into the EXISTING `report_timing` command in
  `cli.py` (additive only — no other existing command touched). Looks up
  `TaskRunState.output_artifact_path`, joins `claude_cli.py::TRANSCRIPT_FILE`, and handles both
  graceful-missing-transcript cases without crashing: no `output_artifact_path` recorded (task
  never dispatched, e.g. `fake`-executor stub or a skipped task), and no `transcript.jsonl` file
  at that path (`OSError` caught).

## Evidence
- `pytest tests/test_reporting.py -q` → 38 passed (10 pre-existing B2.1/B4 + 28 new B2.2:
  `_categorize_tool` white-box coverage of every category incl. the build/test keyword list,
  and `task_activity_breakdown` incl. the committed fixture, empty/single-event/no-timestamp
  edge cases, and the mixed-tool-turn tie-break).
- `pytest tests/test_e2e_cli_cost_caching.py -q` → 1 passed. Extended the EXISTING e2e test
  (`test_run_then_report_timing_and_graded_outcomes`) with `ao report-timing --task` coverage
  through `typer.testing.CliRunner` (outermost boundary, per CLAUDE.md): (1) the real
  `fake`-executor task's own capture-parity `transcript.jsonl` stub (FR-4/FR-5) has no
  timestamped events, exercising the genuine zero-events graceful path against real output; (2)
  the committed fixture is then placed at that task's REAL `output_artifact_path`, proving the
  full CLI-to-`reporting.py` wiring end-to-end (not just the pure function in isolation); (3)
  the never-dispatched `skip_task` (no `output_artifact_path` at all) exercises the other
  graceful-missing-data path.
- `pytest tests/test_reporting.py tests/test_e2e_cli_cost_caching.py -q` → 39 passed.
- `pytest -q` (full suite) → 1 failed, 3968 passed, 8 skipped. The 1 failure is the KNOWN
  pre-existing, unrelated `tests/test_nfr2_regression_gate.py::TestPreEpicTestsUnedited::
  test_every_pre_epic_test_file_is_byte_identical_to_its_pre_epic_content` (pre-dates this
  epic; not touched or caused by this task). Ran the full suite twice (before and after the
  e2e-test extension above) — identical result both times, zero regressions.
- `ruff check src/agent_orchestrator/reporting.py src/agent_orchestrator/cli.py
  tests/test_reporting.py tests/test_e2e_cli_cost_caching.py` → all checks passed.
  `ruff format --check` (same files) → all formatted.
- `mypy src/agent_orchestrator/reporting.py src/agent_orchestrator/cli.py` → no issues (2 source
  files). `mypy src` (full, CI's own convention per `.github/workflows/ci.yml`) → 4 pre-existing
  errors, all in `_version.py`, unrelated to this task's files.
- Smoke test: `ao report-timing --help` run directly — shows the new `--task` option with its
  approximation caveat in the help text (see STATUS.md for the captured output).

## Files touched
- `src/agent_orchestrator/reporting.py` (B2.2 additions only — B2.1's `top_n_slowest_tasks`/
  `cache_effectiveness`/`run_cache_effectiveness` untouched)
- `src/agent_orchestrator/cli.py` (`report_timing`'s `--task` option only — no other command
  touched)
- `tests/test_reporting.py` (new tests appended to the existing file)
- `tests/test_e2e_cli_cost_caching.py` (existing e2e test extended with `--task` coverage)
- `tests/fixtures/transcript_activity_breakdown.jsonl` (new, committed fixture)
- This ticket's `TASK.md`/`STATUS.md`

By: developer agent · Role: developer · Date: 2026-09-21 · Comment: B2.2 implemented, tested,
and verified per the epic's early-gate hardening requirements (named constants, `Literal`
category type, committed redacted fixture, explicit approximation caveat). Ticket marked Done
now that both B2.1 and B2.2 are complete. Did not touch `meta/tickets/E-1cecSx-cost-caching-
optimization/EPIC.md`/`STATUS.md` (epic-level rollup) or any sibling task folder, per this
task's explicit scope boundary — flagging for the coordinating `dev-epic`/`manager` agent to
sync the epic rollup once it has this task's final status.
