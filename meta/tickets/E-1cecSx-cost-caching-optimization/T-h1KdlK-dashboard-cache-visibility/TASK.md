# TASK: T-h1KdlK-dashboard-cache-visibility

## Metadata
- Task ID: `T-h1KdlK-dashboard-cache-visibility`
- Epic ID: `E-1cecSx-cost-caching-optimization`
- Owner: delegated `developer` agent
- Created: 2026-09-21
- Last Updated: 2026-09-21
- Status: `Done` (backend Done + frontend Done — see STATUS.md)
- Estimate: `1-2 days`

## Requirements Mapping
- Requirement IDs: FR-B4-1

## Description
See `docs-md/cost-caching-optimization-hld.md` §4. `TaskRunState.cumulative_cache_creation_
input_tokens`/`.cumulative_cache_read_input_tokens` already exist (E-9h3m7k) but nothing computes
or surfaces a hit-rate/effectiveness figure. **Locked-in constraint (do not violate): cache-stat
(and, per T-J1b0FN, timing) visibility must be on-demand/expandable — a detail drill-down, an
expandable row, or a separate report view — NEVER a new default column on the main task table.**

Dashboard backend lives in `src/agent_orchestrator/ui/*.py` (FastAPI: `ui/service.py`,
`ui/app.py`, `ui/runs.py`). The rendered frontend is a SEPARATE source tree at repo-root `ui/`
(Vite/React, `ui/src/`, `ui/package.json`) that builds into `src/agent_orchestrator/ui/static/`.
**First step: locate the dashboard's existing expandable/detail-drill-down pattern in `ui/src/`
before writing any new UI code** — the epic requires reusing it, not inventing a parallel
mechanism. If no such pattern exists yet, say so explicitly in your handoff and propose the
smallest one consistent with the rest of the UI, rather than assuming one exists.

## Acceptance Criteria
1. New `reporting.py::cache_effectiveness(...)` pure function (coordinate file ownership with
   `T-J1b0FN` — that task creates `reporting.py`; if it lands first, add to it; if this task
   starts first, create it with just this function and leave room for `T-J1b0FN`'s additions).
   Hit rate = `cache_read / (cache_read + cache_creation + input_tokens)`, guarding the
   zero-denominator case (return `None`/0, document the choice).
2. Backend: `ui/service.py::run_detail` (or the per-task detail path it composes) includes the
   new cache-effectiveness figure(s) as an ADDITIONAL field on the existing per-task detail
   payload — not a new top-level list/table endpoint.
3. Frontend: cache-effectiveness (and, once `T-J1b0FN` lands, timing) surfaced inside the
   dashboard's existing expand/detail affordance for a task row. Unit/component test if the
   existing frontend has a test harness; otherwise a manual verification note plus a backend
   contract test is acceptable (document which).
4. Unit test: `cache_effectiveness` against synthetic `TaskRunState`/totals, including the
   zero-denominator case.
5. Backend contract test: `run_detail`'s response JSON includes the new field(s) for a task with
   non-zero cache usage.
6. `ruff`/`mypy` clean on touched Python files; frontend build (`npm run build` or equivalent in
   `ui/`) succeeds if frontend files are touched.
7. Do NOT touch `engine.py`, `models.py` (beyond reading existing fields), `isolation/*`,
   `hooks.py`, or `cli.py`'s existing commands.

## Risks
- Frontend build tooling (`ui/node_modules`, Vite) may need a fresh `npm install`/build step in
  CI — note in handoff whether the existing CI already runs this, or whether the change is
  backend-only if frontend build isn't verifiable in this environment (state which, do not
  claim an unverified frontend build succeeded).

## Dependencies
- Coordinates with `T-J1b0FN` on shared ownership of `reporting.py` — do not let either task
  block on the other; both may add functions to the same file across parallel work, resolved at
  integration time by the coordinating `dev-epic` agent if a conflict arises.

## Pseudocode / Algorithm
See design doc §4.

## Schemas / Interface Notes
- Interface: `reporting.py::cache_effectiveness`; `ui/service.py::run_detail` response shape
  (additive field(s)); frontend component change inside the existing detail-expand pattern.
- Artifacts: none new — reads already-persisted `RunState`/`TaskRunState` fields only.

## Handoff Boundary
- Upstream: `T-J1b0FN` (shares `reporting.py`, non-blocking).
- Downstream: `T-UJElTR` (e2e demonstration checks the dashboard REST response carries the new
  field).

## Artifacts
- Docs/comments: `meta/tickets/E-1cecSx-cost-caching-optimization/T-h1KdlK-dashboard-cache-visibility/`
- Design doc: `docs-md/cost-caching-optimization-hld.md` §4
