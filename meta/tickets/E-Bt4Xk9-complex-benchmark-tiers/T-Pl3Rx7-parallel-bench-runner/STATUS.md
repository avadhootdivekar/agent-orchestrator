# STATUS

- ID: `T-Pl3Rx7-parallel-bench-runner`
- Updated At: 2026-07-22
- State: **Done**
- Owner: developer agent

## This update
- By: developer agent
- Role: developer
- Date: 2026-07-22
- Comment: Implemented bounded task-level parallelism in `run_suite` per TASK.md and
  ADR-0009 D4: `run_suite(..., max_parallel: int = 1)`. `max_parallel<=1` walks
  `tasks_to_consider` in the exact pre-existing plain `for` loop (now calling a nested
  `_run_one(task)` closure, but with identical order/log-events/persist-cadence --
  byte-identical, proven by the full pre-existing `test_runner.py`/`test_budget.py`
  suites passing UNEDITED). `max_parallel>1` dispatches the same `_run_one` across a
  `ThreadPoolExecutor(max_workers=max_parallel)` via `list(executor.map(...))`. ONE
  `threading.Lock` (`_run_lock`) guards everything the T-Bg2Wq4 `LOCK-IN POINT`
  comments flagged: the resume/budget dispatch decision (`_dispatch_or_skip`, which
  also does the `skipped_budget` record + persist atomically with the check that
  produced it), the `tasks_dict` write-back, the `running_cost +=` accumulation, and
  every `_persist_record` call -- confined to two `with _run_lock:` blocks per task,
  never held across `materialize_workspace`/`Subject.run`/`Grader.grade` (the
  long-running, per-task-isolated work happens entirely outside the lock, so
  parallelism is not defeated). Persisted `run.json` `tasks` stays id-sorted
  regardless of completion order (`_build_record`, unchanged). `max_parallel` is
  excluded from `config_fingerprint`/`overrides` (control-flow, same rationale as
  `cost_budget_usd`) and `bench.run.start` gains a `max_parallel` field (Schemas/
  Interface Notes). **Scope deviation (orchestrator-authoritative over TASK.md):**
  `cli.py` was NOT touched -- `--max-parallel` CLI wiring is deferred to T-Cm9Tb4; see
  "Forward notes for T-Cm9Tb4" below for the exact wiring it must add.

## Evidence (AC-by-AC)
1. **AC1 (max_parallel=1 byte-identical to serial)**: the full pre-existing
   `tests/bench/test_runner.py` (23 tests) and `tests/bench/test_budget.py` (8
   original tests) pass **completely unedited** -- including
   `test_run_suite_persists_after_each_task_not_batched` (asserts only t1 is on disk
   when t2's `Subject.run` is invoked -- proves no batching/reordering was
   introduced) and `test_run_suite_executes_tasks_in_sorted_id_order` (exact call
   order). `bench.run.start` gaining a `max_parallel` field does not break any
   existing assertion (none of them assert the exact key set of that event's extra
   dict, only specific fields via `getattr`).
2. **AC2 (8 fake tasks, max_parallel=4, all recorded exactly once, id-sorted, no
   corruption)**:
   `tests/bench/test_parallel_runner.py::test_parallel_bounded_concurrency_and_result_parity_with_serial`
   (a `threading.Barrier(4)` deterministically forces genuine `max_parallel`-wide
   overlap -- no sleep-as-synchronization -- and proves the high-water mark is
   exactly 4; asserts id-sorted `run.json`, all 8 tasks present, and per-task results
   identical to a serial run of the same suite modulo timing/workspace) and
   `test_parallel_repeated_runs_never_corrupt_or_lose_a_task_record` (5 repeated
   fresh runs, each asserted id-sorted with all tasks `succeeded`).
3. **AC3 (budget + max_parallel=4 bounded overshoot)**:
   `tests/bench/test_budget.py::TestBudgetUnderConcurrency::
   test_overshoot_bounded_by_max_parallel_in_flight_tasks` -- 8 tasks x $3, cap=$1,
   `max_parallel=4`; a `Barrier(4)` forces t1-t4 all past the dispatch check before
   any returns, so all 4 run for real (total cost exactly $12) while t5-t8 are
   `skipped_budget`; asserts the exact documented bound `cap <= total_cost_usd < cap +
   max_parallel*max_task_cost` (`1 <= 12 < 13`). Companion
   `test_resume_under_concurrency_reattempts_all_skipped_budget_tasks` proves resume +
   D4 combine correctly (cap=$0 skips everything deterministically regardless of
   dispatch order; raising the cap re-runs and succeeds all, same
   `config_fingerprint`).
