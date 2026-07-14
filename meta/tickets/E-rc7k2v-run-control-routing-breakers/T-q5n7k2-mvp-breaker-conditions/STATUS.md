# STATUS

- ID: `T-q5n7k2-mvp-breaker-conditions`
- Updated At: 2026-07-09
- State: Done
- Owner: developer

## This update
Ticket created from LLD §7. Six MVP conditions; delivers `injected_task_count` (E2) to epic `E-gd8m4x`.

By: architect · Role: architect · Date: 2026-07-09 · Comment: Wave-3. Fixed-clock boundary tests per condition (memory-consistent with budget tests). `verdict` reuses the shared reader.

## Evidence
- Design: `docs-md/lld-run-control-routing-breakers.md` §7.

## Risks / Blockers
- Depends on T-x8v4d3 (framework), T-k9r3n8 (`read_bool_field`), T-b7q2m4 (specs).

## Next actions
1. Implement + register the six conditions.
2. Notify `E-gd8m4x` that `injected_task_count` (E2) is available.

---

## Completion (2026-07-09)

Implemented all six `Breaker` subclasses in `src/agent_orchestrator/breakers.py`
(the only `BREAKER_REGISTRY`-empty module T-x8v4d3 shipped) and registered them at
module scope so they are active as soon as the module is imported:
`TaskFailuresBreaker`, `ConsecutiveFailuresBreaker`, `RunWallClockSecondsBreaker`,
`VerdictBreaker`, `InjectedTaskCountBreaker`, `StopFileBreaker`. Module docstring
updated to drop the stale "ships with no MVP conditions" note.

Trip logic matches LLD §7's table exactly, with one added defensive convention
(not specified in the LLD, called out here as a judgement call): every breaker
treats its own required-but-`Optional` spec field (`threshold`/`task_id`+
`verdict_path`/`path`) as "never trips" rather than raising, if that field happens
to be unset — schema-level `conditional required` (LLD §2.1) guarantees it's set
for any spec that passed `ao validate`/`load_workflow`, so this only matters for a
`CircuitBreakerSpec` built directly (e.g. in a unit test) with the field omitted.

`consecutive_failures` is implemented via a pure helper,
`_consecutive_failure_streak(state)`, that sorts `state.tasks` by parsed
`ended_at` (excluding `ended_at is None`, which also excludes `not_taken`/
`pending`/`running`) and counts the trailing failed/timed_out run walking
backward from the most recent. No new persisted field — it is proven to
reconstruct correctly after a `RunState.model_validate_json(state.model_dump_json())`
round trip (resume simulation) in `tests/test_mvp_breaker_conditions.py`.

`verdict` short-circuits to "no trip" before ever calling `read_bool_field` unless
`spec.task_id`'s `TaskRunState.status == "succeeded"` — proven by monkeypatching
`read_bool_field` to raise `AssertionError` if called while the task is still
pending/failed. A genuinely unreadable/missing verdict file *after* real success
is deliberately left to propagate `ControlFileError` (a subclass of the existing
`OrchestratorError`, already caught at the CLI boundary around `orch.run(...)`)
rather than being swallowed into a silent "no trip" — this is the one place the
LLD table doesn't spell out the error path, and propagating (not catching)
matches CLAUDE.md's "never swallow errors" rule and the existing pattern of
other engine errors (e.g. `MissingInputError`, `InjectionError`) that aren't
locally caught at the `evaluate_breakers` call site in `engine.py` either.

`injected_task_count` is a direct `len(ctx.state.injected_tasks)` read — already
correct for nested-emission by construction, verified with a test that builds an
`injected_tasks` list mixing a top-level emitter's tasks with tasks representing
a second, nested emission wave.

`stop_file` calls `ArtifactStore.exists()` only; a test double
(`_ExistsOnlyStore`) that raises from `resolve()`/`size()` if either is ever
called proves no content path is touched.

**T-x8v4d3 test fixtures (guardrail check, no change needed):** both
`tests/test_breakers.py`'s `registry` fixture and
`tests/test_engine_breakers.py`'s `stub_condition` fixture already use targeted
`BREAKER_REGISTRY.pop(condition, None)` cleanup (never `.clear()` or a
save-as-empty-dict-and-restore pattern), and their stub condition names
(`test.always_trip`, `test.engine_boundary_stub`, etc.) never collide with the
six real MVP condition names — so both suites continued to pass unmodified once
the six real entries became permanently registered. No fixture changes were
required.

Tests added: `tests/test_mvp_breaker_conditions.py` (38 cases) — parametrised
below/at/above-threshold boundary tests for all six conditions (AC1), the
verdict-reader/short-circuit tests described above (AC2), a nested-emission
`injected_task_count` test (AC3), completion-order + resume-reconstruction +
streak-reset tests for `consecutive_failures` (AC4), and the existence-only
`stop_file` double (AC5). A `TestRegistryWiring` sanity test asserts all six
condition names are present in `BREAKER_REGISTRY` at import time.

Verification: `uv run ruff check .`, `uv run ruff format --check .`, and
`uv run mypy .` all clean on every file this ticket touched
(`src/agent_orchestrator/breakers.py`, `tests/test_mvp_breaker_conditions.py`);
pre-existing findings in untouched files (`engine.py`, `cli.py`,
`test_engine.py`, `test_e2e_cli.py`, `test_executor.py`, `test_engine_budget.py`,
`test_project_config.py`, `test_dynamic_injection.py`) predate this change and
belong to the parallel T-m2h5t7/T-w6p2c8 tickets currently in flight — out of
this ticket's scope, left untouched. `uv run pytest -q` → 516 passed, 3 skipped
(478 baseline + 38 new, all from `tests/test_mvp_breaker_conditions.py`) — zero
regressions, including in `test_breakers.py`/`test_engine_breakers.py`.

All 6 TASK.md acceptance criteria verified: (1) fixed-clock boundary
parametrised tests below/at/above threshold for all six conditions; (2)
`verdict` uses `read_bool_field` exclusively (spied, no second reader) and only
evaluates once `task_id` is settled succeeded; (3) `injected_task_count` counts
nested-emitted tasks by construction; (4) `consecutive_failures` uses
completion order via `ended_at`, resets on a trailing success, reconstructs
correctly after a JSON round-trip (resume simulation), no new persisted field;
(5) `stop_file` is existence-only, proven via a store double that raises if
content-adjacent methods are called; (6) `ruff`/`ruff format`/`mypy`/`pytest`
all clean.

By: developer · Role: developer · Date: 2026-07-09 · Comment: T-q5n7k2
mvp-breaker-conditions DONE. Six conditions implemented + permanently
registered exactly to LLD §7's trip-logic table, with the two judgement calls
(unset-optional-field defensiveness, `verdict`'s propagate-don't-swallow
`ControlFileError` policy) called out explicitly above since the LLD doesn't
spell them out. Zero changes needed to `engine.py`/`spec.py`/`cli.py` or to the
T-x8v4d3 test fixtures — scope stayed exactly to `breakers.py` + new tests as
directed.
