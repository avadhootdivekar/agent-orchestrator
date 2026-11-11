# STATUS

- ID: `T-8knqVe-dashboard-usage-panel`
- Updated At: 2026-11-11
- State: Partial — usage + governance-events API and panel landed; capped-resume route, paused list and generic-resume 409 unmet
- Owner: TBD (assigned at sprint start)

## Reconciliation 2026-11-11 (By: developer · Role: developer · unit `w24-07-a1-task-status-sync` · docs only)
- Checked against branch `ad/enhancements-10nov` at HEAD `1d5c6f9`. Any older line below that says "not started", "uncommitted" or "in the working tree" is superseded by this block where they disagree.
- State: **Partial — usage + governance-events API and panel landed; capped-resume route, paused list and generic-resume 409 unmet**
- Landed and committed (`d7cb6a7`): `GET /api/schedules/{id}/usage?at=`, `GET /api/schedules/governance-events`, `ScheduleUsage.tsx` mounted from the Schedules panel, bundle rebuilt; H-2/F-3 hardening (`usage?at=` 400 not 500) in `4b9d6a3`; R-2 read-only ledger on the usage route; `make ui-test` 915 passed. The old "uncommitted" state is superseded.
- **Unmet / open**: **AC3** `POST /api/schedules/{id}/runs/{run_id}/resume-capped`; **AC5** paused-run list + Resume button; **AC10 (M5)** generic dashboard resume refusing governed cap raises - the route-level 409 exists for `cost_cap_usd` (H-2) but the New-run/template start route skips `_check_cost_cap_option` (T-lTW88y I-7, Info); overshoot display needs the ledger to expose it per window. `capped_resume.py` is now committed, so the blocker is gone; the work is simply not done.

## This update
- 2026-11-11 — By: developer (unit `w20-08-a2-docs-adr-sync`) · Role: developer · Date: 2026-11-11 · Comment: UNFINISHED. `GET /api/schedules/{id}/usage` (`ui/app.py`, `ui/service.py`, `ui/src/*`) is present only in the uncommitted working tree, built bundle assets deleted/regenerated there. Not reviewed. HLD §26.2.
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
