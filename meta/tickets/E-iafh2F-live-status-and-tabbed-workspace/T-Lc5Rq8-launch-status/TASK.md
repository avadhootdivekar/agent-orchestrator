# TASK: T-Lc5Rq8-launch-status

## Metadata
- Task ID: `T-Lc5Rq8-launch-status`
- Epic ID: `E-iafh2F-live-status-and-tabbed-workspace`
- Owner: developer
- Created: 2026-10-03
- Last Updated: 2026-10-03
- Status: Done
- Estimate: < 3 days

## Requirements Mapping
- Requirement IDs: FR-12 (launch outcome visible, never auto-redirect), FR-13 (pre-spawn validation, root cause), NFR-1 (path safety, ADR-0011), NFR-2 (fixed clocks)

## Description
A dashboard launch whose engine dies immediately (real case: `Unknown repo_set: ai-models`) left
no run dir, so the failure was invisible and the UI redirected to the run list. Replace the
redirect with an explicit launch-result panel (classic and template flows), derive and expose a
launch status + bounded log tail, surface recent failed-to-start launches on the runs list, and
reject a bad repo_set/agents/reposets reference before spawning.

## Acceptance Criteria
1. No auto-navigation: `onLaunched` fires only from explicit buttons; panel states starting / started / not-confirmed / failed as in HLD Phase 3.
2. Backend additive fields `status`, `log_tail`, `log_truncated`; `GET /api/launches/{id}`; `status`/`since_hours` filters; `_discover_run_id` returns when the child exits; log tail bounded and path-safe.
3. Preflight via engine loaders + `cross_validate`: 400 `Unknown repo_set X; available: ...`; template `repo_set` param offered as enum.
4. pytest (fake engines: exits 1 / stays alive / creates run dir; traversal and garbled records; tail cap; HTTP failure path), vitest per panel state, UI coverage >= 95% on touched files.
5. Gates green; bundle rebuilt and committed; screenshots in output/E-iafh2F-live-status-and-tabbed-workspace/; ui/README, HLD Phase 3, ADR-0018 addendum.

## Handoff Boundary
- Upstream: T-6UNaea-docs-and-closeout (epic closed 2026-10-02; reopened for this follow-up). Downstream: none.
