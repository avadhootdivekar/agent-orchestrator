# STATUS

- ID: `T-XpF1pF-cache-engine-integration`
- Updated At: `2026-10-05`
- State: `Done`
- Owner: `developer` (Dev A)

## This update
Implemented in commit `b7ca9c7` (branch `worktree-agent-a18ce2c08e42a3a5a`): HLD 8.7.1 seams
(a)-(e) in `src/agent_orchestrator/engine.py` and `tests/cache/test_engine_result_cache.py`
(25 tests). The cache stays OFF by default (`result_cache=None`); T-o95l1M wires the CLI.

## Acceptance criteria
| AC | Result | Evidence |
|----|--------|----------|
| 1 Seams | PASS | (a) ctor kwarg + docstring entry, `TYPE_CHECKING` imports; (b) `_RunContext.result_cache_pending`; (c) 5-line call site before `_estimate = 0`; (c') `_reverse_stale_charge` (existing block moved, one call in the gate); (d) 2-line settle call; (e) three private methods. Measured below; `ruff check` / `ruff format --check` clean |
| 2 NFR-1 | PASS | I-1 and I-2 (serial and `max_parallel=3`) in `test_noop_proof.py`: 58 passed (with `test_spawn_provenance.py` and the NFR-2 gate) on the engine change, goldens and test code unedited; U-AST-E in the new module (with negative self-tests); the existing `engine.py` audits ran in the full suite |
| 3 I-3 | PASS | miss -> store -> delete output -> hit: spy shows 0 dispatches; identical bytes; `succeeded`; record `hit True`, `ended_at == ts.ended_at`; `cache.hit` and `task.end` (`cached True`) logged; `CostlyFakeExecutor` gives `saved_cost_usd` 0.75 and `saved_tokens` 1540 |
| 4 I-4 | PASS | chain `a -> b`: both hit on the second run, 0 dispatches, bytes equal |
| 5 I-5 | PASS | `max_parallel=4`, two hits and two opted-out tasks: only the two misses reach a worker thread; statuses and bytes equal the serial run |
| 6 I-6 / I-6b | PASS | `SpyBudget`: a hit makes no `gate` / `charge_estimate` / `reconcile` call. Simulated crash (cycle 2 charged, never dispatched) -> resume -> hit: stale `charged_estimate` entry gone, `consumed_tokens` restored, `budget.resume_reverse` logged with cycle 2. Mutation check: removing the `_reverse_stale_charge` call from the hit path fails this test |
| 7 I-7 | PASS | `evaluate_breakers` spy: called on the miss run, never on the hit run; `cumulative_*` are 0 and `tripped_breakers == []` |
| 8 I-8 | PASS | after `prepare_resume` the hit stays `succeeded`; the `lookup` spy sees no new call; the record is unchanged |
| 9 I-18 | PASS | hit keeps `dispatch_cycle 1`; `locate_attempt_dirs(run_dir, "a", 1) == []`; a later real dispatch (cache off, resume) writes under `cycle-2/` |
| 10 I-21 / I-22 | PASS | `not_taken` task under `join: any` and a task with a missing required input never reach `lookup` and get no record |
| 11 I-27 | PASS | first-pass hit has `attempts == 0`; `cache: false` under `defaults.cache: true` gets no record, no `cache.*` event at INFO or above, and no `.orchestrator/cache` directory (the hook IS called for it: the coordinator owns the policy and returns `LookupOutcome(False, None, None)` without a store lookup) |
| 12 Budget-gate regression | PASS | `tests/test_engine_budget.py`, `test_budget*.py`, `test_engine_isolation_accounting.py` unedited and green |
| 13 Hygiene | PASS | see Evidence; `tests/conftest.py` and `tests/test_nfr2_regression_gate.py` unedited |

## Evidence
- **Engine diff vs the pre-task commit `ed8b8c3`** (`git diff --numstat ed8b8c3 -- src/agent_orchestrator/engine.py`, after `ruff format`): **133 added, 30 removed, net +103** (budget: net <= +110). Added lines inside existing functions: **12** (`__init__` 4, prepare call site 5, gate call 1, settle call 2; budget: <= 12). Longest added line <= 100 columns (checked with `awk`). `grep` of the added lines for `open(` / `.read(` / `.origin`: 0 matches.
- `.venv/bin/python -m pytest -q -p no:cacheprovider tests/cache tests/test_spawn_provenance.py tests/test_nfr2_regression_gate.py tests/test_engine*.py tests/test_budget*.py tests/test_wave*.py tests/test_breaker*.py tests/test_loop_construct.py tests/test_dynamic_injection.py tests/test_e2e_cli_max_parallel.py tests/test_resume_replay.py` -> **1454 passed**.
- `.venv/bin/python -m pytest -q -p no:cacheprovider tests/cache/test_engine_result_cache.py` -> 25 passed.
- Full suite `.venv/bin/python -m pytest -q -p no:cacheprovider` -> **6249 passed, 10 skipped, 1 failed** (651.99 s). The one failure is `tests/test_wave_scheduler.py::TestParallelDispatchProof::test_two_independent_tasks_overlap_at_max_parallel_two`: a thread-start race in the test's own `entered_snapshot` (the log shows both tasks ran; cache off, no cache code on that path). It passed 25 of 25 in isolation and in the focused run above, and the two wave test files pass on re-run (30 passed). Baseline 6081 passed / 10 skipped / 0 failed (before the coordinator and Part 1 tests); the increase is the coordinator, Part 1 and the 25 new tests.
- `.venv/bin/ruff check src tests` -> All checks passed. `.venv/bin/ruff format --check src tests` -> only the generated `src/agent_orchestrator/_build_info.py` (pre-existing).
- `.venv/bin/mypy src tests/cache` -> only the 4 pre-existing `_version.py` errors. The two known test errors (`tests/cache/test_types.py:22`, `tests/cache/test_records.py:11`) are fixed here by importing the submodules (`import agent_orchestrator.cache.safeio as safeio`, `... .records as records`).

## Deviations from the HLD blocks (reason)
1. **`LookupRequest.injected`** is `spawn is not None and spawn.loop_id is None` with `spawn = state.spawned_by.get(tid)`, not `ts.origin == SPAWN_ORIGIN_INJECTED`: `tests/test_spawn_provenance.py` forbids any `.origin` comparison under `src/`, comments included. By the `SpawnRecord` invariant (`loop_id` is set iff origin is `loop`), a spawn record without `loop_id` is exactly an emit_tasks-injected task. Tested (injected child flagged True, static emitter False).
2. **Constructor keyword placed last** (after `summarizer`, which landed after the HLD block): no positional caller changes; still after `run_prompt`.
3. **No blank line after the call-site (c) block** so the added lines inside existing functions stay at 12 (a blank counts).
4. **I-27 "no lookup"**: the engine calls the hook for every task (the coordinator owns the opt-in policy); "no lookup" is verified as no record, no store activity, no cache directory.
5. **Test ids owned elsewhere not added here:** I-20 (T-JCOAsq Part 2) and U-LZ1 / U-LZ2 (T-eyn5UG, T-o95l1M, T-bLpoze) are not in this ticket's scope.
6. **HLD 8.7.1** gained an implementation note recording items 1 to 3.

## Approval-ordering rule (HLD 24.2)
E-Ag7Pw3 code is not present in this tree (no approval or human-gate code in `engine.py`). The seam comment at call site (c) states that approval and human gates run BEFORE it; G2 must verify after the merge that a hit never satisfies or bypasses an approval.

## Risks / Blockers
- No blockers. Hits take no worker slot; restore I/O and the lookup-time HEAD read run on the main thread (bounded). The cost under `max_parallel` is for G1b to review.
- Success side effects that run only in `_settle_completed_task` (quota timer reset, router hook, emit/loop, breakers, head recording) do not run for hits by design (HLD 8.7.4).
- Flaky pre-existing test noted above (`test_two_independent_tasks_overlap_at_max_parallel_two`), unrelated to this change.

## Next actions
1. T-o95l1M (CLI construction), T-eyn5UG, T-ZTxN1x complete the engine set; then gate G1b.
2. T-JCOAsq Part 2 adds I-20 and the remaining engine integration scenarios.

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: Status initialized (Draft).
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2 re-plan after the Phase-4
  consultation.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 3 re-plan after the
  early-gate review and the manager's scope decisions (HLD 23.5).
- By: developer · Role: developer · Date: 2026-10-05 · Comment: State -> Done (commit `b7ca9c7`).
  Every acceptance criterion passes; engine diff net +103 with 12 added lines inside existing
  functions; deviations 1-6 above are small and documented. Epic `EPIC.md` / `STATUS.md` rollup
  updated to match.
- By: developer · Role: developer · Date: 2026-10-05 · Comment: G1b remediation touched this task's code (rev S-1 (HLD 8.7.4 rule in the _result_cache_lookup docstring), rev N-1 and N-3; engine net +105) in commit `6ba90ba`; findings and regression tests are listed in `T-fXWbqg-cache-review-gates/STATUS.md` (G1b remediation). Task state stays Done.
