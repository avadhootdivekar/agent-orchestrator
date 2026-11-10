# STATUS

- ID: `T-Ap1Xs3-dashboard-schedules`
- Updated At: 2026-11-11
- State: Implemented — awaiting review (branch `ad/enhancements-10nov`)
- Owner: developer agent (overseer unit w15-05-a1-dashboard-schedules)

## This update — By: developer agent / Role: developer / Date: 2026-11-11
Implemented all ACs (1-18). Nothing is marked unfinished except the review/live-install steps below.

- **Backend**: `ui/errors.py` (new: `ScheduleApiError` + `ScheduleNotFound` 404 / `ScheduleFileInvalid` 400 /
  `ScheduleAlreadyActive` 409, `status_code` class attribute); `DashboardService.list_schedules /
  schedule_history / set_schedule_enabled / request_schedule_fire` (+ optional `schedule_state_dir`
  constructor arg, default `service.paths.default_state_dir()` resolved per call); five routes in `ui/app.py`
  (each body 4 lines; `GET /api/schedules` registered before every `/schedules/{id}/...`).
- **Panel**: `components/Schedules.tsx`, one new `TabKind` `schedules` + NAV entry (`⏱ Schedules`), `types.ts`,
  `api.ts`, pure helpers in `format.ts` (`formatRelative` with injected clock, suppressed-event classifier),
  additive CSS, `usePolling` at `SCHEDULES_POLL_MS = 10000` (reason in a comment). `make ui-build` run;
  `src/agent_orchestrator/ui/static/` regenerated and committed (new hashed bundles, `index.html` changed).
- **Read-only services**: `service/*` and `schedules/store.py` are only *imported* (store via `load`/`mutate`);
  none were edited. `request_schedule_fire` writes the HLD 6.9 request file (same body as `ao schedule run-now
  --queue`, `requested_by: "ui"`) and never launches.

## Deviations / decisions worth review
- `max_runs` (AC2) comes from `until.max_runs` (there is no `limits.max_runs`; `limits.quotas[].max_runs` are
  per-period quotas, not shown).
- `daemon_seen` = `<state_dir>/schedules/state.json` exists (the daemon has persisted state at least once).
  It is NOT liveness: a stopped daemon with old state shows `true`.
- Enabling a `completed` schedule resets state only while holding `engine.lock` non-blocking (the CLI's F9
  rule); if a daemon holds it the route returns 409 (`ScheduleAlreadyActive`) instead of racing its cache.
  The lock helper is a small local copy of `schedules/cli._hold_engine_lock` (that one is private to the CLI).
- `statusTone/statusGlyph` gained schedule statuses (`active`, `completed`, `quarantined`, `disabled`) so the
  existing `StatusChip` is reused unchanged. These words do not occur as run/task statuses.
- Run-now has no UI affordance for `force` (API supports `?force=true`); the 409 detail is shown in the row.
- `tests/auth/test_auth_off_regression.py::PRE_CHANGE_OPENAPI_PATHS` extended with the five new paths (it
  asserts the OpenAPI path set exactly).

## Security posture (AC17) — for `T-Se4Bk5`
With dashboard auth **off** (still the default) these routes let an unauthenticated caller toggle a schedule and
request a fire; with auth **on** all five sit behind the session (deny-by-default, AUTHENTICATED by omission;
tested: `tests/auth/test_schedules_auth.py` -> 401 without a session, success with one). That is **not** a new
exposure class — the same caller can already `POST /api/runs` — and it is a chosen posture. The routes accept
no workflow path or run args from the request body (tested: a hostile JSON body leaves the request file
unchanged); they act only on schedules already declared in `.ao/schedules.yaml`. `run-now` is a request file
the daemon validates and drains, so the daemon's governance gates still apply.

## Evidence
- `pytest tests/ui/test_schedules_api.py tests/auth/test_schedules_auth.py`: 24 + 6 pass. Coverage of
  `ui/service.py` schedule methods (list/history/enable/restart/run-now): 100% line coverage.
- `make ui-typecheck` clean; `make ui-test` 912 passed (71 files, +11 in `schedules.test.tsx`).
- `ruff check .` / `ruff format --check .` clean; `mypy src`: 0 errors in 230 files. `mypy .` (tests included)
  reports 1180 pre-existing errors in 121 files, none in files touched here.
- Full `pytest -q`: see the unit report `outputs/waves/w15/w15-05-a1-dashboard-schedules.md` for the final
  count (one pre-existing failure from another unit's uncommitted cost-cap work:
  `test_e2e_run_options_dashboard::test_schema_options_are_cli_flags`, `--cost-cap-usd` has no `ao run` flag yet).

## Risks / Blockers
- The shipped engine does not change until a human runs the install flow (self-hosting rule); not run here.
- `types.ts` is hand-mirrored: keep it in step with `list_schedules` / `schedule_history` shapes.
- Two error idioms now coexist in `ui/app.py` (typed here, substring there) — deliberate, commented.

## Next actions
1. Review (`T-Se4Bk5` for the mutation-route posture; `T-Te3Qw8` e2e tier; `T-Dc6Zr2` screenshots/docs).
2. Human: promote via the Install / deploy path in `CLAUDE.md` after merge.
