# STATUS

- ID: `T-Ap1Xs3-dashboard-schedules`
- Updated At: 2026-11-11
- State: **Done** (review w26-08 recorded in `REVIEW.md`; non-blocking follow-ups W-2, W-3(a)(c), S-1..S-7 open)
- Owner: developer agent (overseer unit w15-05-a1-dashboard-schedules)

## Review outcome — By: reviewer agent (Opus) · Role: reviewer · Date: 2026-11-11 · unit `w26-08-a1-dashboard-review`
- Comment: `REVIEW.md` finalised and committed. **Done.** All ACs 1-18 (+14a) Met; no Critical.
- AC18 figure (clean worktree at `defa662`): full `pytest -q` **12265 passed, 11 failed, 32 skipped** — none of the 11
  in this task's files (9 load-sensitive bench/audit tests pass in isolation; 2 bench tests fail only in a detached
  worktree and pass in the checkout). Targeted schedules API/auth tests 54 passed; the eight schedule methods at
  **100 %** statement coverage; `ruff`/`ruff format` clean; `mypy src` clean for this task (4 worktree-only
  `_version.py` errors from the missing generated `_build_info.py`); `tsc -b` clean; vitest **915 passed**;
  `npm run build` reproduces the committed `ui/static/` byte-identically. Re-check at tip `69dd4c0`: 109 passed
  (schedules API/auth + `test_until.py`).
- W-1 (completed-schedule enable under a running daemon) **fixed** by `ed84cb4` (shared engine-routed
  `restart_completed_schedule`), verified in code and tests.
- **Unfinished (non-blocking, should fix before promoting to the live engine):** W-2 Run now reports "queued" for
  an inert workspace (not registered / schedules not opted in / disabled machine-wide) — expose an `inert_reason`;
  W-3 (a) `_set_enabled` apply closure and (c) run-now request body still duplicated between `schedules/cli.py`
  and `ui/service.py`; S-7 `restarted` does not distinguish applied vs queued; S-1..S-6 in `REVIEW.md`.

## Sync 2026-11-11 (w27-02, developer; docs only; HEAD `c4e3871`)
- Supersedes the w24-07 "no review recorded" line below. `REVIEW.md` (unit w26-08) exists as an **uncommitted draft** with unfilled verdict/AC18-figure placeholders; its recorded outcome so far: no Critical; all 18 ACs Met; Warnings W-1 (enabling a `completed` schedule always 409s under a running daemon and the message says retry; same root cause as T-Un0Lm6 M1), W-2 (Run now says queued for a workspace whose schedules are inert), W-3 (CLI logic triplicated into `ui/service.py`).
- **Unfinished:** finalize/commit that review, fix W-1/W-2 (W-3 refactor optional). Not edited by this sync.

## Reconciliation 2026-11-11 (By: developer · Role: developer · unit `w24-07-a1-task-status-sync` · docs only)
- Checked against branch `ad/enhancements-10nov` at HEAD `1d5c6f9`. Any older line below that says "not started", "uncommitted" or "in the working tree" is superseded by this block where they disagree.
- State (history, w24-07 snapshot; superseded by the header State on line 5): **Implemented — awaiting review (ACs 1-18 implemented)**
- ACs 1-18 implemented and committed (`62ae321`); 24 + 6 API/auth tests, `make ui-typecheck` clean, `make ui-test` 915 passed (w23-05).
- **Why not Done**: no review recorded; AC18's full-suite figure lives only in the unit report (`outputs/waves/w15/w15-05-a1-dashboard-schedules.md`); the live engine/UI is unchanged until a human runs the install flow (by design, not run).
- Security posture (AC17) was handed to T-Se4Bk5 and confirmed in SECURITY.md.

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
0. (w26-08) Fix W-2 and W-3(a)(c) as a follow-up; record S-1..S-7 decisions. Items 1-2 below: review done (this task); T-Se4Bk5/T-Te3Qw8/T-Dc6Zr2 items stay with those tasks.
1. Review (`T-Se4Bk5` for the mutation-route posture; `T-Te3Qw8` e2e tier; `T-Dc6Zr2` screenshots/docs).
2. Human: promote via the Install / deploy path in `CLAUDE.md` after merge.
