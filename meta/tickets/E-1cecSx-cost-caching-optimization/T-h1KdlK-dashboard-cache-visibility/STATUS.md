# STATUS

- ID: `T-h1KdlK-dashboard-cache-visibility`
- Updated At: 2026-09-21
- State: `Done` (backend Done, frontend Done)
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
- **Frontend (done, `developer` agent)**: pattern discovery found the dashboard has ONE
  genuine existing expandable/on-demand disclosure convention in `ui/src/` — the native
  `<details>`/`<summary>` element used by `components/viewer/HtmlPreview.tsx` (its
  `.html-preview-dropped` panel), styled with a minimal dedicated CSS block, not a UI
  library/modal/accordion component. `RunDetail.tsx`'s per-task table itself is flat (no
  existing per-row expand affordance) — its "Integration" column/cell (S-5,
  `IntegrationCell`) is inline, always-visible, not on-demand, so it was read for context but
  NOT copied as the reuse target (copying it would have meant adding new always-visible cell
  content, i.e. exactly the "new default column" the constraint forbids). Reused the
  `<details>`/`<summary>` mechanism verbatim: new `CacheDetails` component, collapsed by
  default, nested INSIDE the existing Task-id cell (first column) — zero new `<th>`/column,
  zero new row. Surfaces `cache_hit_rate` (new `format.ts::formatPercent`, one decimal place,
  `null` -> `"n/a"` — matches the sibling CLI's own convention for the identical field,
  `cli.py::hit_rate_str` from `T-J1b0FN`'s `ao report timing`, confirmed by reading that code
  before choosing the string), `cache_read_tokens`, `cache_creation_tokens` (existing
  `formatCount`).
- Touched: `ui/src/types.ts` (3 new `TaskStat` fields), `ui/src/format.ts` (`formatPercent`),
  `ui/src/components/RunDetail.tsx` (`CacheDetails` + wiring), `ui/src/styles.css`
  (`.task-cache-details`/`.task-cache-body`, mirrors `.html-preview-dropped`'s existing
  rules), `ui/src/test/format.test.ts` (+3 tests), `ui/src/test/run-detail.test.tsx` (new, 3
  tests: no new column header, disclosure collapsed by default with correct figures, n/a-vs-
  0% distinction) — plus the rebuilt `src/agent_orchestrator/ui/static/` bundle (this repo
  commits built output on purpose, per its `.gitignore` comment). No Python file touched.
- Committed directly to `ad/cost-perf-hooks-skills` (commit `35cffaf`) — staged by explicit
  path only, since `cli.py`/`reporting.py`/`tests/test_reporting.py` had concurrent, unrelated
  in-flight edits (another parallel B-task agent, e.g. `T-J1b0FN`/`T-Ar8HJF`) sitting
  uncommitted in the same working tree at the time; those were deliberately left untouched and
  unstaged, not part of this task's commit.

## Evidence
- Backend (unchanged from prior update): `pytest tests/ui -q` → 378 passed at the time of the
  backend-only update.
- Frontend, this update, all commands actually run (not assumed):
  - `cd ui && npm run typecheck` (`tsc -b --noEmit`) → clean, zero errors.
  - `cd ui && npm run build` (`tsc -b && vite build`) → succeeded (`✓ built in 134ms`,
    52 modules transformed); rebuilt bundle committed.
  - `cd ui && npm run test` (`vitest run`) → **85 passed (9 test files)**, 0 failed. `ui/`
    has no `lint` script and no ESLint config at all (checked `package.json` scripts and the
    `ui/` directory directly) — `typecheck` is this project's only static-analysis gate for
    the frontend, and it is clean.
  - `pytest tests/ui -q` (repo root, `.venv/bin/python -m pytest`) → **380 passed** (run
    AFTER the frontend change, to confirm no Python-side regression from a frontend-only
    change). This is 2 more than the 378 recorded in the prior backend-only update; the delta
    is NOT from this task (zero Python files touched by this task, confirmed via
    `git status`/`git diff --stat` before committing) — it reflects other B-workstream tests
    that landed on this shared branch from a concurrent, still-uncommitted-at-commit-time
    agent (see "Committed" note above). Recorded here for an honest count, not claimed as
    this task's own evidence.

## Risks / Blockers
- None remaining. `ui/` has no lint script/config (only `typecheck`) — noted as a fact about
  this project's tooling, not a gap introduced by this task.
- This task's commit intentionally excludes concurrently-modified `cli.py`/`reporting.py`/
  `tests/test_reporting.py` (another agent's in-flight work on the same shared branch at
  commit time) — flagging for the coordinating `dev-epic`/`manager` agent so that work isn't
  mistaken for lost or reverted; it was simply never staged by this task.

## Next actions
1. None for this task — Done. Roll into `T-UJElTR` late-gate verification (dashboard REST
   response + rendered UI carrying the cache-effectiveness field) once the epic reaches that
   gate.

By: developer agent · Role: developer · Date: 2026-09-21 · Comment: Frontend half complete.
Reused `HtmlPreview.tsx`'s existing `<details>`/`<summary>` disclosure (the dashboard's only
genuine on-demand/expandable convention) inside the existing Task cell — no new table column,
no new row, no new display mechanism. `npm run typecheck`/`build`/`test` all actually run and
passing (85/85); `pytest tests/ui -q` 380 passed confirming no Python-side regression (zero
Python files touched by this task). Committed to `ad/cost-perf-hooks-skills` as `35cffaf`,
staged by explicit file path to avoid sweeping in a concurrent agent's uncommitted
`cli.py`/`reporting.py` edits present in the same working tree at commit time.
