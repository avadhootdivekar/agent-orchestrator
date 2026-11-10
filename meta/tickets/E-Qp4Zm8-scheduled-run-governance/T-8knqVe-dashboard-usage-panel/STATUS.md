# STATUS

- ID: `T-8knqVe-dashboard-usage-panel`
- Updated At: 2026-11-11
- State: Partial (read-only usage panel landed; resume POST + M5 deferred)
- Owner: TBD (assigned at sprint start)

## This update
- Landed (w20-06): `GET /api/schedules/{id}/usage?at=` (404 unknown id, 400 malformed `at`; per-window runs used/max,
  spend actual/reserved/max, confidence, `skipped` + `quota_exhausted` counts) and
  `GET /api/schedules/governance-events?limit=&id=` (newest-first, limit 1..500, 404 unknown id, prompt keys dropped).
  `ScheduleUsage.tsx` mounted from a per-row **Usage** toggle in `Schedules.tsx` (meters as numbers, ⚠ on
  partial/unknown, reset relative + absolute, 50-event feed, polls at `SCHEDULES_POLL_MS`). UI bundle rebuilt
  (`make ui-build`) and committed.
- NOT done (AC3, AC5 paused list/Resume, AC10/M5): `POST .../resume-capped` and paused-run list. The usage builder
  `build_usage_doc` (T-rwj4nV) is not merged on this branch, so `DashboardService.schedule_usage` composes
  `windows_for` + `UsageLedger.usage` directly (same primitives as the gate). `capped_resume.py` is still uncommitted
  carry-over work (w18-01), so the resume route was not wired. Overshoot display needs the ledger to expose it per window.

## Evidence
- `tests/ui/test_schedule_usage_api.py` (new, 5 tests) + all `tests/ui`: green; `ui/src/test/schedule-usage.test.tsx`
  (new, 3 tests) + full vitest 915 green; ruff/format/mypy/tsc clean.

## Risks / Blockers
- Blocked until E-Sc9Rt4 `T-Ap1Xs3` (its Sprint 2), `T-rwj4nV`, `T-nI4DBf`, `T-Uz0Uol` have merged.

## Next actions
1. Wire `resume-capped` POST + paused list once `capped_resume.py` is committed; add overshoot to window rows.
2. Read the upstream tasks' **merged code** (including E-Sc9Rt4's), not only the HLD, before starting.
2. Implement against the acceptance criteria in `TASK.md`, in AC order; report the pytest delta, ruff/format, mypy, coverage.
3. Hand the POST surface to `T-lTW88y` for security review.
