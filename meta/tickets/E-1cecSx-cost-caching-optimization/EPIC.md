# EPIC: E-1cecSx-cost-caching-optimization

## Metadata
- Epic ID: `E-1cecSx-cost-caching-optimization`
- Title: Cost & Caching Optimization (Epic B of the cost/perf/hooks/skills thread)
- Owner: `dev-epic` agent
- Created: 2026-09-21
- Last Updated: 2026-09-21
- Status: `Done`

## Summary
- Goal: (1) Audit and, if real, fix Claude prompt-caching leaks across per-task isolated
  worktrees; (2) surface run-level and within-task timing profiling on-demand; (3) add
  local outcome/accuracy metrics including a deterministic grading mechanism covering both
  dispatched and skipped tasks; (4) surface prompt-cache effectiveness in the dashboard,
  on-demand/expandable only.
- **Scope In (as-built, Rev 2 — see `docs-md/adr/ADR-0015-prompt-cache-scope-and-post-run-
  grading.md` for why this differs from the original plan)**: `executors/claude_cli.py`
  (opt-in argv flag + version-incompatibility detection), `models.py`/`specs/agents.schema.json`
  (additive `AgentSpec` field; `HookOutcome.kind` Literal widened), `hooks.py` (`run_hook`'s
  `kind` parameter Literal widened — the only change to that module), two new modules
  (`reporting.py`, `outcomes.py`), two new CLI commands (`ao report-timing`,
  `ao report-outcomes --grade`), dashboard backend (`ui/runs.py`) + frontend (`ui/src/`) wiring
  for on-demand cache/timing views, a generalized grading hook script
  (`specs/examples/hooks/grade_command.py`).
- **`engine.py`: ZERO changes** — the original plan's in-engine `settlement_hook` mechanism
  (one helper + two call sites) was rejected at early-gate architect review ("needs rework":
  wrong call site relative to final task status, isolated-task worktree staleness, main-thread
  blocking against `max_parallel`, unguarded registry lookup) and replaced with a post-run
  grading pass that needs no engine involvement at all.
- Scope Out: cross-user anonymized telemetry (future epic), LLM-based grading hooks (explicit
  non-goal per Epic A HLD §7's non-billable boundary), workflow-level default grading hooks,
  engine core/scheduler/breaker/monitoring changes, editing Epic A's `pre_hook`/`post_hook`
  call sites.

## Requirements
See `docs-md/cost-caching-optimization-hld.md` §5 for the full MVP/Non-MVP/Stretch traceability
table (Rev 2). Summary:
- FR-B1-1 / NFR-B1-1: prompt-cache audit + opt-in fix (`T-lue4Rz`) — Done
- FR-B2-1 / FR-B2-2: timing profiling, run-level + within-task (`T-J1b0FN`) — Done
- FR-B3-1 / FR-B3-2 / FR-B3-3: outcome/accuracy MVP, post-run grading (`T-Ar8HJF`) — Done
- FR-B4-1: dashboard cache visibility (`T-h1KdlK`) — Done

## Task List
- [x] `T-lue4Rz-prompt-caching-audit` — B1: audit + opt-in fix + tests — **Done**
- [x] `T-J1b0FN-timing-profiling` — B2: top-N slowest + activity breakdown (B2.2 delegated,
      landed `9eb4e12`) — **Done**
- [x] `T-Ar8HJF-outcome-accuracy-metrics` — B3: local counts + grading script + post-run
      settlement grading (redesigned per ADR-0015 decision 2, zero `engine.py` changes) —
      **Done**
- [x] `T-h1KdlK-dashboard-cache-visibility` — B4: backend + frontend (frontend delegated,
      landed `35cffaf`/`9edc02b`) — **Done**
- [x] `T-UJElTR-e2e-verification` — late-gate e2e demonstration + full suite + ruff/mypy —
      **Done**: 3968 passed/8 skipped/0 failed, ruff+mypy clean, real CLI demo captured

## Risks and Dependencies
- Depended on Epic A (`E-AMSSHX-task-lifecycle-hooks`), already landed on this branch —
  `post_hook` mechanism and `hooks.run_hook` reused unchanged (Literal widened only).
- The original plan's `engine.py` risk (two call sites in `_settle_completed_task`/
  `_prepare_and_maybe_dispatch`) was eliminated entirely by the ADR-0015 redesign — realized
  risk was materially lower than planned.
- B1's fix effectiveness against the LIVE Anthropic cache is not empirically verified end to
  end: the design doc honestly records a residual cache-scope gap (a separate branch/git-status
  determinant the flag does not address) that the audit could not close without further paid
  API experimentation, which was deliberately not run beyond one incidental $0.19 connectivity
  check. Documented as an open verification boundary, not asserted either way.

## Links
- Design doc: `docs-md/cost-caching-optimization-hld.md` (Rev 2)
- ADR: `docs-md/adr/ADR-0015-prompt-cache-scope-and-post-run-grading.md`
- Epic A (dependency): `meta/tickets/E-AMSSHX-task-lifecycle-hooks/`
- Output artifacts: `output/E-1cecSx-cost-caching-optimization/` (real CLI demo transcript +
  e2e test output)
- Demo script: `scripts/helper/epics/E-1cecSx/run_demo.sh`
