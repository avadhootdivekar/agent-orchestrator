# STATUS

- ID: `T-jI3P4p-unit-integration-tests`
- Updated At: 2026-09-21
- State: Done
- Owner: tester (delegated by dev-epic), bug-fixed by dev-epic

## This update
- By: dev-epic
- Role: manager
- Date: 2026-09-21
- Comment: Ticket drafted with acceptance criteria that specifically defend the epic's two
  hardest requirements: the FR-4 no-op proof (AC-5) and the Epic B forward-compat contract
  (AC-7).

## Evidence
- (pending implementation)

## Risks / Blockers
- None yet.

## Next actions
1. Delegate to `tester` once T-AHvmYR/T-lzQEyy/T-DgheoA/T-fbQIFX land.

## Rev 2 sync (post early-gate review)
- By: dev-epic
- Role: manager
- Date: 2026-09-21
- Comment: Acceptance criteria updated to match HLD Rev 2 (hooks moved to a `WorkflowSpec.hooks`
  named registry referenced by `HookRef`, T2 resolver-dispatch suppression added, `hooks.py`
  module extraction, error-detail folding for self-heal). See
  `meta/tickets/E-AMSSHX-task-lifecycle-hooks/STATUS.md` for the full review outcome.

## Update — tester delivered, dev-epic found and fixed 4 test bugs before accepting
- By: dev-epic
- Role: manager
- Date: 2026-09-21
- Comment: `tester` delivered 4 test files (`test_hooks.py` 32 tests, `test_engine_hooks.py` 15
  tests, `test_engine_hooks_resolver.py` 4 tests, `test_hooks_schema_validation.py` 14 tests =
  65 new tests) plus a benchmark script — commits `d97d76b`/`e203a78`. **Its own final report
  explicitly flagged `test_engine_hooks.py` as "pending full run"** and never gave the required
  full-suite pass/fail count (AC-15) — a red flag I did not wave through. Ran it myself: it hung
  past 120s. Root-caused 4 concrete bugs (not design issues — see commit `a9e8791` for full
  detail): (1) a quota-exhaustion test with no `sleeper=`/`clock=` override that would wait on
  the engine's REAL 15-minute poll / 6-hour max-wait, the actual cause of the hang; (2)/(3) two
  AC-13 tests referencing a nonexistent `TaskRunState.result` attribute; (4) an AC-4 cancelled
  test whose `cancel_fn=lambda: True` cancelled the whole run before the task ever dispatched,
  never exercising the code path it claimed to test. Fixed all 4 directly (commit `a9e8791`),
  re-ran: all 15 tests in the file now genuinely pass. Independently re-verified the other 3
  files' 50 tests too (not just trusting the tester's count): `pytest -q tests/test_hooks.py
  tests/test_engine_hooks_resolver.py tests/test_hooks_schema_validation.py` -> 50 passed,
  1.17s — genuine, no issues found there. Read `test_engine_hooks_resolver.py` in full: AC-12's
  T2/T3 suppression proof is a direct, precise unit test of
  `build_resolver_dispatch`/`build_rerun_task` (the exact functions the production fix touched)
  — accepted as sufficient for MVP; a full end-to-end git-conflict-through-`Orchestrator.run()`
  version would add marginal confidence at real cost/flakiness risk, a deliberate scoping call
  made now rather than bounced back for more rework.

## Evidence (final)
- Full suite: `pytest -q -m "not real_llm and not swebench"` -> **3897 passed, 1 failed
  (pre-existing, confirmed unrelated), 1 skipped, 7 deselected** — zero regressions.
- `mypy src/agent_orchestrator/` (the actual CI gate, `.github/workflows/ci.yml` runs `mypy
  src`) — clean except 4 pre-existing `_version.py` errors, none in any file this epic touched.
  (Whole-repo `mypy .` / `mypy tests/` has pre-existing, out-of-CI-scope errors unrelated to
  this epic, plus a handful of minor type-narrowing nits in the new test files themselves —
  noted for T-6gR2ya's optional cleanup pass, not blocking since `tests/` is not this repo's
  mypy CI gate.)
- `ruff check .` / `ruff format --check .` — clean.
- Benchmark: `scripts/helper/epics/E-AMSSHX/benchmark_hooks_overhead.py` (illustrative only,
  not a pytest gate, per CLAUDE.md determinism rule).
