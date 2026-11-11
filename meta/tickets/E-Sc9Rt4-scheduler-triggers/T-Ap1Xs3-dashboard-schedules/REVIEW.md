# REVIEW: T-Ap1Xs3-dashboard-schedules

- By: reviewer agent (Opus) · Role: reviewer · Date: 2026-11-11 · Unit: `w26-08-a1-dashboard-review`
- Scope: implementation commit `62ae321` (backend `ui/errors.py`, `ui/service.py` schedule methods, the five
  routes in `ui/app.py`; panel `Schedules.tsx` + `api.ts`/`types.ts`/`format.ts`/tab model; tests), read
  at branch tip `defa662` (`ad/enhancements-10nov`). Later additions in the same files by other tasks
  (`schedule_usage`, `governance_events`, `paused_runs`, `request_capped_resume`: T-8knqVe / E-Qp4Zm8)
  are **out of scope**. All checks below ran in a **clean detached worktree at `defa662`** because other
  units had uncommitted edits to `ui/service.py`/`ui/app.py` in the shared tree.
- Measured against: TASK.md ACs 1-18 (+14a), HLD `docs-md/scheduler-triggers-hld.md` §6.9/§10/§11,
  and the CLI semantics the routes must mirror (`schedules/cli.py` `enable`/`disable`/`run-now --queue`).

## Summary

The backend is a clean, small, well-tested thin-adapter design: four plain-Python service methods with
no framework imports, a typed `ScheduleApiError` hierarchy mapped by `exc.status_code`, five ≤5-line
routes, literal route registered first, run-now only writes the HLD §6.9 request file. All ACs are
implemented and the claims in STATUS.md reproduce. There are **no Critical findings**. Two Warnings are
real operator-facing gaps that should be fixed before the panel is promoted to the live engine:
enabling a `completed` schedule cannot work while a daemon is running (and the error says "retry"),
and *Run now* reports "queued" for a workspace whose schedules the daemon will ignore. A third Warning
is triplicated CLI logic that has already started to drift.

## AC verification (in code + re-run)

| AC | Verdict | Evidence |
|---|---|---|
| 1 service methods, no framework import, injectable | Met | `ui/service.py` `list_schedules`/`schedule_history`/`set_schedule_enabled`/`request_schedule_fire`; `schedule_state_dir` + `clock` ctor args; no fastapi import in the module. |
| 2 merged list, null state + `daemon_seen:false` | Met | `list_schedules`; `test_list_without_state_dir_is_bindings_with_null_state`, `test_list_merges_state`. `max_runs` from `until.max_runs` (documented deviation; correct — there is no `limits.max_runs`). |
| 3 mutate via store, completed reset, never launches | Met (see W-1) | `set_schedule_enabled` → `ScheduleStore.mutate`; `_restart_completed` under `engine.lock`; `test_enable_completed_schedule_resets_runs_count`. AC wording "grep **this module** finds no `launch_run`" is literally false (pre-existing run-launch methods in `ui/service.py` call `self._supervisor.launch_run`); the test sensibly scopes it to `request_schedule_fire` (`test_service_module_never_launches`). Intent met. |
| 4 typed hierarchy, status_code class attr | Met | `ui/errors.py`; `test_typed_errors_carry_status_codes`; second-idiom comment present. |
| 5 routes + codes | Met | `ui/app.py` schedules block; tests for 200/202/400/404/409/422. |
| 6 ≤5-line bodies, SecurityMiddleware, bodyless POST | Met | `test_route_bodies_are_at_most_five_lines`; `api.ts` `postJson` without body = the `/resume` exemption shape. No test sends a cross-origin POST to these routes (S-5). |
| 7 literal route first | Met | `test_literal_schedules_route_precedes_parametrised_ones`. |
| 8 component conventions | Met (S-3) | named export, hooks only, `StatusChip`/`Tile`/`ErrorBanner`/`Empty` reused, helpers in `format.ts`, `.table-wrap`, no hex literals. |
| 9 `usePolling` @ 10 s with reason | Met | `SCHEDULES_POLL_MS = 10000` + comment. |
| 10 table columns, relative+absolute, openRun, queued wording | Met | `Schedules.tsx`; vitest "Run now renders 'queued', never 'started'", "opens the last run through the tab mechanism". |
| 11 history incl. suppressed + reason | Met | `HistoryRows`, `isSuppressedEvent`; API + vitest tests. |
| 12 one `TabKind` + NAV entry | Met | `App.tsx:25`, `tabs/model.ts`, `tabs/TabView.tsx`. |
| 13 do not re-surface `/api/launches` | Met | only a link to the Runs tab in the warning banner. |
| 14 `make ui-build` committed, typecheck + test pass | Met | `npm run build` at `defa662` reproduces the committed `ui/static/` **byte-identically** (`git status` clean after build); `tsc -b --noEmit` clean; vitest 72 files / **915 passed**. |
| 14a auth | Met | `tests/auth/test_schedules_auth.py` (401 without session, works with one); comment in route block. |
| 15 API tests | Met | 21 tests in `tests/ui/test_schedules_api.py` cover every listed case. |
| 16 vitest | Met | 11 tests in `schedules.test.tsx`, all pass. |
| 17 security posture in STATUS.md | Met | STATUS.md "Security posture (AC17)"; hostile-body test. |
| 18 suite / ruff / mypy / coverage | See "AC18 figure" below | |

