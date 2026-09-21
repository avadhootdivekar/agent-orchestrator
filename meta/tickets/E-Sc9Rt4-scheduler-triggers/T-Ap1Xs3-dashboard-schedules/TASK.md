# TASK: T-Ap1Xs3-dashboard-schedules

## Metadata
- Task ID: `T-Ap1Xs3-dashboard-schedules`
- Epic ID: `E-Sc9Rt4-scheduler-triggers`
- Owner: developer agent
- Created: 2026-09-06
- Last Updated: 2026-09-06
- Status: Draft
- Estimate: 3 days

## Requirements Mapping
- Requirement IDs: FR-14 (see `../EPIC.md`)

## Description
The dashboard surface: five API routes plus one React panel showing upcoming and recent scheduled
runs, with enable/disable and run-now. Deliberately small and consistent with the existing
dashboard — this is not the "UI-driven workflow construction" epic (roadmap §3.3).

Two structural rules the existing code sets and this task must honor: behavior lives in
`ui/service.py` with **zero** framework imports, and `ui/app.py` route bodies are ≤5 lines that
call one service method and translate an exception (ADR-0010 D5). One deliberate improvement: the
existing routes discriminate error codes by **substring-matching exception messages**
(`"still running" in str(exc)`); these new routes use a **typed exception hierarchy** instead.
New surface starts clean; the existing matching is left alone.

Read HLD §10 and §11, `ui/README.md`'s conventions, and the panel-addition file list in HLD §11.

Files you own (create/edit freely):
- `src/agent_orchestrator/ui/service.py` (edit: five new methods + the typed exceptions)
- `src/agent_orchestrator/ui/app.py` (edit: five new routes)
- `ui/src/components/Schedules.tsx` (new), `ui/src/App.tsx` (3-line edit),
  `ui/src/api.ts`, `ui/src/types.ts`, `ui/src/styles.css` (additive)
- `ui/src/test/schedules.test.tsx` (new)
- `tests/ui/test_schedules_api.py` (new)
- `src/agent_orchestrator/ui/static/**` (the committed `make ui-build` output)

Do NOT touch: `service/` (read-only imports), `schedules/store.py` beyond calling `mutate`,
`ui/processes.py`, `ui/files.py`, `ui/security.py`, `ui/htmlpreview.py`, `cli.py`.

## Acceptance Criteria

### API (behavior in `ui/service.py`)
1. `DashboardService` gains `list_schedules()`, `schedule_history(id, limit)`,
   `set_schedule_enabled(id, enabled)` and `request_schedule_fire(id, force)`. Each is plain
   Python with no framework import, testable directly, and takes its collaborators from the
   already-injectable constructor.
2. `list_schedules()` merges the workspace's `.ao/schedules.yaml` bindings with the service state
   dir's `ScheduleState`, returning per schedule: `id`, `kind`, `enabled`, `status`,
   `next_fire_at`, `last_fire_at`, `last_run_id`, `last_status`, `consecutive_failures`,
   `runs_count`, `max_runs`, and the workflow (or template) it points at. A missing state dir
   (no daemon has ever run) yields bindings with null state, **not** an error, plus a
   `daemon_seen: false` flag the UI renders as a warning.
3. `set_schedule_enabled` writes `.ao/schedules.yaml` through `ScheduleStore.mutate`; enabling a
   `completed` schedule applies `T-Un0Lm6` AC4's `runs_count` reset. `request_schedule_fire`
   writes a drop-directory request (HLD §6.9) and **never launches anything itself** — a reviewer
   must be able to grep this module and find no `Popen` and no `launch_run`.
4. A typed hierarchy — `ScheduleApiError` with `ScheduleNotFound`, `ScheduleFileInvalid`,
   `ScheduleAlreadyActive` — carries the status code as a class attribute. Route bodies map
   `exc.status_code` directly; no substring matching is introduced.

### Routes (thin adapter in `ui/app.py`)
5. The five routes in HLD §10 with the stated codes: `GET /api/schedules` (200 / 400 on an
   invalid file, with the parse error in `detail`), `GET /api/schedules/{id}/history?limit=`
   (200 / 404), `POST /api/schedules/{id}/enable` and `/disable` (200 / 404),
   `POST /api/schedules/{id}/run-now` (202 / 404 / 409 when already active and not `force`).
6. Every route body is ≤5 lines. The mutating routes are covered by the existing
   `SecurityMiddleware` Origin + JSON-content-type checks with no new exemptions; a bodyless
   `POST .../enable` follows the same deliberate exemption `POST .../resume` already relies on.
7. `GET /api/schedules` is registered **before** any `/api/schedules/{id}` route so a literal
   path cannot be shadowed — the same ordering care `/api/runs/stats` already needs.

### Panel
8. `Schedules.tsx` is a named-export function component, hooks only, no state library, following
   `ui/README.md`: status is glyph + word (never colour alone), colours from theme tokens in
   `styles.css` (no hex literals), wide content inside `.table-wrap`, reuse of `StatusChip` /
   `Tile` / `ErrorBanner` / `Empty` from `components/common.tsx`, and pure display helpers in
   `format.ts` rather than in the component.
9. Polling follows the `RunsList.tsx` pattern (`useCallback` refresh + `setInterval` + cleanup)
   at `POLL_MS = 10000` — schedules change on the order of minutes, so the 3–4 s run cadence
   would be wasteful. State the reason in a comment.
