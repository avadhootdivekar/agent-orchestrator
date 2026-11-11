# STATUS

- ID: `T-8knqVe-dashboard-usage-panel`
- Updated At: 2026-11-11
- State: Partial — usage, governance-events, paused list + bounded Resume route/control and the M5 409 landed (w26-03); overshoot display (AC5) and a live-auth/e2e pass remain
- Owner: TBD (assigned at sprint start)

## Reconciliation 2026-11-11 (By: developer · Role: developer · unit `w24-07-a1-task-status-sync` · docs only)
- Checked against branch `ad/enhancements-10nov` at HEAD `1d5c6f9`. Any older line below that says "not started", "uncommitted" or "in the working tree" is superseded by this block where they disagree.
- State: **Partial — usage + governance-events API and panel landed; capped-resume route, paused list and generic-resume 409 unmet**
- Landed and committed (`d7cb6a7`): `GET /api/schedules/{id}/usage?at=`, `GET /api/schedules/governance-events`, `ScheduleUsage.tsx` mounted from the Schedules panel, bundle rebuilt; H-2/F-3 hardening (`usage?at=` 400 not 500) in `4b9d6a3`; R-2 read-only ledger on the usage route; `make ui-test` 915 passed. The old "uncommitted" state is superseded.
- **Unmet / open**: **AC3** `POST /api/schedules/{id}/runs/{run_id}/resume-capped`; **AC5** paused-run list + Resume button; **AC10 (M5)** generic dashboard resume refusing governed cap raises - the route-level 409 exists for `cost_cap_usd` (H-2) but the New-run/template start route skips `_check_cost_cap_option` (T-lTW88y I-7, Info); overshoot display needs the ledger to expose it per window. `capped_resume.py` is now committed, so the blocker is gone; the work is simply not done.

## Update 2026-11-11 (By: developer · Role: developer · unit `w26-03-a2-resume-capped-route`)
- Landed: `POST /api/schedules/{id}/runs/{run_id}/resume-capped` (AC3): body is the closed model `{add_usd: number|null}` (`extra=forbid`, `0 < add_usd <= MAX_USD`, no NaN/inf; validated by hand so a JSON `NaN` is a clean 422, not a 500). It reuses `capped_resume.request_capped_resume` (shared `check_resume`), writes a drop request with `requested_by: "ui"` and returns **202 = queued** (the daemon stays the only launcher; no in-process drain). `ResumeRefused` maps: `schedule_not_found` -> 404, `bad_amount` -> 422, everything else (`not_paused|expired|aborted|no_headroom|overlap`) -> 409, body `{detail, reason, remaining_usd?, window?}`. Per-resume bound = `max_add` (`per_run_usd * MAX_RESUME_FACTOR`, else headroom, else `MAX_USD`) - H-2/ADR-0025.
- Landed: `GET /api/schedules/{id}/paused-runs` (read-only store; spend vs cap, expiry, default/max add, `resumable`/`refusal`) and `ui/src/components/PausedRuns.tsx` mounted inside `ScheduleUsage` (RunDetail link, spend vs cap, expiry countdown + absolute, amount field defaulting to `default_add_usd`, Resume disabled with its reason, 409/422 `reason` shown inline via `ApiError.extra`). Bundle rebuilt.
- Landed (AC10/M5): the governed-resume 409 now names the schedule and the Usage-panel/`ao schedule resume` path; the template start route (`create_instance(start=True)`) now validates `options.cost_cap_usd` (T-lTW88y I-7). A brand-new run is never governed, so only the bound applies there; resume of a governed run was already refused (H-2).
- AC4: the POST inherits `AuthMiddleware` (no policy entry) and the global Origin/Content-Type gate; asserted in `tests/auth/test_schedule_resume_auth.py` and a cross-origin 403 test in `tests/ui/test_schedule_usage_api.py`. Not exercised: a real logged-in session POST (the auth harness proves deny only).
- Tests: `tests/ui` + `tests/auth` 3054 passed, 1 fail (the OpenAPI path-set pin, since fixed by adding the two new paths; re-run 14/14); new API tests 17 (`test_schedule_usage_api.py`) + 1 (`test_cost_cap_resume_guard_ltw88y.py`) + 1 auth; vitest 921 passed (6 new in `paused-runs.test.tsx`); `make ui-typecheck` clean; ruff/format/mypy clean on touched files.
- **Still unmet**: AC5 overshoot display (the ledger does not expose overshoot per window); AC6 schedule filter on the feed UI not verified here; AC7 "feed filters" vitest case not added; no browser/e2e of the Resume button against a live daemon; `T-88wSLC` API-level e2e and `T-lTW88y` security pass on this POST are still to run. The paused list shows only `cap_state: paused` records (expired-and-swept ones disappear).

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
