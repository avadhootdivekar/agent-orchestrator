# EPIC: E-1cecSx-cost-caching-optimization

## Metadata
- Epic ID: `E-1cecSx-cost-caching-optimization`
- Title: Cost & Caching Optimization (Epic B of the cost/perf/hooks/skills thread)
- Owner: `dev-epic` agent
- Created: 2026-09-21
- Last Updated: 2026-09-21 (post early-gate redesign — see `docs-md/adr/
  ADR-0015-prompt-cache-scope-and-post-run-grading.md`)
- Status: `In Progress` (B1/B3 Done; B2/B4 backend Done, frontend/activity-breakdown delegated
  and in progress)

## Summary
- Goal: (1) Audit and, if real, fix Claude prompt-caching leaks across per-task isolated
  worktrees; (2) surface run-level and within-task timing profiling on-demand; (3) add
  local outcome/accuracy metrics including a deterministic grading post-hook (direct use of
  Epic A's hook mechanism) with a new post-settlement trigger point for skip/resume coverage;
  (4) surface prompt-cache effectiveness in the dashboard, on-demand/expandable only.
- Scope In: `executors/claude_cli.py`, `models.py` (additive fields), `spec.py` (one new
  cross-validate rule), `engine.py` (one new helper + two call sites), two new modules
  (`reporting.py`, `outcomes.py`), one new CLI report command group, dashboard backend+frontend
  wiring for on-demand cache/timing views, a generalized grading hook script, example workflow.
- Scope Out: cross-user anonymized telemetry (future epic), LLM-based grading hooks (explicit
  non-goal per Epic A HLD §7's non-billable boundary), workflow-level default hooks, engine
  core/scheduler/breaker/monitoring changes, editing Epic A's `pre_hook`/`post_hook` call sites.

## Requirements
See `docs-md/cost-caching-optimization-hld.md` §5 for the full MVP/Non-MVP/Stretch traceability
table. Summary:
- FR-B1-1 / NFR-B1-1: prompt-cache audit + opt-in fix (`T-lue4Rz`)
- FR-B2-1 / FR-B2-2: timing profiling, run-level + within-task (`T-J1b0FN`)
- FR-B3-1 / FR-B3-2 / FR-B3-3 / NFR-B3-1: outcome/accuracy MVP (`T-Ar8HJF`)
- FR-B4-1: dashboard cache visibility (`T-h1KdlK`)

## Task List
- [x] `T-lue4Rz-prompt-caching-audit` — B1: audit + opt-in fix + tests — **Done**
- [ ] `T-J1b0FN-timing-profiling` — B2: top-N slowest (Done) + activity breakdown (delegated,
      in progress)
- [x] `T-Ar8HJF-outcome-accuracy-metrics` — B3: local counts + grading script + post-run
      settlement grading (redesigned per ADR-0015 decision 2, zero `engine.py` changes) —
      **Done**
- [ ] `T-h1KdlK-dashboard-cache-visibility` — B4: backend (Done) + frontend (delegated, in
      progress)
- [ ] `T-UJElTR-e2e-verification` — late-gate e2e demonstration + full suite + ruff/mypy —
      blocked on the two in-progress tasks above

## Risks and Dependencies
- Depends on Epic A (`E-AMSSHX-task-lifecycle-hooks`), already landed on this branch — `post_hook`
  mechanism reused unchanged for FR-B3-2.
- `settlement_hook` (FR-B3-3) touches `engine.py`'s `_settle_completed_task` and
  `_prepare_and_maybe_dispatch` — both large, shared functions; kept to two narrow additive call
  sites per the design doc's change-scope boundary (§7).
- B1's fix effectiveness against the LIVE Anthropic cache cannot be verified with a real API call
  in this environment without spending real account budget purely for R&D; the audit instead
  rests on Anthropic's own primary documentation (quoted verbatim in the design doc) plus a code
  trace proving ao's own content is byte-stable. Recorded as a documented evidence boundary, not
  hidden.
- Dashboard frontend (`ui/src/`, a separate Vite/React tree) requires locating its existing
  expandable-detail pattern before adding to it — first delegated sub-step of `T-h1KdlK`.

## Links
- Design doc: `docs-md/cost-caching-optimization-hld.md`
- Epic A (dependency): `meta/tickets/E-AMSSHX-task-lifecycle-hooks/`
- Output artifacts: `output/E-1cecSx-cost-caching-optimization/` (created as evidence lands)
