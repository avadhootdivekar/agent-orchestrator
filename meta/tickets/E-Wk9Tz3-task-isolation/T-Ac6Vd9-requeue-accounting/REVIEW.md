# REVIEW: T-Ac6Vd9-requeue-accounting (E-Wk9Tz3-task-isolation)

- Reviewer: reviewer-agent
- Date: 2026-09-07
- Scope: `src/agent_orchestrator/budget.py`, `src/agent_orchestrator/engine.py` (delta vs HEAD
  `340d825`, verified via `git diff -- src/agent_orchestrator/engine.py
  src/agent_orchestrator/budget.py`), `tests/test_engine_isolation_accounting.py` (new),
  `tests/test_budget.py`, `tests/test_engine_budget.py`, this task's `TASK.md`/`STATUS.md`.
  `T-Cx4Jf1` part A and `T-Rm2Lx7` files were not opened/edited, per instruction.

## Verdict: **APPROVE WITH CHANGES**

Must-fix: **W-1** (non-deterministic ~30s real sleep in a new test — direct violation of
CLAUDE.md's fixed-clock testing rule, trivial one-line fix). Everything else is a should-fix or
a disclosure/traceability nit; nothing here blocks merge on correctness or safety grounds.

## Summary

This is a tight, well-scoped fix. The two accounting defects (R-1a cross-call actuals loss,
R-1b one-shot-per-task-id ledger latch) and the capture-directory clobber (R-21) are each fixed
correctly, with the cycle-keyed ledger backward-compatible by construction (`cycle: int = 1`
default, verified against every real caller — there are no other `BudgetManager` subclasses in
the repo). The new test file drives the conflict ladder through the real engine via the
established `_ScriptedIntegrator` double (not by poking internals), and specifically asserts
against `BudgetCounters` fields, not just `TaskRunState.cumulative_*`, exactly per the ticket's
own warning about how this defect class survives review. The developer also found and
transparently disclosed a real, pre-existing gap in frozen `runstate.py` (see Major-1) rather
than silently working around it or hiding it. Alignment with project/epic goals, HLD §10.3/§13,
and CLAUDE.md's no-duplicate-logic rule is good. All three review gates were re-run
independently and match the developer's reported numbers.

## Verification performed (independent, not trusted from STATUS.md)

- `git diff -- src/agent_orchestrator/engine.py src/agent_orchestrator/budget.py` read in full
  against HEAD `340d825`; confirmed the diff matches this task's file-ownership boundary exactly
  (`git status` shows no other file under this ticket's control touched).
- Read `_settle_completed_task` (lines 1219-1738) and `_prepare_and_maybe_dispatch` (787-1217) in
  full to confirm control flow, not just the diff hunks in isolation — in particular that the
  unconditional `self._accumulate_actuals(ts, result)` call (line 1549) genuinely runs *before*
  the `conflict_resolver`/`conflict_rerun` switch (line 1673+) that returns `"requeue"`.
- Traced `dispatch_cycle`'s single increment site (engine.py:864) and cross-checked
  `models.py`/`runstate.py` (frozen, not edited by this diff) to verify the developer's claims
  about `prepare_resume`'s carry-forward behavior.
- `uv run ruff check` / `ruff format --check` on the 5 scope files: clean.
- `uv run mypy src`: exactly 4 pre-existing `_version.py` errors, 0 new — matches STATUS.md.
- `uv run pytest tests/test_engine.py tests/test_engine_isolation.py
  tests/test_engine_isolation_accounting.py tests/test_engine_budget.py tests/test_budget.py
  tests/test_budget_integration.py tests/test_engine_breakers.py tests/test_e2e_cli_isolation.py
  tests/test_e2e_monitoring_cli.py tests/playground -q --durations=10`: **195 passed, 3
  skipped**, 36.4s wall — matches the "targeted suite" gate. (Full-suite-with-coverage run was
  not re-executed; STATUS.md's 3446/7 skipped/0 failed figure is taken on trust for that one run,
  consistent with the targeted subset actually re-run here.)
- Grepped the whole `src/` tree for other readers of `charged_estimate`/`reconciled_tasks`/
  `reconciled_cycles` and for other `BudgetManager` subclasses — none exist outside
  `budget.py`/`engine.py`/`models.py`, so the "superset/backward-compatible" claim has no
  untested blind spot.
- Grepped `ui/runs.py` and `cli.py` for capture-path parsing — confirmed `output_artifact_path`
  is surfaced as an opaque string in both, so the two-shape capture-directory rule (flat cycle 1,
  nested cycle 2+) genuinely needs no reader-side change today, as STATUS.md claims.

## Critical (block merge)

None.

## Major (should fix before merge, or explicitly tracked if deferred)

**Major-1 — `TaskRunState.cumulative_*` does not survive a genuine crash + `prepare_resume` for
a task that requeued.** `runstate.py:255-266` (frozen, out of this ticket's file scope) replaces
a non-terminal task's `TaskRunState` wholesale on resume, carrying forward only `status` and
(by this ticket's own hand-added line 265) `dispatch_cycle` — every other field, including
`cumulative_input_tokens`/`cumulative_output_tokens`/`cumulative_cost_usd`, resets to 0.
Concretely: task cycle 1 completes and reconciles (actuals accumulated into
`ts.cumulative_*`), a conflict-ladder requeue puts it into cycle 2 ("running"), the process
crashes before cycle 2 settles. On resume, `ts.status == "running"` is neither `"succeeded"`
nor `"not_taken"` nor `"pending"`, so it hits the wholesale-replace branch and cycle 1's
already-accumulated actuals are wiped from the per-task view. The *run-level* ledger
(`RunState.budget_counters.consumed_tokens`, used by breakers/rate-window gating) is unaffected
— it is never rebuilt by `prepare_resume` — so this is a display/observability gap, not a
double-charge or under-charge of the actual budget enforcement path.
- Evidence this was found deliberately, not missed: `tests/test_engine_isolation_accounting.py`
  `TestResumeMidCycleTwo::test_resume_after_crash_mid_cycle_two_reconciles_each_cycle_exactly_once`
  (lines 582-701) reproduces exactly this scenario and asserts both halves — the (correct, wiped)
  `ts.cumulative_input_tokens == 0` after resume, and the (correct, durable) `bc.consumed_tokens
  == 150`. STATUS.md's "Risks" section documents it with the same reasoning.
- Why this is still Major, not just an accepted risk: the ticket's own epic-level claim (R-1,
  carried into this ticket's brief) is "requeues never discard cost/token accounting, cumulative
  across dispatch cycles" — that promise silently narrows to "within one continuous process" the
  moment a crash+resume is involved, and nothing outside STATUS.md prose records that narrowing
  where a future reader (operator, or `T-Cx4Jf1`'s CLI status display) would find it before
  shipping a per-task cost view that trusts `TaskRunState.cumulative_*` after a resume.
- Fix: not in this ticket (file is frozen). Concrete next step: file an actual tracked ticket
  under `meta/tickets/E-Wk9Tz3-task-isolation/` now (STATUS.md's "Next actions" item 3 currently
  says "Unassigned follow-up" — that is not a ticket) so `T-Cx4Jf1`/dashboard work knows
  per-task cost display is not resume-durable, and so this doesn't quietly become tribal
  knowledge. Low urgency to implement (the durable ledger is correct), but should be tracked
  before this epic is called done.

## Warnings (should fix)

**W-1 — new self-heal test sleeps ~30 real seconds; violates the project's fixed-clock testing
rule.** `tests/test_engine_isolation_accounting.py::TestCumulativeAccumulationAcrossTheLadder::
test_self_heal_requeue_path_accounted` (line 351) constructs
`Orchestrator(usage, store, rs_store, self_heal_enabled=True)` without a `sleeper=` override, so
`_consult_task_failure_heal`'s retry wait goes through the real `time.sleep` default
(`engine.py:434`). Confirmed by direct measurement: this single test took 30.01s in the targeted
run, by far the slowest test in the whole 195-test suite (next slowest: 0.32s). Every other
self-heal test in the repo (`tests/test_monitoring_self_heal.py`, 10+ call sites) injects
`sleeper=lambda _s: None` or `sleeper=sleeps.append` for exactly this reason — this is an
established, easily-discoverable convention this new test didn't follow (CLAUDE.md: "For
anything stochastic or scheduled, use fixed seeds / fixed clocks so assertions are deterministic
and replayable"; "Follow existing patterns in adjacent files before introducing new
abstractions"). Fix: add `sleeper=lambda _s: None` to the `Orchestrator(...)` call at line 351.
No assertion in the test depends on wall-clock time, so this is a pure win with no test-value
loss.

**W-2 — `cycle` field addition to `budget.*` log events is unverified by any test.**
`engine.py`'s `budget.charge`/`budget.reconcile`/`budget.resume_reverse` `run_log`/`task_log`
calls all gained a `"cycle": ...` key in their `extra` dict (e.g. engine.py:1168, 1469,
resume_reverse block). Read and confirmed correct by inspection, and no existing
`caplog`-based test broke (the targeted suite passed, including the e2e-playground tests that
assert on `budget.charge`/`budget.reconcile` event *names*). But nothing in the new test file
asserts the `cycle` field's *value* is actually present and correct in a log record — Challenge
#6 in this review's brief calls this out explicitly, and it's the kind of thing that silently
regresses (e.g. a future refactor could drop the kwarg without any test noticing). Low cost to
add: one `caplog.at_level(logging.INFO)` assertion in one of the existing ladder tests would
close this. Not blocking — correctness was verified by code reading — but worth a follow-up if
someone is touching this test file again soon.

## Minor / Nits

**Minor-1 — `f"cycle-{cycle}"` in `engine.py:3120` (approx.) is an inline literal, not a named
constant**, unlike `_CYCLE_KEY_SEP = "#"` in `budget.py` which correctly followed the
no-magic-literals rule. Low priority: it's a single occurrence, heavily commented, and matches
the pre-existing (also inline) `"attempt-{n}"` precedent it sits next to — but for symmetry with
`budget.py`'s own constant and to give the two-shape rule one greppable anchor, consider hoisting
it to a `_CYCLE_DIR_PREFIX = "cycle-"` module constant the way `_CYCLE_KEY_SEP` was.

**Minor-2 — `reconciled_cycles`/`reconciled_tasks` are `list[str]` with `in`/`.append()`
idempotency checks, O(n) per call.** Not a regression — `reconciled_tasks` already had this
shape pre-diff, and this ticket's `reconciled_cycles` field is frozen (ships with `T-Sc7Rm2`,
`models.py` not owned by this ticket) — so there was no opportunity to change the type here even
if desired. Flagging only because a workflow with many redispatched tasks will scale this
linearly; likely fine at realistic run sizes, not worth a change now.

## Dimension-by-dimension checklist

- **Project goals**: declarative specs unaffected (no schema change); DAG untouched; pluggable
  boundary (`BudgetManager` ABC) extended in a backward-compatible way; determinism preserved
  (no new `random`/wall-clock reads on the accounting path itself — see W-1 for the *test's* own
  clock hygiene, which is a different concern); resumability is the one nuanced area (Major-1);
  observability slightly improved (new `cycle` fields on existing events, see W-2). Aligned.
- **Epic/task goals**: TASK.md's 10 ACs were checked one-by-one against code + tests (AC-1
  through AC-9 all directly verified via reading the settle switch order and running the
  matching tests; AC-10 gates re-run independently above). All satisfied. No scope creep
  detected — `models.py`, `runstate.py`, `cli.py`, `spec.py`, `isolation/` genuinely untouched
  (confirmed via `git status`), matching the "Do NOT touch" list exactly.
- **Code-level intent**: extensive docstrings/comments accurately describe the actual code in
  every case spot-checked (the `_stale_cycle = dispatch_cycle - 1` reasoning, the
  "`_accumulate_actuals` runs before the conflict switch" claim, the `ui/runs.py`
  opaque-string claim) — no comment found to be stale or aspirational.
- **SOLID/KISS**: `_accumulate_actuals` is a clean, minimal extraction (one static method, one
  new call site added, one pre-existing call site rewired) — no over-abstraction. `cycle_key()`
  is a small pure function, correctly exported for `engine.py`'s membership-check use case
  rather than adding a second ABC method just for that.
- **DRY**: verified no third accumulation copy exists — `_run_with_retries`'s own `cum_*` locals
  (engine.py:3127-3188) are a *different*, pre-existing, within-call concern (feeding
  `result.*`), not a duplicate of `_accumulate_actuals` (which is strictly cross-call). Good.
- **No magic literals**: `_CYCLE_KEY_SEP` named; see Minor-1 for the one inline literal that
  wasn't.
- **Pluggable architecture**: `BudgetManager` ABC's new `cycle` parameter is additive with a
  safe default; no other implementation exists to break, and the default-value approach is
  exactly right for the stated "any third-party implementation keeps working" goal.
- **Spec & DAG correctness**: N/A — no schema/DAG change in this diff.
- **Determinism & resume safety**: ledger is resume-safe and tested (own crash+resume test, plus
  the rewritten `test_stale_estimate_reversed_on_resume`); the one gap is Major-1 above, on the
  per-task display field, not the enforcement path. `random`/wall-clock: none introduced on the
  accounting path; W-1 is about the *test's* sleeper, not production code.
- **Errors & logging**: no swallowed errors introduced; `budget.*` log lines remain one
  authoritative log per event (charge/reconcile/resume_reverse), each now cycle-tagged; no new
  duplicate logging spam.
- **Testability**: all new logic is exercised through the public `Orchestrator.run()` /
  `DefaultBudgetManager` surfaces via injected doubles (`_UsageExecutor`, `_ScriptedIntegrator`,
  fixed clock) — nothing required internals-poking.
- **Concurrency/rollout**: N=1 serial engine only exercised here (matches existing suite's
  scope); no new shared-mutable-state concern introduced — `state.budget_counters` mutation is
  still main-thread-only, consistent with ADR-0007 D3 (verified no charge/reconcile call was
  added on a worker-thread path).

## Testing notes

- **What's already well-mocked**: executor (`_UsageExecutor`, fixed usage per call — genuinely
  deterministic), integrator (`_ScriptedIntegrator`, scripted outcome queue), clock
  (`_FIXED_DT`), budget manager (real `DefaultBudgetManager`, appropriately NOT faked since it's
  the unit under test).
- **What to mock going forward**: nothing new required; W-1's fix (`sleeper=lambda _s: None`) is
  the only mocking gap found.
- **Integration-test coverage**: the 3-cycle ladder tests are the right altitude — real engine,
  real DAG/dispatch loop, only the integrator and executor are doubled. Good use of
  integration-level tests for DAG/scheduling-adjacent logic per CLAUDE.md guidance.
- **Coverage gaps**: (1) Major-1's scenario is tested for the ledger but the finding itself
  (that `cumulative_*` resets) is asserted as a documented *characterization*, not guarded
  against regressing further — acceptable since fixing it is out of scope. (2) W-2: no
  assertion on log event field contents. (3) Not tested here (correctly out of scope): what
  `T-Cx4Jf1`'s CLI status printer or the dashboard would show for a resumed, multi-cycle task's
  cost — flagged for that ticket already in STATUS.md.

## Downstream hook-point check (T-Wl2Bq7 / T-Lr6Ka3 / T-Cx4Jf1 part B)

- `DispatchPrep.cycle`, `_run_and_integrate(..., cycle=...)`, `_run_with_retries(..., cycle=...)`
  and the `budget.*` event `cycle` field are all real, present, and exercised by tests — the
  "Interface (final signatures)" block in STATUS.md matches the actual code read line-for-line.
  `T-Lr6Ka3`'s stated plan to assert against `BudgetCounters.consumed_tokens`/`reconciled_cycles`
  directly is fully supported by what's shipped here.
- Capture-directory two-shape rule (flat cycle 1 / nested cycle 2+) is stated in exactly one
  place (`_run_with_retries`'s comment block, per AC-7's "state in one place" requirement) —
  confirmed, not duplicated elsewhere with a risk of drifting out of sync.

## Pre-submit checklist

- [x] Review scope confirmed: exactly the files named in the prompt; diffed against HEAD
  `340d825`; other in-flight tickets' files not opened.
- [x] Three alignment levels checked: project goals (CLAUDE.md), epic/task goals (TASK.md's 10
  ACs walked individually), code-level intent (docstrings/comments spot-checked against actual
  control flow).
- [x] All review dimensions walked; each stated as checked or flagged above.
- [x] Every finding cites file:line (or line range) + observation + why it matters + concrete
  next step.
- [x] Findings bucketed by actual severity: 0 Critical, 1 Major (disclosed, frozen-file,
  ledger-safe), 2 Warnings (1 must-fix test hygiene, 1 low-cost coverage gap), 2 Minor nits.
- [x] Testing notes included above.
- [x] No source/test edits made by this review; no commit created.
