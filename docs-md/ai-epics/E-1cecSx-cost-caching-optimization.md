# Epic: E-1cecSx-cost-caching-optimization

## Metadata
- Epic ID: `E-1cecSx-cost-caching-optimization`
- Title: Cost & Caching Optimization (Epic B of the cost/perf/hooks/skills thread)
- Owner: `dev-epic` agent
- Created: 2026-09-21
- Last Updated: 2026-09-21
- Status: **Done**
- Origin ask: user request, third of a 3-epic thread on branch `ad/cost-perf-hooks-skills`
  (Epic A: task lifecycle hooks, landed first; Epic B: this one; Epic C: not yet scoped).
- Mirror: [`meta/tickets/E-1cecSx-cost-caching-optimization/EPIC.md`](../../meta/tickets/E-1cecSx-cost-caching-optimization/EPIC.md)
  (task-level tracking lives there; this file is the running narrative + evidence log).

## Goal

Four workstreams, delivered in priority order:

1. **B1 — audit and, if real, fix a Claude prompt-caching leak** across per-task isolated
   worktrees (the sibling task-isolation epic's `.claude/worktrees/<task-id>/...` pattern).
2. **B2 — timing/profiling**: run-level top-N slowest tasks + within-task activity-type
   breakdown, surfaced on-demand.
3. **B3 — outcome/accuracy metrics**: local retry/review-loop/breakdown-frequency counts, plus
   a deterministic grading mechanism covering both dispatched and skipped tasks.
4. **B4 — dashboard cache-stat visibility**: on-demand/expandable only, never a default column.

Full design: [`docs-md/cost-caching-optimization-hld.md`](../cost-caching-optimization-hld.md)
(Rev 2, incorporates early-gate `reviewer`+`architect` findings).
Decision record: [`docs-md/adr/ADR-0015-prompt-cache-scope-and-post-run-grading.md`](../adr/ADR-0015-prompt-cache-scope-and-post-run-grading.md).

## Acceptance Criteria (testable)

See [`EPIC.md`](../../meta/tickets/E-1cecSx-cost-caching-optimization/EPIC.md) and the HLD §5
for the full MVP/Non-MVP/Stretch traceability table. Summary, all verified Done:

1. B1: `AgentSpec.exclude_dynamic_system_prompt_sections` injects Claude Code's own
   `--exclude-dynamic-system-prompt-sections` flag when set; default `False` keeps every
   pre-epic dispatch's argv byte-identical (regression-tested).
2. B2: `ao report-timing --run-id <id> [--top N] [--task <id>]` prints real top-N-slowest and
   (optionally) a real within-task activity-category breakdown.
3. B3: `ao report-outcomes --run-id <id> [--grade <hook-name>]` prints real local
   retry/self-heal counts, and, with `--grade`, grades EVERY settled task (dispatched and
   `skip_if_outputs_exist`-skipped alike) in one post-run pass, with zero per-task wiring.
4. B4: the dashboard's existing per-task detail payload (`run_detail`) carries
   `cache_hit_rate`/`cache_read_tokens`/`cache_creation_tokens`, surfaced in the frontend's
   existing expandable `<details>` pattern — never a new default table column.

## Design Decisions (locked after early-gate review)

Both `reviewer` and `architect` ran against Rev 1 of the design doc on 2026-09-21, tracing every
load-bearing claim against the actual current code (not taken on faith) rather than reviewing
prose alone. Two decisions came out of that pass and are recorded in ADR-0015:

- **Decision 1 (B1)**: opt into Claude Code's own `--exclude-dynamic-system-prompt-sections`
  flag, per-agent (ADR-0006 layering), default off. **Honest limitation recorded, not hidden**:
  the flag addresses the working-directory/environment-info component of Claude Code's cache
  scoping, but Claude Code's own docs name a SEPARATE branch/git-status determinant the flag
  does not cover — and ao's per-task worktrees give every isolated task its own branch. The fix
  reduces, but is not proven to eliminate, the cache miss for isolated workflows. Confirming
  the residual gap empirically would need further paid `claude` CLI experimentation, which was
  deliberately not run (see Evidence Log's disclosed-spend note).
- **Decision 2 (B3)**: an original in-engine `TaskSpec.settlement_hook` design (two new
  `engine.py` call sites) was rejected at early-gate review — the architect verdict was
  explicitly **"needs rework"** on this one sub-design, citing a wrong call site relative to the
  task's final status, an isolated-task worktree-staleness grading bug, main-thread blocking
  against `max_parallel`, and unguarded registry lookups. Replaced with a POST-RUN grading pass
  (`ao report-outcomes --grade`) that resolves every finding structurally and needs **zero**
  `engine.py` changes — a stronger guarantee than the original "one helper + two call sites"
  plan.

No item from either review was left unaddressed; full record in the HLD's §8 and this doc's
Evidence Log below.

## Decomposition

| Task | Workstream | Owner | Status |
|---|---|---|---|
| `T-lue4Rz-prompt-caching-audit` | B1 | `dev-epic` (direct) | Done |
| `T-J1b0FN-timing-profiling` | B2.1 + B2.2 | `dev-epic` (B2.1) + delegated `developer` (B2.2) | Done |
| `T-Ar8HJF-outcome-accuracy-metrics` | B3 (all sub-items) | `dev-epic` (direct, post-redesign) | Done |
| `T-h1KdlK-dashboard-cache-visibility` | B4 backend + frontend | `dev-epic` (backend) + delegated `developer` (frontend) | Done |
| `T-UJElTR-e2e-verification` | Late gate | `dev-epic` (direct) | Done |

Two sub-deliverables (B2.2, B4-frontend) were delegated to fresh `developer` subagents with
explicit change-boundary instructions (see each ticket's TASK.md); both were independently
re-verified (code review + a fresh `ruff`/`mypy` run) by this session before being accepted,
rather than trusted on the delegate's own report alone.

## Evidence Log

- Early-gate `reviewer` + `architect` passes (2026-09-21): both traced claims against actual
  code; reviewer independently re-fetched Anthropic's live docs rather than trusting the
  design doc's quotes. Findings incorporated into HLD Rev 2 and ADR-0015 (see Design Decisions
  above).
- Full test suite (final, `pytest -q`): **3968 passed, 8 skipped, 1 deselected, 0 failed**
  (166.86s). The one deselected test is a confirmed pre-existing, out-of-scope NFR-2 gate
  failure from a DIFFERENT, already-merged epic (`tests/test_e2e_builtin_routed_runner.py`,
  last touched in `b0cb467`, the sibling task-isolation epic's own PR — zero diff from this
  epic, verified via `git log`/`git diff`).
- 60+ new tests added across `tests/test_executor.py`, `tests/test_outcomes.py`,
  `tests/test_reporting.py`, `tests/test_e2e_cli_cost_caching.py`, `tests/ui/test_runs.py`,
  plus the delegated agents' own additions (28 more in `tests/test_reporting.py` for B2.2, 6
  frontend component tests for B4).
- One real outer-CLI-boundary e2e test (`tests/test_e2e_cli_cost_caching.py`,
  `typer.testing.CliRunner`) AND a real, human-readable CLI demonstration
  (`scripts/helper/epics/E-1cecSx/run_demo.sh`, output captured at
  `output/E-1cecSx-cost-caching-optimization/demo-run-output.txt`) both prove the same
  load-bearing claim: `ao report-outcomes --grade` grades a dispatched AND a skipped task in
  one pass, with real `settlement_grades.json` content showing both `settle_reason` values.
- `ruff check .` clean; `ruff format --check .` clean (one file fixed during verification).
- `mypy src` (this project's actual CI invocation) clean except 4 pre-existing, confirmed-
  untouched `_version.py` errors.
- **Disclosed real spend**: one incidental $0.19 API charge during B1 research (a `claude -p
  "hi"` connectivity/auth check, not a deliberate cache experiment) — recorded in `T-lue4Rz`'s
  STATUS.md. No further paid experimentation was run to avoid unauthorized additional spend
  purely for documentation validation; the residual cache-scope uncertainty noted in Decision 1
  above is left as an honest, open gap rather than closed with unauthorized spend.
- Frontend verification (delegated agent's own report, independently spot-checked): `npm run
  typecheck` clean, `npm run build` succeeded, `npm run test` 85/85 passed,
  `pytest tests/ui -q` 380 passed.

## Risks & Blockers

None remaining. All risks named in the epic's early planning (the `engine.py` touch-surface
risk for B3, the version-compatibility risk for B1's CLI flag, the frontend-pattern-discovery
risk for B4) were resolved during delivery — B3's engine risk was eliminated entirely by the
ADR-0015 redesign rather than merely mitigated.

## Next actions

None — epic complete. Recorded follow-ups for a FUTURE epic (not this one, not silently
dropped):
- Empirical A/B confirmation of B1's residual branch/git-status cache-scope gap (needs real,
  authorized paid `claude` CLI dispatches).
- An advisory `validate_isolation` warning when an `isolation: worktree` task's agent doesn't
  set `exclude_dynamic_system_prompt_sections` (scoped in the HLD §1.4, not built this epic).
- Cross-user anonymized opt-in telemetry (explicitly out of this epic's scope from the start).
