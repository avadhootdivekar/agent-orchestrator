# Epic: E-3JTmVu-breaker-resume-extend

## Metadata
- Epic ID: `E-3JTmVu-breaker-resume-extend`
- Title: Circuit-breaker resume extensions — `run_active_seconds` condition + operator threshold bump/un-latch
- Owner: dev-epic agent
- Created: 2026-07-14
- Last Updated: 2026-07-14
- Status: Done
- Foundation / prior art: [`E-rc7k2v-run-control-routing-breakers`](../../meta/tickets/E-rc7k2v-run-control-routing-breakers/EPIC.md) (Done, 2026-07-09) — this epic is an additive follow-on, NOT a reopening. `E-rc7k2v` is not touched/reopened.

## Goal
`E-rc7k2v` shipped a pluggable circuit-breaker framework with a per-breaker-id latch
(`state.tripped_breakers`) that is permanent for the life of a run — recorded at most once,
ever, per id. Two follow-on gaps were identified but explicitly deferred at the time:

1. `run_wall_clock_seconds` measures elapsed time from the run's ORIGINAL `started_at`
   (breakers.py docstring ~line 224-231: a documented "deadline" semantic). A run paused for
   hours/days and resumed counts the entire pause as elapsed and can trip immediately at the
   very next task boundary — there is no way to measure "time actually spent executing," only
   "time since the run was first created."
2. `T-t4m8x1`'s STATUS.md entry (`E-rc7k2v`) explicitly flagged: "the always-latched case is a
   real gap for whoever owns resume+breaker semantics next" — once a breaker id has recorded a
   trip, `evaluate_breakers` skips it forever (even across `ao resume`), regardless of whether
   its condition is still true, and there was previously no operator lever to un-latch it.

This epic closes both gaps: a new `run_active_seconds` breaker condition (immune to
pause/resume gaps by construction) and a scoped, resume-time `--extend-breaker` CLI mechanism
that lets an operator explicitly bump a *specific* tripped breaker's threshold and un-latch it —
without regressing the existing (intentional) latch behavior for every other, non-extended
breaker.

## Design status (early gate)
The design itself was fully resolved by the requester before this epic started (FR-1/FR-2 below
are specified to the level of exact class/function signatures, call sites, and un-latch
semantics) — this is explicitly "implement as specified, do not re-litigate the design." The
orchestration flow is unchanged: no new spec→DAG→execution→artifact→trigger path is introduced,
only (a) one additional breaker condition evaluated at the existing task-boundary hook, and (b)
a resume-time mutation of already-persisted `RunState` fields before the existing `orch.run()`
call. Given that scope, the "early gate" is satisfied by:
- Inheriting `E-rc7k2v`'s own already-reviewed orchestration design (breaker framework, task
  boundary hook, resume/prepare_resume flow) — no change to those contracts.
