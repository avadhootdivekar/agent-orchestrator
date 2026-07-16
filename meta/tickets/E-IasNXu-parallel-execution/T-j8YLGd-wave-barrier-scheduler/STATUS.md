# STATUS

- ID: `T-j8YLGd-wave-barrier-scheduler`
- Updated At: 2026-07-15
- State: Done
- Owner: developer agent

## This update
- By: Claude · Role: developer · Date: 2026-07-15
- Comment: Replaced the serial cursor walk in `Orchestrator.run()` with the wave/barrier
  scheduler over a `concurrent.futures.ThreadPoolExecutor(max_workers=self._max_parallel)`.
  The pre-dispatch body (not_taken/done/should_skip/apply_join/missing-inputs/budget
  gate+charge/mark-running/manifest+gate path resolution) was extracted **verbatim** into
  `_prepare_and_maybe_dispatch(...) -> DispatchPrep`; the post-dispatch body (quota/429/
  self-heal requeue, reconcile, self-heal, outcome handling, router hook, breaker eval,
  emit_tasks injection, loop-gate clone) was extracted **verbatim** into
  `_settle_completed_task(...) -> SettleResult`. New helpers `_is_barrier`, `_predecessors`,
  `_ready_ids`, `_drain_remaining` complete the scheduler per the HLD §5-§6 pseudocode. The
  blocking AC-1 gate is met: **every test in `tests/test_engine*.py` passes UNEDITED at the
  default `max_parallel=1`** (58/58), and the full suite is green with zero regressions
  (837 passed / 3 skipped vs. the 815/3 baseline — the +22 are new tests in a new file).

