# TASK: T-pIZq3q-dashboard-approvals-frontend

## Metadata
- Task ID: `T-pIZq3q-dashboard-approvals-frontend`
- Epic ID: `E-Ag7Pw3-human-approval-gates`
- Owner: developer
- Created: 2026-10-04
- Last Updated: 2026-10-05
- Status: Draft
- Estimate: 3 days (Gate 1 added the approval-wait stat and its vitest; at the cap)

## Requirements Mapping
- Requirement IDs: FR-10, FR-12 (wait display), NFR-7
- Design: HLD §9.15 (files, state/polling, a11y, vitest, bundle), §9.14.3 (refusal reasons), §9.2.5 (wait
  semantics), §7.4 TM-9/13/14, §26 rows 33–34; Gate 1 R-07, R-08

## Description
Implement the UI of HLD §9.15.1 (and nothing more): `src/approvals.ts`, `components/PendingApprovals.tsx`,
`components/ApprovalReview.tsx`, `tabs/ApprovalTab.tsx`; changes to `types.ts` (incl.
`RunSummary.approval_wait_seconds`), `api.ts` (5 calls, `ApiError.reason` and `ApiError.detailCode`),
`format.ts` (`attention` tone, `‖` glyph for `awaiting_approval`), `styles.css` (`.chip.attention` with
`--status-warning`), `graph/TaskNode.tsx` (tone token), `graph/Legend.tsx` (status order),
`tabs/model.ts` + `tabs/TabView.tsx` (`approval` kind: `{run, id}` required, derived title),
`components/RunsList.tsx` (panel + per-run chip), `components/RunDetail.tsx` (Review link + the
**"Approval wait"** stat next to the wall-time stat, shown only when `approval_wait_seconds > 0`, rendered
from the server value — Gate 1 R-07). No change to `App.tsx`, no new node shape, no sidebar badge.

Decision flow (§9.15.2): echo the hashes of the bytes the human viewed (bound artifact endpoint), fall back
to `review_now` for unopened artifacts; "changed since you viewed it" disables Approve; two-step confirm;
Reject requires a reason; banners chosen by the error's `reason` (never by message text): 409
`stale_hashes` → re-review banner + refetch + clear viewed; 409 `request_mismatch` / `not_pending` /
`already_decided` → banner + refetch; 403 → the server's message (for `anonymous`: the exact CLI command);
`can_decide == false` shows the reason as text. The pending panel and the review view show a live "waiting
since" computed client-side from `created_at`. Markdown only via `MarkdownView`; HTML/SVG review artifacts
as source (`CodeView`).

Then rebuild and commit the bundle: `npm run typecheck && npx vitest run && npm run build` (stage
`src/agent_orchestrator/ui/static/` explicitly, including deleted hashed assets).

Files — new: 4 source files + 7 vitest files (§9.15.4, incl. `run-approval-wait.test.tsx`). Changed: the
10 files listed above + the bundle. **Exclusive files during stage E** (HLD §22.3): `ui/src/**` files of
§9.15.1 and `src/agent_orchestrator/ui/static/**`.

## Acceptance Criteria
1. All vitest cases of HLD §9.15.4 exist in new test files and pass; the existing 406 tests pass unedited
   (or any edit is justified in STATUS).
2. `npm run typecheck` clean; `npm run build` succeeds; the rebuilt bundle is committed by the manager with
   the sources (no stale `static/` files).
3. A message containing `<script>alert(1)</script>` and `<img src=x onerror=alert(1)>` renders inert
   (no script element, no `onerror` attribute in the DOM).
4. The POST body contains `request_id` and, per path, the hash of the bytes the user viewed (test with one
   viewed and one unviewed artifact).
5. Approve requires two clicks (no request after the first); focus moves to "Confirm approve".
6. With `principal: null` the buttons are disabled and the exact `ao approve <run> <task>` command is shown.
7. 409 `stale_hashes` shows a banner, refetches and clears viewed hashes; banners are selected by `reason`
   (a test changes the message text and the same banner still appears).
8. `approval` tab kind rejects missing/invalid `run`/`id` and control characters; the title is derived.
9. `run-approval-wait.test.tsx`: `RunDetail` shows "Approval wait" formatted with `formatDuration` when
   `approval_wait_seconds > 0`, hides it at 0, and renders the given value (never recomputes it).
10. Optional (recommended): a screenshot of the review view under `output/E-Ag7Pw3-human-approval-gates/`.
11. `reviewer`-agent review recorded in STATUS.md.

## Risks
- Bundle merge conflicts with the sibling epics (CE-3: rebuild after the merge).

## Dependencies
- `T-l43hCg` (routes and payload shapes); `T-ZPGoSN` for live `approval_wait_seconds` values (the vitest
  uses fixtures, so no blocking dependency).

## Pseudocode / Algorithm
```text
echoReview(view, viewed) = { r.path: viewed[r.path] ?? r.sha256 for r in view.review_now }
approveDisabled = !view.can_decide || any(viewed[p] && viewed[p] !== now[p]) || anyState(now) !== "ok"
banner = BANNERS[error.reason] ?? genericBanner(error.detail)
```

## Schemas / Interface Notes
- API payloads: HLD §9.14.2–§9.14.4; refusal reasons §9.14.3.

## Verification
```
WT=/usr/avadhoot/mounted/agent-orchestrator/.claude/worktrees/agent-a513b4d78ac3b20bb
cd $WT/ui && npx vitest run && npm run typecheck && npm run build
```

## Handoff Boundary
- Upstream: `T-l43hCg`.
- Downstream: `T-pdLR96`.

## Artifacts
- Code and bundle as listed; optional screenshots in `output/E-Ag7Pw3-human-approval-gates/`.
