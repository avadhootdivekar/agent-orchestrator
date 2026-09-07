# STATUS

- ID: `T-Ac6Vd9-requeue-accounting`
- Updated At: 2026-09-07
- State: In Review
- Owner: developer-agent

## This update

**Implementation complete: In Review.** Read the MERGED `engine.py` from `T-En8Hd4` first
(its own STATUS.md "Hook points" section), then implemented R-1a/R-1b/R-21 narrowly against it.

- **R-1a (accumulate before requeue) — already held by construction, proven by test, not
  re-implemented.** `T-En8Hd4` had already placed the unconditional `ts.cumulative_*`
  accumulation *before* the integration-settle switch in `_settle_completed_task`, so the
  `conflict_resolver`/`conflict_rerun` requeue branches already accumulate correctly. What
  this ticket did: extracted the two near-duplicate accumulation blocks (self-heal's Consult
  Point B retry, and the general one) into one shared `Orchestrator._accumulate_actuals(ts,
  result)` static helper (CLAUDE.md no-duplicate-logic), and proved the whole chain end-to-end
  with a live 3-cycle conflict-ladder test (`TestCumulativeAccumulationAcrossTheLadder`).
- **R-1b (cycle-keyed budget ledger) — implemented in `budget.py` + `engine.py`.**
  `BudgetManager.charge_estimate`/`reconcile`/`reverse_estimate` gained `cycle: int = 1`;
  `DefaultBudgetManager` keys `charged_estimate`/`reconciled_cycles` by
  `cycle_key(task_id, cycle) -> f"{task_id}#{cycle}"` (new public function, exported from
  `budget.py`). `reconciled_tasks` (pre-existing field) is left untouched by the idempotency
  guard but still dual-written once per task_id (deduped) for backward compatibility with any
  reader of an older `state.json` shape. Every `engine.py` call site now passes
  `cycle=ts.dispatch_cycle` (or `ts_pre.dispatch_cycle` in `_prepare_and_maybe_dispatch`):
  charge, reconcile, both `reverse_estimate` sites (quota-exhaustion, provider-429), and the
  resume double-charge guard, which now looks up `cycle_key(tid, ts_pre.dispatch_cycle - 1)`
  (the immediately-preceding cycle) instead of the bare task_id — `prepare_resume` carries
  `dispatch_cycle` forward verbatim (frozen `runstate.py` behavior) so this is always exactly
  one behind the resumed dispatch's own new cycle. `budget.charge`/`budget.reconcile`/
  `budget.resume_reverse` log events gained a `cycle` field (TASK.md's own Interface Note).
- **R-21 (cycle-keyed capture directories) — implemented, with one design correction found
  during gate verification.** `_run_with_retries`/`_run_and_integrate` gained a `cycle: int =
  1` parameter, threaded from `DispatchPrep.cycle` (new field, populated unconditionally in
  `_prepare_and_maybe_dispatch` from `ts_pre.dispatch_cycle`, since dispatch_cycle increments
  for every dispatch regardless of isolation). **Correction:** the first implementation nested
  *every* cycle under `cycle-<n>/`, including cycle 1 — the full suite caught a real regression
  in `tests/playground/test_sum_of_array_deterministic.py::TestArea5OutputCapture` (two tests),
  which hardcode the pre-existing flat `<run_dir>/<task_id>/attempt-1/...` path for a
  never-requeued task. Fixed per AC-7's own suggested alternative: **cycle 1 keeps the legacy
  flat layout** (`<run_dir>/<task_id>/attempt-<n>/`, byte-identical to every pre-this-ticket
  run — the "one place" decision is stated in `_run_with_retries`'s own comment block); **cycle
  2+ nests** under `cycle-<n>/`, so it can never collide with cycle 1's own directory. Both
  playground tests pass again after the fix (confirmed by re-run, not assumed).
  `ui/runs.py` was read, not edited (out of file scope): it surfaces
  `TaskRunState.output_artifact_path` as an opaque string with no path-shape assumption of its
  own, so neither the legacy-flat nor the new cycle-nested form needs a reader-side change —
  flagged below for `T-Dr5Yq6` in case a future capture-path-browsing feature parses this shape
  directly.
- **AC-9 self-heal transcript-clobber investigation: CONFIRMED, and fixed as a byproduct (not a
  second patch).** Self-heal's requeue re-enters `_prepare_and_maybe_dispatch` through the exact
  same code path as any other redispatch, so before this ticket its `output_dir` was keyed by
  task_id alone — a self-heal retry's `attempt-1/` transcript would genuinely have overwritten
  the original failed cycle's. Since `cycle` is now threaded through `_run_and_integrate`/
  `_run_with_retries` unconditionally for every dispatch (not conditioned on isolation or on
  *why* the previous cycle requeued), self-heal's second cycle is protected by the same general
  fix. Verified by a dedicated test (`test_self_heal_requeue_path_accounted`, extended with an
  explicit distinct-capture-directory assertion) rather than assumed. No follow-up ticket needed
  — this was the suspected gap, and it is closed by R-21's own fix, not a separate defect.

## Interface (final signatures, published for downstream tickets)

```python
# budget.py
def cycle_key(task_id: str, cycle: int) -> str: ...  # "<task_id>#<cycle>"

class BudgetManager(ABC):
    def charge_estimate(self, task_id: str, estimate: int, counters: BudgetCounters,
                         cycle: int = 1) -> None: ...
    def reconcile(self, task_id: str, actual: int, counters: BudgetCounters,
                  cycle: int = 1) -> None: ...
    def reverse_estimate(self, task_id: str, counters: BudgetCounters,
                          cycle: int = 1) -> None: ...
    # gate()/on_provider_429() unchanged -- consumed_tokens/window_consumed_tokens already
    # reflect every cycle's charge/reconcile regardless of key, so no cycle param is needed there.

# engine.py
class DispatchPrep:
    cycle: int = 1   # NEW -- ts_pre.dispatch_cycle, always populated

Orchestrator._run_and_integrate(..., cycle: int = 1) -> WorkerOutcome
Orchestrator._run_with_retries(..., cycle: int = 1) -> TaskResult
Orchestrator._accumulate_actuals(ts: TaskRunState, result: TaskResult) -> None  # NEW, shared
```

## Hook points published for T-Wl2Bq7 / T-Lr6Ka3 / T-Cx4Jf1

- **T-Wl2Bq7 (workspace run lock):** untouched by this ticket. `_activate_integration` is
  exactly where `T-En8Hd4`'s own STATUS.md said it would be; this ticket added no code on that
  path. No interaction with cycle-keying (the lock is claimed once per run, not per cycle).
- **T-Lr6Ka3 (resolver/rerun dispatch ladder):** `_run_and_integrate`'s `cycle` parameter is
  already threaded all the way to `_run_with_retries`'s capture-directory keying, so when
  `T-Lr6Ka3` adds the `mode` branch (substituting resolver agent/instruction/conflict-json
  inputs, or reset-to-fresh-head + previous-patch inputs) it needs to do nothing extra for
  accounting or capture paths — both are already correct for whatever cycle that redispatch
  lands on. Its own D9 cost test can assert against `BudgetCounters.consumed_tokens`/
  `reconciled_cycles` directly (this ticket's own tests in `TestBudgetLedgerKeyedByCycle` are a
  worked example of the assertion shape to use).
- **T-Cx4Jf1 (capture-path display / event emission):** the `budget.charge`/`budget.reconcile`/
  `budget.resume_reverse` log events now carry a `cycle` field. Capture-directory display (if
  any is added) should account for the two-shape rule (cycle 1 flat, cycle 2+ nested under
  `cycle-<n>/`) documented in `_run_with_retries`'s own comment block — `ui/runs.py` needs no
  change today since it treats `output_artifact_path` as opaque, but a future feature that lists
  *all* of a task's capture directories (not just the latest) would need to glob both shapes.
  Flagged for `T-Dr5Yq6` per the note above.

## Design correction record (for the reviewer)

- Original draft: every dispatch cycle, including cycle 1, nested under `cycle-<n>/`. This
  broke `tests/playground/test_sum_of_array_deterministic.py` (2 tests, real regression, not
  transient — confirmed by root-causing the exact hardcoded path assertions before fixing).
  Corrected to: cycle 1 stays flat (byte-identical, NFR-2), cycle 2+ nests. This matches TASK.md
  AC-7's own suggested alternative ("cycle 1 may keep the legacy flat `attempt-<n>` layout")
  more literally than the first draft did. All tests (including the two playground ones) pass
  after the fix; the design is recorded in one place (`_run_with_retries`'s comment block) per
  AC-7's own "state in one place" requirement.

## Gates (exact numbers)

- `uv run ruff check .` / `uv run ruff format --check .`: clean, repo-wide.
- `uv run mypy src`: unchanged at exactly 4 pre-existing `_version.py` errors.
- Targeted suite (`tests/test_engine.py tests/test_engine_isolation.py
  tests/test_engine_isolation_accounting.py tests/test_engine_budget.py tests/test_budget.py
  tests/test_budget_integration.py tests/test_engine_breakers.py tests/test_e2e_cli_isolation.py
  tests/test_e2e_monitoring_cli.py`): **155 passed / 0 failed**.
- Full suite WITH coverage (`timeout 900 uv run pytest -q -p no:cacheprovider
  --cov=agent_orchestrator --cov-report=term`), run in the foreground: **3446 passed / 7 skipped
  / 0 failed** in 177.89s — **TOTAL coverage 95%** (matches the epic's own baseline, no
  regression). No transient failures observed on this run.
- `tests/playground/test_sum_of_array_deterministic.py` (regressed by an earlier draft,
  root-caused and fixed, see above): **21 passed / 0 failed**, re-confirmed independently.
- New/changed tests: `tests/test_engine_isolation_accounting.py` (new, 9 tests) covering all of
  R-1a (3-cycle ladder x2, self-heal), R-1b (BudgetCounters-level assertions, rate-window
  gating), R-21 (distinct capture dirs, content-level), the cost breaker seeing the cumulative
  sum, resume-mid-cycle-2 idempotency, and a golden non-isolated status.json cost block.
  `tests/test_budget.py` grew 18 -> 24 (6 new cycle-keying unit tests; 3 existing tests updated
  for the new `charged_estimate` key shape — `charged_estimate == {"t1": N}` became
  `== {cycle_key("t1", 1): N}`, since the key format change is real and system-wide at
  `dispatch_cycle == 1`, not just for a multi-cycle task; the counters/totals stayed
  byte-identical, only the internal dict key literal changed). `tests/test_engine_budget.py`
  stayed at 19 tests; one (`test_stale_estimate_reversed_on_resume`) was rewritten to model a
  realistic cycle-1-crashed scenario (previously it manually set a bare-`task_id`-keyed stale
  charge on a `RunState` with no corresponding `TaskRunState` at all, which the OLD guard also
  failed to clean up correctly — the new test is both correct for the new cycle-keyed guard and
  a strictly stronger assertion (`consumed_tokens == 100` exactly, not merely `< 100_000`) than
  the one it replaced.
- No file outside this ticket's scope was touched (`runstate.py` added to scope by explicit
  coordinator authorization for the Major-1 fix-now, see the review-response entry below).
  `git status` confirms only `src/agent_orchestrator/budget.py`,
  `src/agent_orchestrator/engine.py`, `src/agent_orchestrator/runstate.py`,
  `tests/test_budget.py`, `tests/test_engine_budget.py`, `tests/test_runstate.py` (modified) and
  `tests/test_engine_isolation_accounting.py` (new) under this ticket's control — `cli.py`,
  `project_config.py`, `models.py`, every `isolation/` module, and every
  `tests/isolation/*`/`tests/test_cli*.py`/`tests/test_e2e_cli_prune*.py`/
  `tests/test_project_config*.py` file are untouched, matching the concurrency boundary.

## Evidence
- Design: [`docs-md/task-isolation-hld.md`](../../../../docs-md/task-isolation-hld.md) (§10.3,
  §11 M5, §13, §17.5, §24) and
  [`ADR-0013`](../../../../docs-md/adr/ADR-0013-per-task-git-isolation-and-rebase-integration.md).
- Upstream: `T-En8Hd4-engine-isolation-wiring/STATUS.md` "Hook points published for T-Ac6Vd9 /
  T-Wl2Bq7 / T-Lr6Ka3 / T-Cx4Jf1" section (read first, per this ticket's own instruction).
- Originating findings and their dispositions: HLD §24 "Review dispositions" rows R-1, R-1a,
  R-1b, R-21.
- Edited source: `src/agent_orchestrator/budget.py`, `src/agent_orchestrator/engine.py`.
- New tests: `tests/test_engine_isolation_accounting.py` (9 tests).
- Additive/updated tests: `tests/test_budget.py` (+6 new, 3 updated for the key-format change),
  `tests/test_engine_budget.py` (1 rewritten for the new cycle-keyed guard).

## Risks / Blockers
- Not blocked. `T-Lr6Ka3`/`T-Wl2Bq7`/`T-Cx4Jf1` can proceed against the published hook points
  above without waiting further on this ticket.
- The capture-directory two-shape rule (flat cycle 1, nested cycle 2+) is a deliberate,
  documented tradeoff to preserve NFR-2 byte-identical behavior for the common case rather than
  a fully uniform scheme — any future work that lists/globs capture directories must account for
  both shapes (flagged for `T-Dr5Yq6` above).
- ~~`TaskRunState.cumulative_*` does NOT survive a genuine process crash + `prepare_resume` for a
  non-terminal task~~ **FIXED in this ticket (review Major-1, authorized fix-now).** See the
  review-response entry below — `runstate.py::prepare_resume` now carries the cumulative
  token/cache/cost counters forward alongside `dispatch_cycle`, with a dedicated regression test
  and an old-`state.json`-without-the-fields backward-compat test.

## Next actions
1. `T-Lr6Ka3`/`T-Wl2Bq7`/`T-Cx4Jf1`: proceed against the published hook points/interface above.
2. Reviewer: confirm the Major-1 fix in `runstate.py::prepare_resume` and its two new tests in
   `tests/test_runstate.py`.

---
- By: developer-agent · Role: developer · Date: 2026-09-07 · Comment: R-1a/R-1b/R-21 implemented
  narrowly against the merged `T-En8Hd4` `engine.py`. R-1a was already correct by construction
  (T-En8Hd4 placed the accumulation before the conflict switch); this ticket extracted the
  shared `_accumulate_actuals` helper and proved the chain with a live 3-cycle ladder test.
  R-1b: `budget.py` cycle-keyed ledger (`cycle_key`, ABC `cycle=` param on
  charge_estimate/reconcile/reverse_estimate), every `engine.py` call site updated including the
  resume double-charge guard. R-21: capture directories cycle-keyed for cycle 2+; cycle 1 stays
  flat (design correction made after the full suite caught a real regression in
  `tests/playground/test_sum_of_array_deterministic.py` — root-caused and fixed, both tests
  re-confirmed green). AC-9 self-heal transcript-clobber: confirmed as a real, pre-existing gap
  and closed as a byproduct of the general R-21 fix (verified by test, not assumed). Gates:
  ruff/format clean; mypy 4 pre-existing `_version.py` errors; targeted suite 155/0; full suite
  with coverage 3446 passed / 7 skipped / 0 failed, TOTAL 95% (run in the foreground per
  instruction, no transient failures). No commit made per instruction.

---
- By: reviewer-agent · Role: reviewer · Date: 2026-09-07 · Comment: **APPROVE WITH CHANGES**
  (must-fix: W-1). Full findings in `REVIEW.md`. R-1a/R-1b/R-21 all independently verified
  correct by reading `_settle_completed_task`/`_prepare_and_maybe_dispatch` control flow (not
  just the diff hunks) and re-running the targeted suite (195 passed/3 skipped),
  ruff/format/mypy (0 new errors, 4 pre-existing `_version.py` as claimed). No Critical findings.
  Major-1: `TaskRunState.cumulative_*` does not survive a crash + `prepare_resume` for a
  requeued task (frozen `runstate.py:255-266`, disclosed by the developer in this file's Risks
  section and tested — the durable ledger `RunState.budget_counters` is unaffected, so this is a
  per-task display gap, not a budget-enforcement bug). Recommend converting the "Next actions"
  item 3 above into an actual tracked ticket rather than leaving it as prose. W-1 (must-fix
  before merge): `test_self_heal_requeue_path_accounted`
  (`tests/test_engine_isolation_accounting.py:351`) omits `sleeper=lambda _s: None`, causing a
  real ~30s sleep — every other self-heal test in the repo injects a fake sleeper; this is a
  direct CLAUDE.md fixed-clock violation with a one-line fix. W-2: the new `cycle` field on
  `budget.*` log events is correct by inspection but has no test asserting its value. Two Minor
  nits (inline `"cycle-"` literal; pre-existing O(n) list membership pattern, not a regression).
  DRY confirmed: no third accumulation copy exists alongside `_accumulate_actuals`. Backward
  compatibility of the `BudgetManager` ABC's new `cycle=1` default confirmed against every real
  caller (no other subclass exists in-repo). Downstream hook points for `T-Lr6Ka3`/`T-Cx4Jf1`
  match what's actually shipped.

---
- By: developer-agent · Role: developer · Date: 2026-09-07 · Comment: Review fix pass — all
  findings dispositioned, per coordinator authorization to fix Major-1 now (not defer).
  - **W-1 (must-fix) — FIXED.** Added `sleeper=lambda _s: None` to
    `test_self_heal_requeue_path_accounted`'s `Orchestrator(...)` construction, matching every
    other self-heal test's convention. Confirmed: 30.01s → 0.02s; `--durations=5` on the full
    file now tops out at 0.14s.
  - **Major-1 — FIXED HERE (authorized fix-now, not deferred to a follow-up ticket).**
    `runstate.py::prepare_resume`'s wholesale-replace branch for a non-terminal task now carries
    the five `cumulative_*` fields (input/output/cache-creation/cache-read tokens, cost_usd)
    forward alongside `dispatch_cycle` — the same "carry forward by hand" treatment
    `dispatch_cycle` already got, and consistent with `models.py`'s own field docstring ("Not
    reset on resume"), which the prior implementation silently didn't honor for this branch. No
    schema change: these are pre-existing `TaskRunState` fields with defaults, not new ones, so
    an older `state.json` still loads. Two new tests in `tests/test_runstate.py`
    (`TestPrepareResume`): the regression scenario the review asked for (persist mid-ladder with
    real cumulative values → `prepare_resume` → fields intact, dispatch_cycle unchanged, then
    still increasing after a simulated next-cycle `+=`), and an explicit old-`state.json`
    backward-compat test (fields stripped from the raw JSON before load → pydantic default-fills
    to 0/0.0 → `prepare_resume` doesn't error). Updated `TestResumeMidCycleTwo`'s existing
    engine-level test and its docstring to assert the now-correct `cumulative_input_tokens ==
    150` (cycle 1 + cycle 3) instead of the previously-documented `== 0` characterization of the
    bug. "Risks"/"Next actions" above updated to say fixed, not deferred — no separate follow-up
    ticket filed since the fix landed here.
  - **W-2 — FIXED.** Two new `caplog`-based tests: `test_budget_charge_and_reconcile_events_
    carry_the_correct_cycle` (2-cycle ladder; asserts `budget.charge`/`budget.reconcile` records
    for task "a" carry `cycle == [1, 2]` in dispatch order) and an addition to the existing
    resume test asserting `budget.resume_reverse`'s `cycle` field names the STALE cycle (2), not
    the new one (3) about to be charged.
  - **Minor-1 — applied** (≤15 lines): hoisted the inline `f"cycle-{cycle}"` literal to a new
    `_CYCLE_DIR_PREFIX = "cycle-"` module constant in `engine.py`, matching `budget.py`'s
    `_CYCLE_KEY_SEP` precedent. **Minor-2 — not applied**, per the review's own recommendation
    (frozen field, no regression, not worth a change now).
  - Gates re-run: `ruff check .`/`format --check .` clean on every file this ticket owns (2
    pre-existing E501s surfaced repo-wide belong to `T-Cx4Jf1`'s concurrent, in-progress
    `cli.py`/`project_config.py` work — confirmed not touched by this ticket). `mypy src`:
    unchanged at 4 pre-existing `_version.py` errors. Targeted suite (`tests/test_engine.py
    tests/test_engine_isolation.py tests/test_engine_isolation_accounting.py
    tests/test_engine_budget.py tests/test_budget.py tests/test_budget_integration.py
    tests/test_engine_breakers.py tests/test_e2e_cli_isolation.py tests/test_e2e_monitoring_cli.py
    tests/playground tests/test_runstate*.py`, `--durations=5`): **209 passed / 3 skipped / 0
    failed**, slowest 0.29s. Full suite (`pytest -q`), run twice per instruction: **run 1: 3447
    passed / 7 skipped / 3 failed** (`tests/test_cli_isolation_flags.py::TestResolveIsolationSettings`
    × 3 — `T-Cx4Jf1`'s own file, not touched here); **run 2: 3459 passed / 7 skipped / 0 failed**
    — confirms run 1's failures were transient (unrelated to this ticket's files). No commit made
    per instruction.