10. The table shows id, kind, next fire (relative **and** absolute — a relative-only time is
    useless for a cron schedule), last fire, last run (clicking it opens the existing `RunDetail`
    view via `openRun`, reusing the shell's existing mechanism), status chip, an enable/disable
    toggle, and a *Run now* button whose 202 renders as "queued — will fire within ~15 s", not as
    "started".
11. An expandable row shows the last 20 history events including **suppressed** fires with their
    reason — what did not happen is the diagnostic operators need.
12. `App.tsx` gains exactly three things: `"schedules"` in the `View` union, one `NAV` entry
    (`{ id: "schedules", label: "Schedules", glyph: "⏱" }`), one ternary arm.
13. The panel also surfaces `GET /api/launches`, which exists on the backend today with no UI at
    all — a small free win while this file is open, rendered as a collapsed "recent launches"
    section.
14. `make ui-build` is run and `src/agent_orchestrator/ui/static/` is committed — the manual step
    `ui/README.md` requires. `make ui-typecheck` and `make ui-test` both pass.

### Tests
15. `tests/ui/test_schedules_api.py` via `TestClient` (reusing `tests/ui/conftest.py`'s `client`
    / `service` fixtures and the autouse `_allow_testclient_host`): every route's success and
    every error code; `run-now` asserting a request file appeared and **no** launch occurred;
    `enable` on a `completed` schedule asserting the `runs_count` reset; an invalid
    `.ao/schedules.yaml` returning 400 with the parse error rather than a 500.
16. `ui/src/test/schedules.test.tsx` with mocked `fetch`: renders rows, toggles enable, clicks
    *Run now* and asserts the queued-not-started wording, renders the `daemon_seen: false`
    warning, and renders an empty state.
17. Security posture note recorded in `STATUS.md`: these routes let an unauthenticated caller
    toggle a schedule and request a fire. That is **not** a new exposure class — the same caller
    can already `POST /api/runs` — but it must be stated as a chosen posture, and the routes must
    **not** accept a workflow path or run args from the request body; they act only on schedules
    already declared in the workspace file.
18. `uv run pytest -q` green with the delta reported; ruff + format clean; mypy whole-tree count
    reported; coverage ≥80 % on the new `ui/service.py` methods.

## Risks
- Forgetting `make ui-build` + committing `static/` means the shipped wheel serves a dashboard
  without the panel while every test passes. AC14 is the guard; verify with `git status` that
  `static/` actually changed.
- `types.ts` is a **hand-maintained** mirror of the API shapes — it is not generated from the
  OpenAPI schema. A field renamed in `ui/service.py` will not fail any test until the panel
  renders `undefined`. Keep the two edits in the same commit.
- Introducing typed exceptions next to the existing substring matching creates two idioms in one
  file. That is deliberate (AC4) but must be stated in a comment so a later reader does not
  "unify" them by reverting to substrings.
- Route-ordering (AC7) has bitten this file before with `/api/runs/stats`.

## Dependencies
- `T-Sd1Kq7` (store), `T-Fr2Nx8` (state + event log), `T-Cl6Jn9` (the semantics these routes
  mirror — do not invent different ones), `T-Un0Lm6` (the `completed` lifecycle and the
  `runs_count` reset).
- Reads (read-only): `ui/app.py`'s existing route table and error-translation style,
  `ui/README.md`, `ui/src/components/RunsList.tsx` (the polling pattern), `service/paths.py`.

## Pseudocode / Algorithm
```text
# ui/service.py -- behavior, no framework imports
FUNCTION DashboardService.request_schedule_fire(schedule_id, force) -> dict:
  file = ScheduleStore(self._root).load()             # ScheduleFileInvalid -> 400
  b    = file.binding(schedule_id) OR RAISE ScheduleNotFound(schedule_id)      # -> 404
  st   = ScheduleStateStore(default_state_dir()).get(self._root, schedule_id)
  IF st.active_fire_keys AND NOT force: RAISE ScheduleAlreadyActive(schedule_id)  # -> 409
  write_request_file(default_state_dir(), {workspace_root: self._root,
                                           schedule_id: schedule_id,
                                           requested_at: utcnow().isoformat(),
                                           force: force, requested_by: "ui"})
  RETURN {"queued": True, "schedule_id": schedule_id}   # 202 -- NEVER launches here

# ui/app.py -- thin adapter, <=5 lines
@app.post(f"{API_PREFIX}/schedules/{{schedule_id}}/run-now", status_code=202)
def run_schedule_now(schedule_id: str, force: bool = False) -> dict:
    try:
        return service.request_schedule_fire(schedule_id, force)
    except ScheduleApiError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
```

## Schemas / Interface Notes
- Interface / HTTP: the five routes in HLD §10.
- Interface / API: `DashboardService.{list_schedules, schedule_history, set_schedule_enabled,
  request_schedule_fire}`; `ui.errors.{ScheduleApiError, ScheduleNotFound, ScheduleFileInvalid,
  ScheduleAlreadyActive}`.
- Spec / data schema: `Schedule` and `ScheduleEvent` TypeScript interfaces in `ui/src/types.ts`,
  hand-mirrored from the route responses.
- Triggers / events: writes `<state_dir>/schedules/requests/<uuid>.json`; emits
  `schedule.enabled` / `schedule.disabled` with `requested_by: "ui"`.
- Artifacts: `.ao/schedules.yaml` (enable/disable), the request drop directory, and the committed
  `src/agent_orchestrator/ui/static/` bundle.

## Handoff Boundary
- Upstream: `T-Sd1Kq7`, `T-Fr2Nx8`, `T-Cl6Jn9`, `T-Un0Lm6`; HLD §10/§11; ADR-0010 D5/D7;
  ADR-0014 D10.
- Downstream: `T-Te3Qw8` (e2e tier includes the dashboard path), `T-Se4Bk5` (reviews the
  unauthenticated-mutation posture), `T-Dc6Zr2` (screenshots/docs).

## Artifacts
- Docs/comments: `meta/tickets/E-Sc9Rt4-scheduler-triggers/T-Ap1Xs3-dashboard-schedules/`
- Large outputs: N/A
