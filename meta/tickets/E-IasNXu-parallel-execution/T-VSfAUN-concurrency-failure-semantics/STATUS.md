# STATUS

- ID: `T-VSfAUN-concurrency-failure-semantics`
- Updated At: 2026-07-15
- State: Done
- Owner: developer agent

## This update
- By: Claude · Role: developer · Date: 2026-07-15
- Comment: Implemented and proved the concurrency-correct budget/requeue/failure/cancel
  semantics on top of `T-j8YLGd`'s wave/barrier scheduler. **Only one of the four
  sub-deliverables required a code change** (sub-deliverable 1, FR-6): `_settle_completed_task`
  and `_drain_remaining`, as landed by `T-j8YLGd`, already matched the ticket's own pseudocode
  verbatim for quota/429/self-heal requeue (FR-7), breaker/cancel drain (FR-8), and thread-leak
  safety (NFR-4) — verified, not re-derived, via the new tests below. Deviations #1 and #5 named
  in `T-j8YLGd/STATUS.md` (the two concrete starting points) are now both closed.

### Sub-deliverable 1 — budget BLOCKED drain-then-re-gate (FR-6)
  `_prepare_and_maybe_dispatch`'s budget-gate block was an internal `while not _gate_admitted:`
  retry loop that unconditionally slept inline on every block, regardless of `in_flight`
  (T-j8YLGd's documented carve-out). Replaced with a **single gate check per call**, taking a new
  `in_flight_nonempty: bool = False` parameter:
  - unsatisfiable / `on_exhaustion=="stop"` → `record_trip` + `HALT` (unchanged).
  - blocked + `in_flight_nonempty` → return `BLOCKED` immediately, **no sleep** (R3: never sleep
    while a sibling holds the capacity that might free the window).
  - blocked + nothing in flight → sleep once inline exactly as before, then return `BLOCKED`
    (re-gating now happens by the caller re-invoking this method on a later wave, not an internal
    loop). At `N=1`, `in_flight` is always empty at gate time, so this is the *only* reachable
    branch — same `gate/wait/resume` event sequence as before, just constructed across separate
    calls instead of one internal loop (proved byte-identical: see N=1 evidence below).
  `run()`'s fill-loop call site now passes `in_flight_nonempty=bool(in_flight)` (computed live,
  so a task blocked mid-wave after siblings already dispatched this same wave correctly sees
  `True`); the stale "never actually returns BLOCKED" comment was replaced with the real
  drain-then-re-gate explanation.

### Sub-deliverables 2-4 — verified unchanged (FR-7, FR-8, NFR-4)
  Read `_settle_completed_task`'s quota-exhaustion/provider-429/self-heal blocks and
  `_drain_remaining` line-by-line against the ticket's pseudocode and HLD §7.2/§7.3: every
  reverse-estimate + `_sleeper` wait + `ts.status="pending"` + `REQUEUE` site, every `HALT` site
  (failed/timed_out/cancelled settle, breaker trip, budget stop/unsatisfiable, router-fail), and
  `_drain_remaining`'s `as_completed` + settle-ignoring-further-signals loop were already correct
  as landed — no reordering, no missing drain, no double-reverse. This task's job here was proof,
  not construction: new concurrency-genuine tests (below) exercise all three under `N>1` with real
  siblings in flight.

## Acceptance Criteria — evidence
- **AC-1 (budget cap holds under concurrency).** `TestBudgetCapUnderConcurrency`: `max_parallel=4`,
  a total-token budget sized to admit exactly 3 of 4; genuine 3-way overlap proven via the shared
  `_GatedExecutor`'s `entered_snapshot`/`max_concurrent`; the 4th is confirmed never dispatched
  (`"d" not in gated.entered_order`) until a release frees the window; final `consumed_tokens`
  equals the exact sum of actuals with nothing stranded (`charged_estimate == {}`,
  `reconciled_tasks` has all 4 exactly once); a second test forces two different first-completion
  orders (`first_release="a"` vs `"c"`) and asserts the identical final total.
