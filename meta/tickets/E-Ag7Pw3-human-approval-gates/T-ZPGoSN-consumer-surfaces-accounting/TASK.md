# TASK: T-ZPGoSN-consumer-surfaces-accounting

## Metadata
- Task ID: `T-ZPGoSN-consumer-surfaces-accounting`
- Epic ID: `E-Ag7Pw3-human-approval-gates`
- Owner: developer
- Created: 2026-10-04
- Last Updated: 2026-10-05
- Status: Draft
- Estimate: 1.5 days (rev 1: 2 d; the pure accounting module moved to `T-drPIif` at Gate 1 so that
  `approvals/views.py` can use it)

## Requirements Mapping
- Requirement IDs: FR-12, NFR-1, NFR-9
- Design: HLD §9.2.4–§9.2.6, §26 rows 18, 20, 21, 27, 31, 32; tension T-15; Gate 1 R-07, suggestion S-08

## Description
Make every consumer of task status consistent with `awaiting_approval` and report approval wait time,
using `approvals/accounting.py` (delivered by `T-drPIif`).

1. `runstate.write_status`: when `state.approvals` is non-empty only, add `pending_approvals` and
   `counts.setdefault("awaiting_approval", 0)`; keep the existing `current_task` rule (first `running` or
   `pending` task) and, only when it finds none, fall back to the first `awaiting_approval` task (T-15 —
   gate-free output unchanged).
2. `cli._print_state` / `_print_status_snapshot`: "Pending approvals" trailer (task, request id, expiry,
   flags, the `ao approvals show` hint) only when there are pending approvals; widen the status column only
   when some status is longer than 15 characters (gate-free output byte-identical).
3. `cli report_timing`: one `Approval wait: <duration> (<n> requests)` line when the run has requests,
   from `compute_run_approval_wait_seconds`.
4. `outcomes.grade_run`: skip tasks whose spec has `approval`.
5. `ui/runs.py`: `RunSummary.approval_wait_seconds = compute_run_approval_wait_seconds(state)` (same window
   and end marker as `wall_seconds`, no clock read — Gate 1 R-07).
6. `ui/activity.py`: `"awaiting_approval"` in `_NO_CAPTURE_STATUSES`.

Files — new: `tests/approvals/test_surfaces.py`. Shared: `runstate.py`, `cli.py`, `outcomes.py`,
`ui/runs.py`, `ui/activity.py` (§26 rows 18, 20, 21, 27, 31, 32). **Exclusive files during stage E** (HLD
§22.3): these; start on `cli.py` only after `T-nmL0HP`'s registration commit has merged, and keep the
printer edits at the end of each function.

## Acceptance Criteria
1. A gated run's `status.json` contains `pending_approvals` rows with exactly the §9.2.4 keys and
   `counts.awaiting_approval`; `test_gate_free_status_json_key_set_unchanged` asserts a gate-free run's
   `status.json` has neither key; the pre-existing exact-key tests (`tests/test_isolation_models.py`,
   `tests/test_spawn_provenance.py`) pass unedited; `test_current_task_falls_back_to_awaiting_gate` shows
   `current_task` naming the awaiting gate only when no task is running or pending, and a running/pending
   task otherwise.
2. `ao status` (CliRunner) prints the trailer for a gated run; for a gate-free run the output is
   byte-identical to a golden recorded before the change.
3. `RunSummary.approval_wait_seconds` equals `compute_run_approval_wait_seconds(state)` and never exceeds
   `wall_seconds` for the fixtures (pending in a running run, applied, halted, superseded, two parallel
   gates); `tests/ui/test_run_graph_endpoint.py` (exact new-key sets of `RunDetail`/`TaskStat`) passes
   unedited.
4. `ao report-timing` prints the approval line for a gated run only.
5. `grade_run` never runs the hook for a gate; `aggregate_usage` produces no `(unknown, unknown, none)` group
   for a gated run; `read_run_activity` returns no row for an awaiting gate.
6. Targeted tests green; ruff/mypy clean (full suite at the stage-E checkpoint); `reviewer`-agent review
   recorded.

## Risks
- `cli.py` printers are shared with the cache epic (medium merge risk; keep edits at the end of each
  function).

## Dependencies
- `T-vwIpSw` (gates exist end to end); `T-drPIif` (`approvals/accounting.py`); `T-nmL0HP`'s `cli.py`
  registration commit.

## Pseudocode / Algorithm
```text
HLD §9.2.4 write_status guard + current_task fallback; §9.2.5 compute_run_approval_wait_seconds (union
inside [started_at, updated_at]).
```

## Schemas / Interface Notes
- `status.json` `pending_approvals[]` row: HLD §9.2.4.

## Verification
```
WT=/usr/avadhoot/mounted/agent-orchestrator/.claude/worktrees/agent-a513b4d78ac3b20bb
PY=/usr/avadhoot/mounted/agent-orchestrator/.venv/bin/python
cd $WT && $PY -m pytest -o pythonpath=src -p no:cacheprovider -q tests/approvals/test_surfaces.py tests/approvals/test_wait_accounting.py tests/test_status_artifact.py tests/test_isolation_models.py tests/test_spawn_provenance.py tests/ui
cd $WT && $PY -m ruff check src/agent_orchestrator tests/approvals && $PY -m mypy src/agent_orchestrator/approvals src/agent_orchestrator/runstate.py src/agent_orchestrator/cli.py src/agent_orchestrator/outcomes.py src/agent_orchestrator/ui/runs.py src/agent_orchestrator/ui/activity.py
```

## Handoff Boundary
- Upstream: `T-vwIpSw`, `T-drPIif`, `T-nmL0HP` (registration).
- Downstream: `T-pdLR96`; the frontend (`T-pIZq3q`) reads `task_counts.awaiting_approval` and
  `approval_wait_seconds`.

## Artifacts
- Code as listed; golden files; evidence in STATUS.md.