## AC18 figure (verified and recorded)

Original unit report (`outputs/waves/w15/w15-05-a1-dashboard-schedules.md`, run on the dirty shared tree at
implementation time): `pytest -q` **11765 passed, 6 failed, 32 skipped** (17m41s); the 6 failures were
outside this task's files (5 from another unit's then-uncommitted cost-cap work, 1 process-group bench
test). That run was therefore **not green**, which the report stated honestly.

Re-verified by this review on a clean worktree at `defa662` (2026-11-11):

AC18_RESULT_PLACEHOLDER

- Targeted: `tests/ui/test_schedules_api.py` + `tests/auth/test_schedules_auth.py` +
  `tests/auth/test_auth_off_regression.py`: **54 passed**.
- Coverage of the task's eight schedule methods/helpers in `ui/service.py` (`_schedule_state_dir_path`,
  `_load_schedule_file`, `_require_schedule`, `list_schedules`, `schedule_history`, `set_schedule_enabled`,
  `_restart_completed`, `request_schedule_fire`): **76/76 statements = 100 %** (≥80 % required).
- `ruff check .` clean; `ruff format --check .` 774 files clean.
- `mypy src`: 0 errors in the task's files. The worktree run reports 4 errors, all in
  `src/agent_orchestrator/_version.py` (`no-redef` on `BUILD_*`) caused by the git-ignored generated
  `_build_info.py` being absent from a fresh worktree — an environment artifact, not a code defect.

## Critical

None.

## Warnings (should fix before promoting to the live engine)

**W-1 — Enabling a `completed` schedule is impossible while the daemon runs, and the 409 says "Retry in a
moment".** `ui/service.py` `_restart_completed` takes `engine.lock` non-blocking; the service daemon takes that
lock in `ScheduleEngine.start()` (`service/cli.py:376`) and holds it until shutdown (`:414`). So in the normal
dashboard situation (`ao service` running) the toggle on a completed schedule always returns 409, and retrying
never succeeds. The CLI has the same limitation but at least says "Stop the daemon and run `enable` again"; the
UI message is misleading and the operator has no in-panel path to restart a completed schedule.
*Why it matters:* AC3's restart behaviour only works when no daemon is running — i.e. almost never from the
dashboard. *Next step:* minimum — change the message to match the CLI (stop the daemon) and render it next to
the toggle (it is already shown via `notice`); proper fix — add a `restart` request kind to the §6.9 drop
directory that the daemon drains under its own lock (same pattern as run-now), used by both CLI and UI.