4. **AC4 (every intermediate persist is valid JSON under concurrency)**:
   `test_parallel_runner.py::test_parallel_every_intermediate_persist_snapshot_is_valid_json`
   wraps `runner._persist_record`, reads back what actually landed on disk after
   every call (not just the in-memory record), and validates each of the 8 snapshots
   both as JSON (`json.loads`) and against the `BenchRunRecord` schema
   (`model_validate`) -- proves the write-temp+atomic-rename-under-the-lock discipline
   holds under real thread contention, not just serially.
5. **AC5 (`max_parallel` excluded from `config_fingerprint`)**:
   `test_max_parallel_excluded_from_config_fingerprint` (same suite/subject,
   `max_parallel=1` vs `4` -> identical fingerprint) and
   `test_max_parallel_does_not_block_resume_with_different_worker_count` (resume path
   specifically: `max_parallel=1` then a same-day resume at `max_parallel=4` does NOT
   raise the W4 `BenchError`).
6. **AC6 (determinism caveat: completed-result-set independent of worker count for a
   non-budget-capped run)**: covered by AC2's parity assertion
   (`_comparable(parallel.tasks) == _comparable(serial.tasks)` over every
   timing-independent field). The *budget*-capped case's documented
   which-task-gets-skipped nondeterminism is exercised (not equality-asserted, since
   it IS nondeterministic by design) by AC3's barrier test, which pins the outcome via
   the barrier rather than leaving it to chance.
- **Error isolation under parallelism** (Risks section, not a numbered AC but an
  explicit testing requirement):
  `test_parallel_one_task_raising_does_not_kill_the_pool` (`SubjectError` mid-pool)
  and `test_parallel_unexpected_exception_isolated_run_continues` (a bare
  `ValueError`) -- both prove one task's failure is recorded (`subject_status="error"`)
  and every other task in the same `max_parallel=4` pool still completes.
- **Timing sanity** (verification requirement, not a numbered AC):
  `test_parallel_wall_clock_speedup_over_serial` (`@pytest.mark.perf`), 6 tasks x
  0.2s scripted delay: measured **serial=1.290s, parallel(4)=0.506s, ratio=2.55x**
  in isolation (>=2.5x, real concurrency proven). Best-of-3 retry added after this
  flaked once (ratio dipped under 2.5x) under full-suite CPU contention -- a genuine
  host-noise-sensitivity in a wall-clock-based test, not a correctness bug (a broken
  `max_parallel` would fail EVERY attempt at ~1.0x, retries do not mask an actual
  regression). Marked `@pytest.mark.perf` per the project's own registered marker
  ("performance envelope checks").

### Commands run (actual output)
- `uv run pytest tests/bench/test_runner.py tests/bench/test_budget.py
  tests/bench/test_parallel_runner.py -q` -> **41 passed** (before: 31
  [test_runner.py 23 + test_budget.py original 8]; after: 41 = 23 unedited + 10
  test_budget.py [8 original + 2 new] + 8 new test_parallel_runner.py -- zero
  regressions, +10 new tests).
- `uv run pytest tests/bench -q --ignore=tests/bench/test_spec.py
  --ignore=tests/bench/test_registries.py --ignore=tests/bench/test_dev_medium_suite.py
  --ignore=tests/bench/test_swebench_import.py
  --ignore=tests/bench/test_swebench_provider.py` (the last two don't exist yet on
  this branch -- ignored harmlessly) -> **255 passed, 1 skipped**.
- `uv run pytest tests/bench -q -m "not real_llm"` (every bench test file, including
  the parallel-agent in-flux files `test_spec.py`/`test_registries.py`/
  `test_dev_medium_suite.py`) -> **327 passed, 1 deselected** -- their in-flux state
  did not affect this task.
- `uv run pytest -q -m "not real_llm"` (whole repo, run twice for stability) ->
  **1184 passed, 4 deselected** both times (a third repo-wide run later showed 1205
  passed as T-Sw5Hd9 landed more files mid-session -- unrelated to this task, 0
  failures throughout).
- `uv run ruff check` + `ruff format --check` on `runner.py`,
  `test_runner.py`(unedited), `test_budget.py`, `test_parallel_runner.py` -> all
  clean.