- **AC-2 (no budget deadlock on a rolling window).** `TestNoBudgetDeadlockOnRollingWindow`: proved
  via the exact `run.log` `budget.*` event order rather than thread timing — task `x` is submitted
  before `y`'s first gate check in the *same* fill pass, so `y` is genuinely a member of
  `in_flight` (the engine's own definition throughout this design) without needing thread races.
  The asserted event sequence (`gate_block(y)` → *no* `wait` → `reconcile(x)` → `gate_block(y)`
  again → *now* `wait(y)` → `resume(y)` → `charge(y)`) fails loudly if the R3 fix regresses to
  "sleep while a sibling holds capacity." Sleeper called exactly once, for exactly the window
  duration (60.0s), via a mutable fake-clock cell (no real waits).
- **AC-3 (quota/429/self-heal requeue with siblings in flight).** `TestRequeueWithSiblingInFlight`,
  3 tests (quota, provider-429, self-heal). Race-free synchronization pattern: gate the requeued
  task's *first* call too (not just the sibling's), rendezvous on "both genuinely dispatched," then
  re-arm a fresh `entered_event` for the second call *before* releasing the first — proves the
  redispatch genuinely happens on a later wave while the sibling is still gated/in-flight, not
  cancelled. Estimate reversed + recharged + reconciled exactly once (`reconciled_tasks.count("q")
  == 1`, `charged_estimate == {}`); self-heal variant asserts exactly one `monitor_decisions` entry
  with `decision == "retry"`.
- **AC-4 (breaker halt mid-wave drains).** `TestBreakerHaltDrainsInFlight`: `task_failures`
  breaker (threshold=1, default `mode="hard"`) trips when `s` settles failed while `t` is still
  gated/in-flight; `t` is drained (settled `succeeded`, persisted) rather than left `running`;
  final `state.status == "failed"`. Resume proof: `prepare_resume` keeps `t` as `succeeded`
  (never reset), `s` reset to `pending`; a resume executor configured to **fail** `t` if called
  again proves it is never re-dispatched, while `s` genuinely retries and the resumed run
  succeeds.
- **AC-5 (cancel mid-wave drains + resumable).** `TestCancelDrainsInFlight`: 3 gated tasks prove
  genuine 3-way overlap; releasing only `trigger` lets the run loop drain one completion and
  return to the top of the wave — the *only* place `cancel_fn` is polled — with exactly `t1`/`t2`
  still in flight; both are drained (not killed) and persisted, `state.status == "cancelled"`. A
  4th task depending on `t1` is never even reached (cancel stops admission before the next fill).
  Resume proof mirrors AC-4: a trap executor would fail `trigger`/`t1`/`t2` if re-dispatched; only
  the genuinely unfinished `later` task runs on resume.
- **AC-6 (no thread leak).** Folded into every scenario above rather than one generic test: each
  asserts `threading.active_count()` returns to its own pre-run baseline after `thread.join()`,
  covering a *different* new drain path each time (BLOCKED-drain, quota/429/self-heal-drain,
  breaker-halt-drain, cancel-drain) — stronger than a single dedicated case since it's tied to the
  exact scenario exercising each path.
- **AC-7 (lint/types/tests, N=1 unchanged).** See Evidence below.

## No-deadlock / reverse-exactly-once / resume-after-drain — how proved
- **No deadlock:** AC-1/AC-2 construct the exact R3 scenario (budget block while a sibling holds
  capacity) and assert the engine makes progress without hanging — `thread.join(timeout=5)` would
  itself fail the test if the engine ever blocked instead of draining.
- **Reverse-estimate exactly once:** AC-1/AC-3 assert `state.budget_counters.charged_estimate ==
  {}` (nothing left outstanding) and `reconciled_tasks.count(tid) == 1` for every requeued task —
  a double-reverse or a leaked charge would show up as a wrong `consumed_tokens` or a non-empty
  `charged_estimate`.
- **Resume-after-drain:** AC-4/AC-5 use a **trap executor** on resume (`FakeExecutor(behaviors=
  {<succeeded-during-drain task>: "fail"})`) — if resume incorrectly re-dispatched a task that
  actually succeeded during the drain, the resumed run would fail loudly; both tests assert
  `state2.status == "succeeded"`, proving it does not.

## Deviations from the ticket (flagged, not silently guessed)
1. **AC-6 satisfied via inline per-scenario assertions, not one dedicated `TestNoThreadLeak`-style
   class.** Every new concurrency test asserts `threading.active_count()` returns to baseline
   after `thread.join()`, tied to its own specific new drain path — a deliberate choice to cover
   AC-6 four times (once per genuinely different code path) rather than once generically.
2. **Test-double gotcha (not an engine bug):** `_GatedExecutor.execute()` (T-j8YLGd's double, reused
   unedited) writes declared `ctx.output_paths` files unconditionally, before my wrapper's script
   can override the result to a failure/quota/429 outcome. A task later re-dispatched via REQUEUE
   or resume would have `should_skip()` see the stale output from the *first* (overridden-to-fail)
   attempt and wrongly skip the genuine retry. Fixed at the test-authoring level: every task that
   gets a scripted failure-then-retry sequence (`q`, `p`, `h` in AC-3; `s` in AC-4) declares no
   `outputs`, documented inline at each site — no engine or `_GatedExecutor` change needed.