**W-2 — *Run now* says "queued — will fire within ~15 s" for a workspace whose schedules the daemon ignores.**
`request_schedule_fire` always writes the request and returns 202. The CLI's `run-now --queue` additionally warns
when the workspace is not registered / not opted in (`schedules/cli.py` `_inert_reason`) or schedules are
disabled machine-wide. The panel has neither signal: schedule opt-in is **default off** per workspace, so a
registered workspace with `schedules: false` shows its schedules, accepts *Run now*, and nothing ever fires.
`daemon_seen` does not help (it is true whenever any workspace's daemon wrote `state.json`).
*Next step:* move `_inert_reason` (+ the machine-wide check) out of the CLI into a service-layer helper, add an
`inert_reason: str | null` field to `list_schedules`, render it in the warning banner, and include it in the
run-now 202 body so the notice can say "queued, but this workspace's schedules are inert: …".

**W-3 — CLI logic copied three times into `ui/service.py`, already drifting.** (a) the `enabled`-flip `apply`
closure = `schedules/cli.py` `_set_enabled`; (b) the engine-lock try-lock = `_hold_engine_lock`; (c) the
run-now request body/path = `_queue_request`. Drift is visible: the 409/CLI messages differ (W-1), and CLI
`enable` emits `schedule.enabled` without the `requested_by` field while the UI includes it.
*Next step:* extract one small `schedules/ops.py` (or `service/schedule_requests.py`) with
`set_enabled(root, id, enabled)`, `hold_engine_lock(sdir)`, `restart_completed(sdir, root, id)` and
`queue_run_now(sdir, root, id, force, requested_by, now)`, called from both the CLI and `DashboardService`.

## Suggestions

- **S-1 Daemon liveness.** `daemon_seen` = "`state.json` exists" (documented). `service.schedule_engine.probe_engine_lock(sdir)`
  is a cheap non-blocking liveness probe; exposing `daemon_live` would let the banner also warn when the daemon
  has stopped.
- **S-2 Status column hides "disabled" when there is no state.** `Schedules.tsx` renders `—` whenever
  `s.status` is null, even for `enabled: false`; render the `disabled` chip whenever `!s.enabled`.
- **S-3 Inline `style={{ fontSize: 11 }}` ×7** in `Schedules.tsx` (and `muted` sub-lines) — a repeated magic
  literal; use one CSS class (e.g. `.muted.small`) in `styles.css`.
- **S-4 Sticky state.** Expanded history is fetched once and not refreshed by the 10 s poll; the per-row
  `notice` ("queued …") never clears. Refresh the open row's history in `load`, clear the notice on the next
  poll that shows a new `last_fire_at`.
- **S-5 AC6 test gap.** No test proves `SecurityMiddleware` rejects a cross-origin or non-JSON-bodied POST to
  `/api/schedules/{id}/{enable,disable,run-now}`; one parametrised test would pin AC6.
- **S-6** No `force` affordance in the UI (disclosed in STATUS.md) — fine for now; record as a follow-up if
  operators need it.

## Dimensions walked

SOLID/KISS — good (thin routes, behaviour in service). DRY — W-3. Magic literals — backend constants named
(`SCHEDULE_REQUESTED_BY`, limits, `SCHEDULE_STATUS_COMPLETED`); UI S-3. Pluggability — state dir and clock
injected; store/state classes concrete but these are internal seams, acceptable. Spec/DAG — N/A (no spec
change). Determinism/resume — clock injected (`requested_at`, event emit); request file written atomically
(`atomic_write_bytes`) with a uuid name, idempotent from the daemon side. Errors/logging — typed, wrapped with
`from exc` once at the route boundary; no swallowed errors. Testability — direct service tests + TestClient +
auth tests + vitest. Concurrency — yaml via the store's locked `mutate`; `state.json` touched only under
`engine.lock` (correct single-writer rule; consequence is W-1).

## Testing notes

- Mock seams used well: `schedule_state_dir` tmp dir, fixed `clock`, stub supervisor asserting no launch.
- Missing: S-5 (middleware on these routes); an integration test that a UI-written run-now request is actually
  drained by a real `ScheduleEngine` evaluate (belongs in `T-Te3Qw8`'s e2e tier); W-2 inert-workspace case once
  implemented.

## Doc fix made by this review (trivial, docs only)

`docs-md/scheduler-triggers-hld.md` §11 described the pre-F17 panel (`View` union + ternary arm, hand-rolled
`setInterval`, "surfaces `GET /api/launches` which has no UI"). Corrected to the shipped tab-model registration,
`usePolling`, and "does not re-surface `/api/launches`" (AC12/AC13). §10's "real authentication deferred"
sentence is also stale since ADR-0021 added optional session auth; left for `T-Dc6Zr2` (not trivial wording).

## Done / not-Done

DONE_PLACEHOLDER

## Pre-submit checklist

- [x] Scope confirmed (commit `62ae321`, read at `defa662`; later T-8knqVe/E-Qp4Zm8 methods excluded)
- [x] Three alignment levels: project (thin adapter, safe-by-default, never launches), task (every AC above), code intent (tests match claims)
- [x] Every dimension walked (section above)
- [x] Each finding has location / observation / why / next step
- [x] Bucketed by real severity: no merge blocker; W-1/W-2 user-facing, W-3 maintainability
- [x] Testing notes included
- [x] No code rewritten (only the HLD §11 doc correction)
