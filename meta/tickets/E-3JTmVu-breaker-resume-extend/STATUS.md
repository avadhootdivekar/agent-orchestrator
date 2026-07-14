# STATUS

- ID: `E-3JTmVu-breaker-resume-extend`
- Updated At: 2026-07-14
- State: **Done** — all 4 tickets complete, reviewer pass done (2 critical bugs found + fixed),
  `uv run pytest -q` -> 676 passed, 3 skipped (643 baseline + 33 new, zero regressions), docs
  reconciled
- Owner: dev-epic

## This update (2026-07-14)
All four tasks landed in one continuous session (small epic, no cross-task blocking):

`T-69MnaW` (run-active-seconds-breaker) completed: `"run_active_seconds"` added to
`BreakerCondition` (models.py) and `specs/workflow.schema.json`'s `condition` enum +
threshold-required `allOf`; `RunActiveSecondsBreaker` + `_settled_task_active_seconds` landed in
`breakers.py`, registered in `BREAKER_REGISTRY`. Sums `(ended_at - started_at)` over every
settled task — a pure reconstruction from persisted timestamps (same pattern as the existing
`_consecutive_failure_streak`), immune to any operator pause/resume gap by construction (proven
against a same-fixture comparison where `run_wall_clock_seconds` trips but `run_active_seconds`
does not). `run_wall_clock_seconds` itself is byte-identical/untouched.

`T-yX1Oi5` (breaker-extend-core) completed: `RunState.breaker_overrides: dict[str, float] = {}`
(NFR-5 defaulted); `apply_breaker_extension(state, spec, *, extend_by_seconds, extend_by_same,
clock, run_log) -> float` (standalone, mirrors `record_trip`'s exact required-`clock`/`run_log`
calling convention) computes the new effective threshold, writes `breaker_overrides`, un-latches
the matching `TrippedBreaker` record(s), logs `breaker.extend`. `evaluate_breakers` resolves
`state.breaker_overrides.get(spec.id, spec.threshold)` ONCE, centrally, per spec — proven
uniform via a non-wall-clock condition (`task_failures`), not a special case. Regression proof:
`tests/test_resume_replay.py` + `tests/test_stop_reframe_parity.py` stayed green, unmodified
(19/19), throughout.

`T-nVWE1W` (resume-extend-breaker-cli) completed: `ao resume` gained `--extend-breaker <id>`,
`--extend-by-seconds <f>`, `--extend-by-same`. Unknown id / both-flags / neither-flag / flag-
without-id all exit 1 with a clear message. CliRunner end-to-end tests manufacture an
already-tripped `RunState` directly (the CLI has no way to inject a fake clock, unlike
engine-API tests) with `started_at` 2 real hours in the past, proving: the run continues past
the point it previously stopped after `--extend-by-seconds`; the SAME mechanism trips again on
the very next boundary via `--extend-by-same` when the doubled threshold is still exceeded by
real elapsed time.

`T-gzG0EI` (docs-and-verification) completed: LLD addendum (`docs-md/lld-run-control-routing-
breakers.md` §16) documenting both FR-1/FR-2 pieces; `breakers.py` module docstring bumped
(eight -> nine conditions + extension-mechanism note). **Reviewer pass requested** (late gate,
agent id `a862873eeae56a8a8`) on the full diff before declaring done — this is the key event of
this update:

The reviewer independently verified, with actual reproduction (not just code reading), **2
Critical bugs**:
1. `run_active_seconds` silently dropped quota-exhaustion wait time — `engine.py`'s single
   `ts.started_at` writer was unconditionally reset on every quota/429/budget-wait redispatch of
   the same task, directly contradicting FR-1's explicit "in-task waits DO count" requirement.
2. `ao resume --extend-breaker` could silently fail to persist — the extension block ran before
   other CLI-only validation (e.g. `--on-exhaustion`) that can `typer.Exit(1)` first, discarding
   an already-confirmed extension with zero trace.

Plus 1 Warning (no sign check on `--extend-by-seconds`, bypassing the schema's
`exclusiveMinimum: 0` invariant) and a ticket-hygiene note (this rollup was stale mid-epic —
fixed by this very update).

**All three fixed, each with a new regression-pinning test**:
1. `engine.py` ~:462 guarded to only set `ts.started_at` on a task's first-ever dispatch (blast
   radius confirmed minimal first: exactly one writer, one reader — the new breaker — in the
   whole codebase; `prepare_resume` already hands a fresh `TaskRunState()` on `ao resume`, so
   resumed dispatches are unaffected). New integration test drives a real `Orchestrator` +
   `FakeExecutor(quota_exhausted_tasks=...)` through 2 quota-wait redispatches and proves
   `started_at` survives both.
2. `cli.py` now calls `rs_store.save(existing)` immediately after `apply_breaker_extension`
   returns, rather than relying solely on `orch.run()`'s later save. New test proves the
   extension survives a subsequent bad `--on-exhaustion` value.
3. `apply_breaker_extension` now rejects non-positive `extend_by_seconds`.

Full regression re-run after both fixes: `uv run pytest tests/test_resume_replay.py
tests/test_stop_reframe_parity.py tests/test_engine_budget.py tests/test_breakers.py
tests/test_engine_breakers.py tests/test_mvp_breaker_conditions.py -q` -> **101 passed**, zero
regressions. Full suite: `uv run pytest -q` -> **676 passed, 3 skipped** (643 baseline + 33 new,
zero regressions). `ruff check .`/`ruff format --check .` clean repo-wide (2 pre-existing findings
in untouched `tests/test_e2e_cli.py`); `mypy .` -> 10 pre-existing baseline errors, all in
untouched files, zero new issues.

By: dev-epic · Role: developer · Date: 2026-07-14 · Comment: Epic **DONE**. All 4 tasks
complete; late-gate `reviewer` pass surfaced 2 real correctness bugs (not design risks) which
were fixed with dedicated regression tests before declaring completion — see each task's
STATUS.md for full per-task detail.

## Evidence
- Epic context: `docs-md/ai-epics/E-3JTmVu-breaker-resume-extend.md` (full evidence log,
  2 iterations).
- LLD addendum: `docs-md/lld-run-control-routing-breakers.md` §16 (includes both post-review
  fixes' rationale).
- Reviewer pass: subagent id `a862873eeae56a8a8` (see epic context doc Iteration 2 for the full
  findings summary).
- Test counts: baseline 643 passed/3 skipped -> final 676 passed/3 skipped (33 new tests across
  `tests/test_run_active_seconds_breaker.py` (12), `tests/test_breaker_extension.py` (12),
  `tests/test_resume_extend_breaker_cli.py` (7), `tests/test_routing_breaker_models.py` (+2)).
- Targeted regression re-run: 101 passed (test_resume_replay.py, test_stop_reframe_parity.py,
  test_engine_budget.py, test_breakers.py, test_engine_breakers.py,
  test_mvp_breaker_conditions.py).

## Risks / Blockers
- None. The design risk flagged at epic start (regressing existing latch semantics for untouched
  breakers) did not materialize. The two implementation bugs found by review are fixed and
  pinned by tests.

## Next actions
1. None — epic complete. Available for merge/PR at the user's discretion (not requested this
   session).