- A self-review pass (this document, before implementation) confirming the two changes are
  additive and centrally resolved (one `evaluate_breakers` change point for overrides, per the
  spec's own DRY requirement), not scattered special-cases.
- A `reviewer` pass on the actual diff before the epic is declared Done (late gate below).

## Acceptance Criteria (testable)
1. `run_active_seconds` is a registered `Breaker` in `BREAKER_REGISTRY`, accepted by
   `specs/workflow.schema.json`'s `condition` enum, and sums `(ended_at - started_at)` over every
   settled task in `RunState.tasks` — proven immune to a large gap between two tasks' boundaries
   (a test where `run_wall_clock_seconds` trips but `run_active_seconds` does not, same fixture).
2. `RunState.breaker_overrides: dict[str, float]` defaults to `{}` (NFR-5 backward-compat, same
   pattern as `tripped_breakers`/`route_decisions`).
3. `apply_breaker_extension(...)` computes the new threshold (`current_effective + delta`),
   stores it in `breaker_overrides`, removes matching `TrippedBreaker` record(s) (un-latch), logs
   a `breaker.extend` event, and raises on the both-given/neither-given mutual-exclusivity error.
4. `evaluate_breakers` resolves `state.breaker_overrides.get(spec.id, spec.threshold)` ONCE,
   centrally, and evaluates every breaker against that effective threshold — proven to work
   uniformly (not a wall-clock-only special case).
5. `ao resume --extend-breaker <id> --extend-by-seconds <f>` / `--extend-by-same` mutates the
   loaded `RunState` before `orch.run()`, prints an old→new confirmation line, and the mutation is
   persisted (same `state.json` the resumed run itself saves to).
6. Non-extended breakers' latch behavior is provably unchanged: `tests/test_resume_replay.py`
   and `tests/test_stop_reframe_parity.py` stay green, byte-for-byte, with zero new failures.
7. A CliRunner end-to-end test proves: a breaker already latched-tripped can be extended via
   `ao resume`, the run then proceeds past the point it previously stopped, and the SAME (or a
   freshly-manufactured) extended breaker can trip again later once its new, larger threshold is
   also exceeded.
8. `uv run pytest -q` stays fully green (exact before/after counts recorded below); `ruff check`,
   `ruff format --check`, `mypy` clean on every touched file.

## Requirements

### MVP — Functional
- FR-1: New breaker condition `run_active_seconds` — schema enum entry + registered
  `RunActiveSecondsBreaker(Breaker)` summing settled-task `(ended_at - started_at)` deltas.
  Verification: unit tests in `tests/test_run_active_seconds_breaker.py`.
- FR-2a: `RunState.breaker_overrides: dict[str, float] = {}` persisted field (NFR-5 defaulted).
  Verification: model round-trip test + old-fixture-resume test.
- FR-2b: `apply_breaker_extension(state, spec, *, extend_by_seconds, extend_by_same) -> float`
  standalone function in `breakers.py` (mirrors `record_trip`'s standalone style): computes
  effective new threshold, writes the override, un-latches matching `tripped_breakers` entries,
  logs `breaker.extend`, returns the new threshold; raises on invalid arg combos.
  Verification: isolated unit tests (both extend paths + error case) in
  `tests/test_breaker_extension.py`.
- FR-2c: `evaluate_breakers` resolves the effective threshold centrally via
  `state.breaker_overrides.get(spec.id, spec.threshold)` and evaluates a `model_copy`'d spec —
  uniform across every condition, current and future. Verification: integration test proving an
  override changes trip behavior for an arbitrary (non-wall-clock) condition too.
- FR-2d: `ao resume --extend-breaker <id> [--extend-by-seconds <f> | --extend-by-same]` — validates
  the id exists in `wf.circuit_breakers`, validates exactly one of the two extend flags, applies
  the extension to the loaded `RunState` before `orch.run()`, echoes the old→new confirmation
  line, persists the mutation. Verification: CliRunner end-to-end test.

### MVP — Non-functional
- NFR-1 (regression safety): `tests/test_resume_replay.py` + `tests/test_stop_reframe_parity.py`
  stay green unmodified in behavior (only additive test files/functions elsewhere) — proves
  non-extended breakers' latch-forever behavior is untouched.
- NFR-2 (determinism): `RunActiveSecondsBreaker` reconstructs purely from `state.tasks[*]
  .started_at/ended_at` (same pattern as the existing `_consecutive_failure_streak` pure
  reconstruction) — no new persisted bookkeeping, resume-safe by construction.
- NFR-3 (DRY / single resolution point): override resolution lives in exactly one place
  (`evaluate_breakers`), never duplicated per-`Breaker`-subclass.
- NFR-4 (observability): `breaker.extend` is a structured log event mirroring `record_trip`'s
  `breaker.trip` logging convention (same `extra={...}` shape style).
- NFR-5 (backward-compat): old `state.json` without `breaker_overrides` loads fine via the
  pydantic default.

### Non-MVP (deferred, per explicit user instruction — do not build)
- D-1: Live/in-process threshold mutation for a currently-running `ao run`/`ao resume`
  invocation (no hot-reload of `WorkflowSpec`, no `stop_file`-style live-control-file poll for
  breaker thresholds). Revisit only if an operator needs to extend a breaker without stopping
  the process first.
- D-2: A blanket "un-latch every breaker on every resume" — deliberately rejected because it
  would regress the existing, intentional "resumed run doesn't immediately re-trip on a
  still-true condition" resumability behavior for every OTHER (non-extended) breaker.

## Traceability (requirement -> task)
| Requirement | Task |
|---|---|
| FR-1, NFR-2 | `T-69MnaW-run-active-seconds-breaker` |
| FR-2a, FR-2b, FR-2c, NFR-3, NFR-4, NFR-5 | `T-yX1Oi5-breaker-extend-core` |
| FR-2d | `T-nVWE1W-resume-extend-breaker-cli` |
| NFR-1 (full-suite regression proof), docs | `T-gzG0EI-docs-and-verification` |

## Task Decomposition
| Task | Description | Requirements | Est. |
|------|-------------|--------------|------|
| `T-69MnaW-run-active-seconds-breaker` | `run_active_seconds` schema enum entry + `RunActiveSecondsBreaker` + registry + unit tests (incl. the pause-gap differentiator vs `run_wall_clock_seconds`) | FR-1, NFR-2 | 1 day |
| `T-yX1Oi5-breaker-extend-core` | `RunState.breaker_overrides` + `apply_breaker_extension` + `evaluate_breakers` centralized override resolution + isolated unit tests + regression proof (existing resume/stop-reframe suites still green) | FR-2a/b/c, NFR-1, NFR-3, NFR-4, NFR-5 | 1.5 days |
| `T-nVWE1W-resume-extend-breaker-cli` | `ao resume --extend-breaker/--extend-by-seconds/--extend-by-same` CLI wiring + validation + CliRunner e2e test | FR-2d | 1 day |
| `T-gzG0EI-docs-and-verification` | LLD addendum, `breakers.py` module docstring bump, schema validation test, `reviewer` pass, full-suite final verification with before/after counts | NFR-1, docs | 0.5 day |

## Evidence Log

### 2026-07-14 — Iteration 1 (context read, plan locked)
- Read `E-rc7k2v` STATUS.md in full (all 12 task entries), `breakers.py`, `models.py`
  (`BreakerCondition`/`CircuitBreakerSpec`/`TrippedBreaker`/`RunState`/`TaskRunState`),
  `specs/workflow.schema.json`'s `circuitBreaker` `$def`, `cli.py`'s full `resume` command body
  (lines 583-742, including the ~40 lines the user flagged), `engine.py`'s `evaluate_breakers`
  call site (~line 770) and `started_at`/`ended_at` assignment sites (462/679, real
  `datetime.now(UTC)`, not the injected clock), `runstate.py`'s `new_run`/`prepare_resume`, and
  the existing test style in `tests/test_mvp_breaker_conditions.py`,
  `tests/test_stop_reframe_parity.py`, `tests/test_resume_replay.py`,
  `tests/test_routing_breaker_models.py` (schema validation convention).
- Confirmed no re-derivation needed: every signature/behavior the user specified matches what is
  actually in the code (evaluate_breakers call site, RunState field list, resume command
  structure) — no BLOCKED assumptions needed.
- Ticket structure created under `meta/tickets/E-3JTmVu-breaker-resume-extend/` (this epic + 4
  tasks). Baseline `uv run pytest -q` captured: **643 passed, 3 skipped**.

### 2026-07-14 — Iteration 2 (FR-1 + FR-2 implemented, reviewer pass, 2 real bugs found + fixed)
- FR-1 (`run_active_seconds`): `BreakerCondition` Literal + schema enum + threshold-required
  `allOf` entry; `RunActiveSecondsBreaker`/`_settled_task_active_seconds` in `breakers.py`,
  registered in `BREAKER_REGISTRY`. `tests/test_run_active_seconds_breaker.py` (12 tests).
- FR-2 (`breaker_overrides` + `apply_breaker_extension` + centralized override resolution in
  `evaluate_breakers` + CLI `--extend-breaker`/`--extend-by-seconds`/`--extend-by-same`):
  `tests/test_breaker_extension.py` (12 tests), `tests/test_resume_extend_breaker_cli.py` (7
  tests), plus 2 new cases in `tests/test_routing_breaker_models.py`.
- Requested a `reviewer` pass on the full diff (agent id `a862873eeae56a8a8`) before declaring
  done, per the epic's own late-gate requirement. The reviewer independently verified 2
  **Critical** bugs with reproduction, not just inference:
  1. `run_active_seconds` silently dropped quota-exhaustion wait time: `engine.py`'s single
     `ts.started_at` writer was unconditionally reset on every quota/429/budget-wait
     redispatch of the SAME task (`cursor -= 1; continue` loops back through the same write
     site), contradicting FR-1's explicit, stated semantic that in-task waits DO count.
  2. `ao resume --extend-breaker` could silently fail to persist: the extension block ran
     BEFORE other CLI-only validation (e.g. `--on-exhaustion`), which can `typer.Exit(1)`
     before `orch.run()` — so an already-logged/confirmed extension could be discarded with
     zero trace if an unrelated flag was also bad.
  Plus one **Warning** (no sign check on `--extend-by-seconds`, bypassing the schema's
  `exclusiveMinimum: 0` invariant) and a ticket-hygiene note (epic rollup docs stale — being
  fixed in this same update).
- All three fixed:
  1. `engine.py` ~:462: guarded `ts.started_at` write to fire only when `ts.started_at is
     None` (task's first-ever dispatch). Verified minimal/safe blast radius first
     (`grep -rn "\.started_at"` — exactly one writer, one reader: my new breaker; nothing
     else in the codebase depends on the reset-per-redispatch behavior). `prepare_resume`
     already hands a fresh `TaskRunState()` to any task reset for `ao resume`, so resumed
     dispatches still get a fresh `started_at` — only same-process wait-and-redispatch loops
     are affected. New integration test
     `tests/test_run_active_seconds_breaker.py::TestRunActiveSecondsCountsQuotaWaitTime`
     drives a real `Orchestrator` + `FakeExecutor(quota_exhausted_tasks=...)` through 2
     quota-exhaustion redispatches (monkeypatching `engine.py`'s `datetime.now` to a
     controlled stepping fake, since `started_at`/`ended_at` use the real clock, not the
     injectable one) and proves `started_at` survives both redispatches.
  2. `cli.py`: added an explicit `rs_store.save(existing)` immediately after
     `apply_breaker_extension` returns, rather than relying solely on `orch.run()`'s later
     save. New test
     `tests/test_resume_extend_breaker_cli.py::TestExtendBreakerEndToEnd::test_extension_persists_even_if_a_later_unrelated_flag_fails_validation`
     proves the extension survives a subsequent bad `--on-exhaustion` value.
  3. `apply_breaker_extension` now rejects a non-positive `extend_by_seconds` with
     `SpecValidationError`. New parametrized test in `tests/test_breaker_extension.py`.
- Full regression re-run after both engine.py and cli.py fixes: `uv run pytest
  tests/test_resume_replay.py tests/test_stop_reframe_parity.py tests/test_engine_budget.py
  tests/test_breakers.py tests/test_engine_breakers.py tests/test_mvp_breaker_conditions.py -q`
  -> **101 passed**, zero regressions (these are the suites most likely to catch a
  latch/retry-loop regression, including the quota/429/budget-wait-heavy
  `test_engine_budget.py`).
- LLD addendum (`docs-md/lld-run-control-routing-breakers.md` §16) updated with both fixes'
  rationale, so the doc doesn't just describe the original (partially-buggy) design.
- Full suite: `uv run pytest -q` -> **676 passed, 3 skipped** (643 baseline + 33 new tests, zero
  regressions). `ruff check .` / `ruff format --check .` clean repo-wide (2 pre-existing findings
  in untouched `tests/test_e2e_cli.py`, confirmed unrelated). `mypy .` -> 10 errors, all in
  untouched files (`_version.py`, `hatch_build.py`, `test_executor.py`, `test_engine_budget.py`,
  `test_project_config.py`) — identical to the pre-existing baseline debt noted by prior
  `E-rc7k2v` tickets (T-w6p2c8/T-d8w4v2 STATUS.md entries); zero new mypy issues introduced.

## Risks & Blockers
- None blocking. The design risk flagged in Iteration 1 (regressing existing latch semantics for
  untouched breakers) did not materialize — proven by the unmodified regression suites staying
  green. Two REAL implementation bugs were found by the reviewer pass (not a design risk, an
  execution gap) and are now fixed with dedicated regression tests (see Iteration 2 above).

## Next Actions
1. None — all 4 tasks Done, reviewer findings addressed, full suite green. Final handoff below.
