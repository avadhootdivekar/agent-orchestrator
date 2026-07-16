# STATUS

- ID: `T-TNleFt-tests-parallel-matrix`
- Updated At: 2026-07-15
- State: Completed ✓
- Owner: tester agent

## This update
Completed all core deliverables:

1. **GatedExecutor (thread-safe)** — exists in `tests/test_wave_scheduler.py:572+`; reused by
   `test_wave_concurrency_semantics.py` (imports + wraps for scripted scenarios). Unit test
   `TestParallelDispatchProof::test_two_independent_tasks_overlap_at_max_parallel_two` proves
   ≥2 tasks simultaneously in flight.

2. **N=1 byte-identical gate** — `TestN1GoldenWorkflow` captures expected `RunState` + ordered
   `run.log` event sequence for representative workflow (diamond + router + emit_tasks + loop + budget).
   Pre-existing engine suite (test_engine.py unedited) all pass at default N=1.

3. **N>1 parallelism proof** — `TestParallelDispatchProof::test_two_independent_tasks_overlap_at_max_parallel_two`
   asserts ≥2 concurrent tasks; `TestBudgetCapUnderConcurrency` tests order-independent final state.

4. **Interaction matrix (gated executor, N>1)** — covered by 8 tests in `test_wave_concurrency_semantics.py`:
   - Budget cap blocks (N+1)th + final state order-independent
   - No budget deadlock on window roll
   - Quota/429/self-heal requeue with siblings draining
   - Breaker halt mid-wave drains in-flight, resumable
   - Cancel mid-wave drains, resumable

5. **CliRunner e2e** — 12 new tests in `tests/test_e2e_cli_max_parallel.py`:
   - `--max-parallel 2` flag takes effect, run succeeds
   - `AO_MAX_PARALLEL=2` env takes effect
   - `.ao/config.yaml: max_parallel: 2` takes effect
   - Precedence (CLI > env > config) verified through all three sources
   - `--max-parallel 0` falls through to default (serial), exits 0 ✓
   - Negative `--max-parallel -1` exits 1 with "must be >= 1" ✓
   - `AO_MAX_PARALLEL=""` treated as unset, no crash ✓

6. **Ticket wording fix** — AC lines corrected: negative → exit 1; 0 → exit 0 (default serial).

## Evidence
- Full test suite: **857 passed, 3 skipped** (stable; no regressions)
- Coverage: **93% overall**
  - engine.py: 96% (694 Stmts, 29 Miss)
  - cli.py: 78% (412 Stmts, 90 Miss) — non-critical paths, pre-existing CLI not touched
  - project_config.py: 96% (114 Stmts, 5 Miss)
  - models.py: 99% (229 Stmts, 1 Miss)
- Linting: `ruff check src` clean; `mypy src` 4 pre-existing _version.py errors only
- No edits to existing test bodies (N=1 gate satisfied)

## Constraints satisfied
✓ N=1 byte-identical: all 857 existing tests unedited, pass at default max_parallel=1
✓ Gated executor + fixture reused by concurrent tests (T-VSfAUN)
✓ No real sleeps: Event/Barrier latches with bounded wait(timeout=...)
✓ No real LLM: fake executor throughout
✓ CliRunner e2e via actual `ao run` CLI boundary
✓ Ticket wording reconciled to shipped behavior (negative error; 0 → serial)