## Extraction methodology (why I'm confident in "verbatim")
Given AC-1 is explicitly "the blocking acceptance gate," I did not hand-retype the
~800-line pre/post-dispatch bodies from memory. I extracted the exact original line ranges
(`engine.py` pre-refactor lines 279-565 and 579-1077) into scratch files via `sed`, applied
the ticket's four sanctioned mechanical transforms as anchor-based, assert-verified string
substitutions (each anchor checked to match exactly once before substitution — the script
raises if an anchor doesn't match byte-for-byte), and then spliced the transformed bodies
back into `engine.py`. Every untouched region (imports prologue minus two intentional
additions, the full `__init__`, and everything from `# Budget helpers (T-algywf)` onward —
`_sum_actuals`, `_is_unsatisfiable`, `_router_for_task`, `_apply_join`, `_on_router_success`,
`_route_fail`, `_consult_breaker_trips`, `_consult_task_failure_heal`, `_run_with_retries`,
`_inject`, `_recompute_order`, `_loop_for_gate`, `_gate_path_for_iter`, `_clone_body`) was
verified to be an exact substring of the new file, both before and after the final
`ruff format` pass.

## Deviations from the ticket pseudocode (flagged, not silently guessed)
1. **`BLOCKED` is a defined signal but is never actually produced this task.**
   `_prepare_and_maybe_dispatch`'s budget-gate block keeps today's inline
   sleep-and-retry-the-gate-loop behavior verbatim (unconditional on `in_flight`), matching
   the ticket's explicit carve-out: "here it may simply mean 'don't fill further'... at
   `in_flight=={}` preserve today's inline budget-wait sleep so N=1 is unchanged." The full
   `in_flight`-aware drain-then-re-gate policy is T-VSfAUN's scope; the `BLOCKED` branch in
   `run()`'s fill loop exists for forward compatibility and is presently unreachable.
2. **`run()` normalizes `state.tasks[tid].status = "pending"` uniformly for every REQUEUE
   signal**, not only the ones where the verbatim settle body already set it. Root cause:
   the quota-exhaustion and self-heal requeue sites already set `ts.status = "pending"`
   before their `cursor -= 1; continue` today, but the **provider-429-wait** site never
   did (the old cursor model revisits the same `tid` positionally regardless of status, so
   it never needed to). The new ready-set model's `_ready_ids` excludes `status == "running"`
   from re-admission, so without this normalization a 429-requeued task would never be
   re-offered and the run would silently stall at `state.status == "succeeded"` with that
   task stuck at `"running"` forever. This is exactly what the ticket's own pseudocode line
   (`IF signal == REQUEUE: ts[tid].status = "pending"`, present in both TASK.md and HLD §6)
   specifies at the caller — confirmed correct, not redundant with the settle body's own
   (site-specific) assignment.
3. **`_estimate` re-derivation in the reconcile log line.** The old inline loop's
   task-block-scoped `_estimate` local no longer exists once prepare/settle are split.
   `_settle_completed_task`'s normal-reconcile branch reads
   `state.budget_counters.charged_estimate.get(tid, 0)` **before** calling
   `self._budget_manager.reconcile(...)` (which pops that key) — this holds the identical
   value `charge_estimate()` stored pre-dispatch, so the `"actual=%d estimate_delta=%d"` log
   line's numbers are unchanged. Exactly the substitution the ticket's "Known extraction
   gotcha" section anticipated.
4. **Loop-gate RESHAPED site drops the `cursor` half of `_recompute_order`.** `cursor` isn't
   part of the new ready-set interface anywhere; that site's log line
   (`"Loop %s starting iteration %d"`) never read it, so I call `graph.topological_order()`
   directly (identical `order` result, one fewer computed-but-dead local — avoids an F841
   ruff finding). The **emit_tasks** RESHAPED site keeps `self._recompute_order(graph,
   ctx.done)` unchanged: its log line (`"Injected %d tasks; order recomputed (%d
   remaining)"`) genuinely reads `len(order) - cursor`, so both values stay live there.
   Both RESHAPED sites hand the already-computed `(graph, order)` back to `run()` via
   `SettleResult` instead of letting the caller rebuild — avoids a second `build_dag` call
   that would double-emit `dag.py`'s inferred-edge warning into `run.log`.
5. **`_prepare_and_maybe_dispatch` returning `HALT` mid-fill does not call
   `_drain_remaining`** before `run()`'s fill loop sets `failed = True; break` — matching the
   ticket's own pseudocode exactly (only the settle-HALT and cancel paths drain). Verified
   irrelevant at `N=1`: the fill loop's `len(in_flight) >= self._max_parallel` cap means at
   most one `_prepare_and_maybe_dispatch` call happens per fill pass when
   `max_parallel=1`, and that pass always starts with `in_flight` empty (just drained), so
   there is never anything to drain at that decision point at the default `N`.
   `pool.shutdown(wait=True)` (the `with` block) still structurally guarantees no thread
   leak (NFR-4) regardless — this is a documented gap for `N>1`, explicitly in T-VSfAUN's
   stated scope ("budget/quota/429/self-heal/breaker/cancel correctness under concurrency").
6. **A worker exception (`fut.result()` re-raising) is not caught anywhere in `run()` or
   `_drain_remaining`.** This matches the OLD code's own behavior (the synchronous
   `result = self._run_with_retries(...)` call was never wrapped in a try/except either), so
   it is not a new gap introduced by the wave scheduler. `pool.shutdown(wait=True)` via the
   `with` block runs on any exit path including an escaping exception, satisfying NFR-4/AC-6.
   My AC-6 test (`TestNoThreadLeak`) asserts `pytest.raises(RuntimeError)` +
   `threading.active_count()` back to baseline, not a "run() gracefully returns a failed
   RunState" outcome — nothing in the ticket, HLD §11, or ADR-0007 D7 asks for the latter.

## `_quota_exhausted_since` / `done` threading
Per the ticket's recommendation, both live on a new `_RunContext` dataclass
(`repo_paths`, `agents`, `cones`, `membership`, `run_log`, `done`, `quota_exhausted_since`)
constructed once in `run()`'s setup and passed BY REFERENCE into
`_prepare_and_maybe_dispatch`/`_settle_completed_task`/`_drain_remaining`. `done` is the
SAME `set` object `run()` already had (mutations via `ctx.done.add(tid)` inside the helpers
are visible to `run()`'s own `done` local on the very next `_ready_ids` call — no hand-back
needed). `quota_exhausted_since` is read AND written exclusively through `ctx.*` inside
`_settle_completed_task`'s quota-exhaustion block, reset to `None` on every task success,
identical semantics to the old closed-over local. `DispatchPrep`/`SettleResult` are small
`@dataclass`es (not pydantic `BaseModel`s, matching this codebase's existing pattern of
using plain classes for pure in-process control-flow values that never cross a
serialization boundary) with `Literal[...]` signal fields (matching the codebase's
established `Literal`-over-`enum.Enum` convention, e.g. `budget.BudgetDecision`,
`breakers.Action`).

## Evidence
- Files changed: `src/agent_orchestrator/engine.py` (imports: `+Future/ThreadPoolExecutor/
  as_completed`, `+dataclass`, `+Graph`; `+DispatchSignal/SettleSignal/DispatchPrep/
  SettleResult/_RunContext`; `run()` rewritten to the wave loop; `+_prepare_and_maybe_dispatch/
  _settle_completed_task/_drain_remaining/_is_barrier/_predecessors/_ready_ids`).
  `tests/test_wave_scheduler.py` (new file, 22 tests).