3. **`_ScriptedGatedExecutor` is a composition wrapper around `_GatedExecutor`, not a subclass.**
   `tests/test_wave_scheduler.py` is never edited (0 deletions, confirmed via `git diff
   --numstat`) — the wrapper delegates gate/release/entered-event/raise_for/bookkeeping to an
   internal `_GatedExecutor()` instance unchanged, and only intercepts the *returned* `TaskResult`
   when a script is queued for that task id, per the ticket's explicit "reuse it... do not build a
   competing one."
4. **`mypy` scoped to `src` per the ticket's own DoD** (not `mypy .`), matching `T-j8YLGd`'s
   precedent. Running `mypy` directly against an isolated test file (outside that invocation)
   produces `import-untyped`/`unused-ignore` noise identical for both the new file and the
   pre-existing, already-passing `test_wave_scheduler.py` — a mypy single-file-invocation artifact
   (package resolution context), not a real type regression; not part of this ticket's mypy gate.

## Evidence
- Files changed: `src/agent_orchestrator/engine.py` (`_prepare_and_maybe_dispatch`: new
  `in_flight_nonempty` parameter + docstring; budget-gate block rewritten from an internal
  while-loop to a single gate check with the BLOCKED/sleep branch split on `in_flight_nonempty`;
  `run()`'s fill-loop call site passes `in_flight_nonempty=bool(in_flight)` and the stale
  "never actually returns BLOCKED" comment replaced). `tests/test_wave_concurrency_semantics.py`
  (new file, 8 tests: `TestBudgetCapUnderConcurrency` ×2, `TestNoBudgetDeadlockOnRollingWindow` ×1,
  `TestRequeueWithSiblingInFlight` ×3, `TestBreakerHaltDrainsInFlight` ×1,
  `TestCancelDrainsInFlight` ×1).
- `uv run pytest tests/test_engine*.py -q` — **58 passed** (N=1 gate; zero edits to any existing
  test body).
- `uv run pytest -q` (full suite) — **845 passed, 3 skipped** vs. the 837 passed / 3 skipped
  baseline (T-j8YLGd's handoff point): **+8, zero regressions.**
- `tests/test_wave_concurrency_semantics.py` re-run **13 times total** across the session (8
  consecutive + 5 more after a final cleanup edit) — **zero flakes**, all fast (no real sleeps;
  fixed/advancing fake clocks + recording/no-op sleepers throughout).
- `uv run ruff check .` — 2 pre-existing errors, both in untouched `tests/test_e2e_cli.py`
  (`E501` line 209, `F841` line 270) — identical to `T-j8YLGd`'s baseline. Zero errors on every
  file this ticket touched.
- `uv run ruff format --check .` — all 71 files clean (including both touched/added files).
- `uv run mypy src` — 4 pre-existing errors, all in untouched `src/agent_orchestrator/
  _version.py` lines 24-27 (`[assignment]`). Zero errors on `engine.py`.
- `git status --short -- specs` — empty (zero schema changes).
- `git diff --numstat tests/` — `test_cli.py`/`test_engine.py`/`test_project_config.py` show only
  the pre-existing `T-JXiI9j` additions (0 deletions); `test_wave_scheduler.py` and
  `test_wave_concurrency_semantics.py` are new, untracked files — `test_wave_scheduler.py` itself
  was never opened for editing.

## Risks / Blockers
- No blockers. R3 (budget-wait deadlock) is now closed — AC-1/AC-2 construct and pass the exact
  scenario. R4 (breaker count nondeterminism at `N>1`) remains accepted/documented per EPIC.md —
  not something this task attempts to make deterministic (design doc §11 / ticket Risks: "Do NOT
  try to force determinism by serializing failures — only barriers serialize").
- Forward note for `T-TNleFt`: the `_ScriptedGatedExecutor` pattern (composition wrapper adding
  scripted per-call `TaskResult` outcomes around `_GatedExecutor`) is available for reuse if the
  full interaction-matrix harness needs scripted failure/quota/429 outcomes alongside gating: it
  currently lives in `tests/test_wave_concurrency_semantics.py`; promoting it to `conftest.py`
  would need `test_wave_scheduler.py`'s `_GatedExecutor` promoted alongside it (neither was moved
  here to keep this task's diff to "additions only" on existing test files).

## Next actions
1. Handed off to `T-TNleFt-tests-parallel-matrix` (gated-executor harness promotion, `N=1`
   regression gate consolidation, full interaction-matrix integration tests, CliRunner e2e for
   `--max-parallel`).
2. `T-EJKD6f-docs-adr-reconcile` remains last (reconcile `docs-md/parallel-execution-hld.md` /
   ADR-0007 to as-built once `T-TNleFt` lands).
