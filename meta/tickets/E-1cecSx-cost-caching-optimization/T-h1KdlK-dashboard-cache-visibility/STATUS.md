# STATUS

- ID: `T-h1KdlK-dashboard-cache-visibility`
- Updated At: 2026-09-21
- State: `In Progress` (backend Done, frontend delegated/in progress)
- Owner: `dev-epic` agent (backend), delegated `developer` agent (frontend)

## Early-gate outcome (2026-09-21)
- Architect finding 4c: split the AC into a backend gate (durable, testable) and a separately
  gated frontend criterion (an unscoped Vite/React change, "delegated to find and reuse a
  pattern, not assume its shape") — the epic's completion must not be held hostage to an
  unscoped frontend task. Adopted.

## This update
- **Backend (done, implemented directly)**: `reporting.py::cache_effectiveness`/
  `run_cache_effectiveness` (shared with B2); wired into `ui/runs.py::TaskStat` (3 new fields:
  `cache_read_tokens`, `cache_creation_tokens`, `cache_hit_rate`) and populated in
  `RunRepository.detail()` — additive on the EXISTING per-task detail payload
  (`run_detail`/`ui/app.py`'s REST endpoint already serializes `TaskStat` via `asdict`, so no
  endpoint code change was needed beyond the dataclass field addition). 2 new backend contract
  tests in `tests/ui/test_runs.py` (populated case + zero-denominator-is-None case).
- **Frontend (delegated to `developer`, in progress)**: locate the dashboard's existing
  expandable/detail-drill-down pattern in `ui/src/` (repo-root Vite/React tree, separate from
  `src/agent_orchestrator/ui/`) and surface `cache_hit_rate`/`cache_read_tokens`/
  `cache_creation_tokens` inside it — never a new default column on the main task table.

## Evidence
- `pytest tests/ui -q` → 378 passed (full UI suite, no regressions) — includes 2 new
  cache-effectiveness contract tests within that count.
- `ruff check` + `mypy src/agent_orchestrator/ui/runs.py` — clean.

## Risks / Blockers
- None for backend (done). Frontend pending delegated developer's pattern discovery + build
  verification (state explicitly whether `npm run build` was actually run, per this ticket's
  own AC 6 — do not claim an unverified frontend build succeeded).

## Next actions
1. Developer: find `ui/src/`'s existing expand/detail pattern, wire cache stats into it,
   verify frontend build.
2. Roll into `T-UJElTR` late-gate verification once landed.