- `uv run pytest tests/test_engine*.py -q` — **58 passed** (AC-1 gate; zero edits to any
  existing test body — `git diff --stat tests/` shows only the pre-existing T-JXiI9j
  additions to `test_cli.py`/`test_engine.py`/`test_project_config.py`, 0 deletions).
- `uv run pytest -q` (full suite) — **837 passed, 3 skipped** vs. the 815 passed / 3 skipped
  baseline: **+22, zero regressions.**
- New tests (`tests/test_wave_scheduler.py`, 22): `TestPredecessors` (2), `TestIsBarrier`
  (5, incl. the `__iter2` loop-gate-clone case), `TestReadyIds` (7, incl. deterministic
  sorted-Kahn order + not_taken-counts-as-settled + own-terminal-status exclusion),
  `TestN1GoldenWorkflow` (2 — AC-1's dedicated golden: diamond + router + emit_tasks + loop +
  budget at N=1, asserting explicit RunState field values and the full ordered 48-event
  `run.log` sequence, both captured from an actual N=1 run since AC-1's own text calls for
  "explicit expected values encoding today's serial behavior," not a hand-derivation),
  `TestParallelDispatchProof` (1 — AC-2, event-gated proof that task B's `execute()` enters
  before task A is released at N=2), `TestBarrierIsolation` (2 — AC-4, a barrier's
  `entered_snapshot` is always the empty set and 3 siblings genuinely overlap first;
  two ready-together barriers never overlap each other), `TestNoThreadLeak` (1 — AC-6, a
  forced worker exception propagates via `pytest.raises` and `threading.active_count()`
  returns to baseline), `TestEmitAndLoopAtMaxParallelFour` (2 — AC-5, N=4 variants of the
  existing N=1-only `test_dynamic_injection.py`/`test_loop_construct.py` scenarios, added
  as new tests rather than edited into those files). Concurrency tests use a local
  thread-safe `_GatedExecutor` double (per-task `threading.Event` gate/entered signals,
  lock-guarded bookkeeping) — the full gated-executor harness is T-TNleFt's scope; this is
  the "local test-double executor" the ticket explicitly sanctions for this task. Re-ran the
  full `test_wave_scheduler.py` file 8 consecutive times with zero flakes.
- `uv run ruff check .` — 2 pre-existing errors, both in untouched `tests/test_e2e_cli.py`
  (`E501` line 209, `F841` unused `manifest_path` line 270). Zero errors on every file this
  ticket touched.
- `uv run ruff format --check .` — all 70 files clean (including both touched files).
- `uv run mypy src` — 4 pre-existing errors, all in untouched `src/agent_orchestrator/
  _version.py` lines 24-27 (`[assignment]`, `None` vs. declared `str`/`bool`). Zero errors on
  `engine.py`.
- `git status --short -- specs` — empty (zero schema changes, per ADR-0007 D5/NFR-3 — this
  task never touched a spec field).

## Risks / Blockers
- No blockers for this task's own scope. Forward risk (unchanged from EPIC.md R1/R3/R4):
  `T-VSfAUN` must harden budget-wait-under-concurrency (currently BLOCKED-signal-inert, per
  Deviation #1), the provider-429/self-heal drain-on-mid-fill-HALT gap (Deviation #5), and
  breaker-count nondeterminism at `N>1` (inherent, accepted per EPIC.md R4) — none of these
  affect `N=1` byte-identical behavior, which is proven by the full AC-1 gate.
- Barrier predicate correctly reuses `_loop_for_gate` (handles `__iter` clones) and
  `_router_for_task` — verified via `TestIsBarrier`, no hand-rolled id matching introduced.

## Next actions
1. Handed off to `T-VSfAUN-concurrency-failure-semantics` (fills in the concurrency-correct
   budget/requeue/cancel semantics inside `_prepare_and_maybe_dispatch`/
   `_settle_completed_task`/`_drain_remaining` per Deviations #1 and #5 above).
2. `T-TNleFt` can now build its gated-executor harness + `N>1` interaction matrix on top of
   the six new helpers and the `_GatedExecutor` pattern established in
   `tests/test_wave_scheduler.py` (a heavier, shared harness is still T-TNleFt's own
   deliverable — this file's `_GatedExecutor` is intentionally local/minimal).
