# TASK: T-8knqVe-dashboard-usage-panel

## Metadata
- Task ID: `T-8knqVe-dashboard-usage-panel`
- Epic ID: `E-Qp4Zm8-scheduled-run-governance`
- Owner: developer agent (frontend + API)
- Created: 2026-11-10
- Last Updated: 2026-11-10
- Status: Partial (updated 2026-11-11, w26-03; see STATUS.md)
- Estimate: 3 days

## Requirements Mapping
- Requirement IDs: FR-5, FR-6 (see `../EPIC.md`); owner decision D-3 ("record on dashboard")

## Description
Three API routes and a usage sub-panel in E-Sc9Rt4's Schedules panel. The panel shows meters per window, a paused-runs
list with a bounded *Resume* action, and the governance event feed. This is where D-3's "record on dashboard" becomes
visible.

Read HLD §14.2, §14.3 and §13. Read the **merged** code of E-Sc9Rt4 `T-Ap1Xs3` (typed exception hierarchy,
`Schedules.tsx`, routes), `T-rwj4nV` (`build_usage_doc`), `T-nI4DBf` (`request_capped_resume`, `ResumeRefused`),
`ui/README.md` conventions, and `auth/policy.py` (route policies; ADR-0021).

Files you own:
- `src/agent_orchestrator/ui/service.py` (edit, additive): `schedule_usage(id, at)`, `governance_events(limit, id)`,
  `request_capped_resume(id, run_id, add_usd)`; map `ResumeRefused` to a typed dashboard error.
- `src/agent_orchestrator/ui/app.py` (edit): 3 routes, each ≤ 5 lines.
- `ui/src/components/ScheduleUsage.tsx` (new), `ui/src/components/Schedules.tsx` (edit: mount the sub-row + feed),
  `ui/src/api.ts`, `ui/src/types.ts` (additive), `ui/src/test/schedule-usage.test.tsx` (new).
- `tests/ui/test_schedule_usage_api.py` (new).
- `src/agent_orchestrator/ui/static/**` (the committed `make ui-build` output).

Do NOT touch: `auth/` (new routes inherit `AUTHENTICATED` by having no policy entry. Do **not** add a public
policy), `service/` (read-only), `schedules/` (read-only), `engine.py`, other UI components.

## Acceptance Criteria
1. `GET /api/schedules/{id}/usage?at=` returns the `ao.schedule.usage/v1` document for one schedule (same builder as
   the CLI). `404` for an unknown id; `400` for a malformed `at`.
2. `GET /api/schedules/governance-events?limit=50&id=` returns the newest-first `schedule.governance.*` events
   (`limit` clamped to 1..500), never including raw prompt text (sha prefix only).
3. `POST /api/schedules/{id}/runs/{run_id}/resume-capped` with body `{"add_usd": number|null}` ⇒ `202` on accept;
   `409` with `{reason}` for `not_paused|expired|aborted|no_headroom|overlap`; `422` for a non-positive, non-finite or
   over-`max_add` amount; `404` for an unknown schedule/run. The body accepts **only** `add_usd`, and unknown keys
   ⇒ 422 (no workflow, argv or absolute cap may be passed).
4. With auth configured (ADR-0021), all three routes require a session (`401` unauthenticated, via the existing
   `AuthMiddleware` test harness), and the POST enforces the same Origin/Content-Type gate the sentinel routes use.
   Without auth, the behavior is as ADR-0010 D7.
5. `ScheduleUsage.tsx`: one meter per window showing runs `used/max` and spend `actual (+reserved) / max` as numbers,
   not colour alone. The reset time is shown relative + absolute. ⚠ marks `partial`/`unknown` confidence, and overshoot
   is shown when > 0. The paused list shows a `RunDetail` link, spend vs cap, an expiry countdown, and an amount field
   (default `per_run_usd`) + Resume button that is disabled with the refusal reason when not resumable.
6. The feed lists the last 50 governance events with a schedule filter. Polling reuses the panel's `POLL_MS`.
7. vitest (jsdom, mocked `fetch`): meters render numbers; the disabled resume shows its reason; a 409 response
   surfaces the reason inline; the feed filters.
8. `make ui-build` output is committed. The API tests use `TestClient` with `StubSupervisor`/temp state dir.
9. ruff/format/mypy clean; existing UI + API tests green.
10. **(M5) Generic resume refuses governed cap raises.** The generic dashboard resume/New-run routes return **409**
    ("governed by schedule `<id>`; use Resume on the Usage panel / `ao schedule resume`") when the request carries
    `options.cost_cap_usd` for a run where `is_governed_run(...)` is true (T-nI4DBf AC12), so `resume-capped`'s headroom
    check cannot be bypassed from the UI. Non-governed runs are unaffected. Test both.

## Risks
- The Resume button spends money from a possibly unauthenticated dashboard. AC3's closed body + server-side bounds
  and AC4 keep it no wider than `POST /api/runs` already is (HLD §14.2).

## Dependencies
- Upstream: E-Sc9Rt4 `T-Ap1Xs3` (merged); `T-rwj4nV` (`build_usage_doc`); `T-nI4DBf`; `T-Uz0Uol`.
- Downstream: `T-88wSLC` (API-level e2e), `T-lTW88y` (security pass on the POST).

## Pseudocode / Algorithm
```text
@app.post("/api/schedules/{sid}/runs/{rid}/resume-capped", status_code=202)
def resume_capped(sid, rid, body: ResumeCappedBody):   # extra="forbid"
    return service.request_capped_resume(sid, rid, body.add_usd)   # typed errors -> 404/409/422
```

## Schemas / Interface Notes
- Interface / HTTP: HLD §14.2 table.
- Spec / data schema: `ao.schedule.usage/v1` (shared with the CLI); `ResumeCappedBody{add_usd: float|null}`.
- Triggers / events: reads `events.jsonl`.
- Artifacts: `ui/static/**`.

## Handoff Boundary
- Upstream: CLI builder, capped resume, E-Sc9Rt4 panel.
- Downstream: security review of the POST (`T-lTW88y`).

## Artifacts
- Docs/comments: `meta/tickets/E-Qp4Zm8-scheduled-run-governance/T-8knqVe-dashboard-usage-panel/`
- Large outputs: N/A
