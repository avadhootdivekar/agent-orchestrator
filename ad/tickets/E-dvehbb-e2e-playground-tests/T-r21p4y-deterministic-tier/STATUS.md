# STATUS

- ID: `T-r21p4y-deterministic-tier`
- Updated At: 2026-07-01
- State: Done
- Owner: tester

## This update
- Implemented deterministic tier: `tests/playground/test_sum_of_array_deterministic.py` with 6 test classes (one per area).
- All AC met: 1-round and 2-round runs succeed with correct DAG order, token math recomputes exactly, dynamic injection/loop work, logging has correct structure, per-task output captured, CLI flags exercised, determinism verified (2 runs yield identical results).
- Pre-seeding uses existing `seed_control_files()` from harness with support for 1 and 2 rounds.

## Evidence
- `tests/playground/test_sum_of_array_deterministic.py`:
  - Area 1 (DAG): Exit 0, outputs exist, task order matches expected, cyclic workflow fails
  - Area 2 (Token): Consumed tokens == recomputed estimate (15200), budget charge/reconcile events present
  - Area 3 (Dynamic injection): Injected tasks have origin="injected", loop clones have origin="loop", 2-round order correct
  - Area 4 (Logging): run.log exists, all lines valid JSON with required fields, event set includes run.start/task.start/task.end/task.injected/run.end, task events carry task_id
  - Area 5 (Output capture): stdout.txt/stderr.txt exist for all 10 executed tasks (1-round), state.json has output_artifact_path set
  - Area 6 (CLI): validate OK/fail, status prints table, determinism test (2 runs = identical results)

## Test Results
- All 6 area tests pass with FakeExecutor
- Budget math verified: 15200 tokens (consistent with estimator formula)
- Determinism verified: two identical runs produce same task order, consumed tokens, status
- No src/ changes (test-only)

## Risks / Blockers
- Token estimate uses hardcoded task list; if task ids change, estimate must be recomputed
- Gate verdict path correctly uses `__iterN` suffix for iterations > 1 (verified via conftest pre-seeding)
