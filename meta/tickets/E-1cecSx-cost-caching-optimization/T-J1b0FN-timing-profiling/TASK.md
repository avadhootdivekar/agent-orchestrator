# TASK: T-J1b0FN-timing-profiling

## Metadata
- Task ID: `T-J1b0FN-timing-profiling`
- Epic ID: `E-1cecSx-cost-caching-optimization`
- Owner: delegated `developer` agent
- Created: 2026-09-21
- Last Updated: 2026-09-21
- Status: `In Progress` (B2.1 Done — see STATUS.md; B2.2 remaining, delegated)
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