- `uv run mypy src/agent_orchestrator/bench/runner.py` (matches this repo's actual CI
  scope, `.github/workflows/ci.yml`'s `mypy src`) -> **Success: no issues found in 1
  source file**. Note: `mypy` run directly against the test files (outside CI's own
  `mypy src`-only scope) surfaces `import-untyped`/`LogRecord` `attr-defined` noise
  that is PRE-EXISTING and identical on the untouched `test_runner.py` (verified
  against `git show HEAD:tests/bench/test_budget.py` in isolation) -- not introduced
  by this task, excluded from scope per CI's own convention.

## Deviations / Assumptions
- **`cli.py` NOT touched** (scope adjustment from the orchestrator, authoritative over
  TASK.md's file-ownership list): `--max-parallel` CLI wiring is T-Cm9Tb4's (Wave D).
  See "Forward notes for T-Cm9Tb4" below for the exact wiring.
- **Lock also covers the resume "already recorded" read**, not just the three items
  literally named at the `LOCK-IN POINT` comments (budget check, `tasks_dict` writes,
  `running_cost`, persist): `tasks_dict[task.id]`'s resume-skip check is folded into
  the SAME `_dispatch_or_skip` critical section as the budget check, for a single
  auditable "is this task already done or budget-capped" decision point, rather than
  reasoning separately about why an unlocked read of a per-task-id dict slot would
  also be safe under the GIL. No behavior change vs. TASK.md's own pseudocode (which
  does the same thing inside one `_dispatch_or_skip`).
- **Overshoot bound derivation**: `cap <= sum(non_skipped cost) < cap +
  max_parallel * max_task_cost` -- because the dispatch decision (budget check) is
  itself serialized by the lock, but a task that passes it runs the actual
  materialize/run/grade OUTSIDE the lock, so up to `max_parallel` tasks (the pool's
  own concurrency bound) can be simultaneously "past the check, cost not yet added"
  before any of them writes back. Verified empirically by the barrier-forced AC3
  test (exactly 4 tasks x $3 = $12 over a $1 cap, matching the bound).
- **Threads, not processes** (per TASK.md's own Risks section): subprocess/IO-bound
  tasks release the GIL during `wait()`, so `ThreadPoolExecutor` gives real
  concurrency with trivially-shared in-process state; no pickling/logging-handler
  complications. `attach_run_handler`/`get_run_logger` are called once around the
  whole pool (unchanged from before), never per-task -- confirmed thread-safe
  (stdlib `logging` is thread-safe; verified via the crash-safety and error-isolation
  tests all passing under real concurrent logging from 4 worker threads).
- **Perf test retry**: `test_parallel_wall_clock_speedup_over_serial` retries up to 3
  times (best-of-N) after observing one flake under full-repo-suite CPU contention
  (see Evidence above) -- a measurement-noise mitigation, not a correctness relaxation
  (documented in the test's own docstring).
- No other deviations from TASK.md's Acceptance Criteria/Risks/Pseudocode -- the
  final `runner.py` structure (`_dispatch_or_skip`/`_run_one` nested closures, serial
  `if max_parallel<=1` branch, `ThreadPoolExecutor.map` branch) matches the
  TASK.md-provided pseudocode shape directly.

## Forward notes for T-Cm9Tb4 (campaign command, owns cli.py)
- `run_suite(..., max_parallel: int = 1, ...)` is the new keyword-only-by-convention
  parameter (positioned after `cost_budget_usd`, before `clock`) -- pass an
  already-resolved `int`.
- Mirror the **exact** `--cost-budget-usd` CLI pattern (per T-Bg2Wq4's own forward
  note, now landed in `cli.py`'s `run` command): add `--max-parallel INTEGER` to
  `ao-bench run` (and to whatever `campaign` subcommand T-Cm9Tb4 adds), resolve it as
  `--max-parallel` (if given, `is not None`) `> suite.tier`'s `default_max_parallel`
  via `bench/tiers.py`'s `load_tier_config(suite.tier).default_max_parallel`
  (`resolve_effective(...)["max_parallel"]` already implements exactly this
  precedence -- reuse it, don't re-derive) `> the loader's own builtin fallback (1,
  serial)`. Resolve the suite's `.tier` once (cli.py already loads the suite to read
  `cost_budget_usd`'s tier default -- reuse that same load, do not load twice).
- `max_parallel` must NOT be added to any fingerprint/`overrides` dict (already
  excluded at the `run_suite` level; nothing further needed at the CLI layer).
- No `--max-parallel` value validation needed beyond int parsing: `run_suite` treats
  anything `<=1` as serial (no need to special-case 0 or negative at the CLI either).

## Risks / Blockers
- None outstanding for this task. `max_parallel`-under-load timing is inherently
  environment-sensitive (see the perf test's best-of-3 retry) -- a real, documented
  characteristic of wall-clock-based tests, not a functional risk to `run_suite`
  itself (every OTHER test in this task's scope is deterministic, no sleeps-as-
  synchronization).
- **Downstream**: T-Sg6Jf2's `SweBenchGrader` still needs its OWN Docker-eval lock
  (separate from `_run_lock` here) so agent execution parallelizes while grading
  serializes -- this task provides the task-level pool only, per the Handoff
  Boundary; not this task's concern to implement.

## Next actions
- None -- task complete. Downstream: T-Cm9Tb4 (CLI `--max-parallel` wiring + campaign
  command, forward notes above), T-Sg6Jf2 (SweBenchGrader's own Docker-eval lock).
