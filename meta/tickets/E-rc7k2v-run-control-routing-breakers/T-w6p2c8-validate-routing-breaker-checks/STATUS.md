# STATUS

- ID: `T-w6p2c8-validate-routing-breaker-checks`
- Updated At: 2026-07-09
- State: Done
- Owner: developer

## This update
Ticket created from LLD §4.4. Static routing + breaker checks in `ao validate`, incl. the R2 inferred-coupling rule.

By: architect · Role: architect · Date: 2026-07-09 · Comment: Wave-3. Must run AFTER `build_dag` (needs inferred edges). Turns ambiguous cones into named, actionable errors (contrast memory `emit-tasks-skip-validation`).

## Evidence
- Design: `docs-md/lld-run-control-routing-breakers.md` §4.4, §0-R2.

## Risks / Blockers
- Depends on T-c4w6p1 (cones/producer-map), T-b7q2m4 (specs).

## Next actions
1. Implement `validate_run_control(workflow, graph)` rules 1-11.
2. Positive + negative CliRunner tests for R2 + unreachable-endpoint.

---

## Completion (2026-07-09)

Note: a prior attempt at this exact ticket hit the platform session-limit error before making
any code change (confirmed via `git status`: zero diff, ticket docs untouched) — see the epic
rollup's T-m2h5t7 entry. This was a fresh, from-scratch implementation.

Implemented `validate_run_control(workflow: WorkflowSpec, graph: Graph) -> list[str]` in
`src/agent_orchestrator/spec.py`, covering all 11 LLD §4.4 rules, and wired it into
`cli.py::_load_all` immediately after `cross_validate(...)`:
`graph = build_dag(wf); graph.topological_order(); warnings = validate_run_control(wf, graph)`,
printed as `WARNING: ...` lines (same style as the existing project-config-load warning at
cli.py:53). Since `_load_all` is the single loader for `validate`/`run`/`resume`/`status`, this
runs uniformly across all four commands per the LLD's intent.

**Rules 1-4, 9, 10, 11** are direct reference/reachability/pattern checks (unknown router task,
unknown entry, entry-not-downstream-of-router, route-entry disjointness, nested-router rejection,
breaker id/condition/task_id refs, id pattern + reserved `__iter` marker reusing the existing
`_ITER_SUFFIX_MARKER` guard). **Rule 5 (R2, the important one)** groups `compute_cones`'
`membership` per-router, and for any task reachable from ≥2 routes of the SAME router, checks
each of its declared `inputs` for a producer (`graph.producer_of`) not present in `depends_on` —
any such producer is an *inferred* edge and fails naming the task, producer, and shared path.
**Rule 6** (WARN only) fires on the same per-router convergence-task loop when `join == "all"`
and rule 5 did NOT trip (i.e. the convergence is fully declared) — see the judgement call below.
**Rule 7** (any-join satisfiability) is implemented as an exhaustive **route-selection
simulation**, not a heuristic: it enumerates every combination of one-route-per-router
(`itertools.product`), and for each combination walks the topological order applying the same
not_taken/join semantics the engine applies at runtime (`_simulate_route_selection`), checking
whether a `join="any"` task is EVER active under ANY combination. A single-hop membership-overlap
check (my first approach) turns out to be mathematically vacuous — a direct producer's route is,
by construction of `build_dag`+`compute_cones`, *always* part of the join task's own membership
(the edge that makes it a producer is the same edge that extends the task's reach) — so genuine
unsatisfiability only arises transitively (a `join="all"` convergence upstream that is *itself*
always dead because it draws from two mutually-exclusive routes). The full simulation is the only
approach that catches this correctly without false negatives; a
`_MAX_ANY_JOIN_COMBINATIONS = 4096` safety cap skips (rather than hangs) enforcement for a
pathological router/route count, per "safe by default." **Rule 8** checks each route's exclusive
cone contains ≥1 sink (`not adj.get(tid)`).

**Judgement call — rule 6 explicit-vs-default `join`:** `TaskSpec.join` defaults to `"all"` with
no pydantic-level way to distinguish "author wrote `join: all` on purpose" from "left unset."
Per the ticket's own guidance ("pick the simplest interpretation... don't over-engineer"), rule 6
always warns on a declared-edge multi-route convergence with `join=="all"`, regardless of explicit
authoring. This trades a small number of redundant warnings (an author who *did* write `join: all`
deliberately still sees the warning) for zero implementation complexity (no shadow "was this
explicit" tracking field) and zero false negatives (an accidental default is never silently
missed) — consistent with rule 6 being non-fatal (a WARN, not a reject), so the cost of a
redundant warning is low.

**CLI exception-handling fix (required to avoid a regression, not scope creep):** `_load_all`
calling `build_dag(wf).topological_order()` means `CycleError` (a `SpecValidationError`
sibling, both `OrchestratorError`) can now surface from inside `_load_all` for `ao run`/`resume`/
`status` too — previously only reachable there via `orch.run()`'s *own*, separate, correctly-
handled `build_dag`+`topological_order()` call inside `engine.py`. But `run`/`resume`/`status`'s
existing exception handler around `_load_all` was `except (OrchestratorError, SystemExit): return`
— a bare `return` with **no** `typer.Exit(1)`, silently producing exit code 0. This was a
pre-existing latent gap (never triggered before, since `_load_all` never built the DAG), but my
own change to `_load_all` would have newly triggered it for cyclic workflows passed to `ao run`,
**regressing** the existing `test_cycle_detected_via_cli` test (which asserts non-zero exit).
Fixed by splitting the except-clause in all three call sites to properly
`typer.echo(f"ERROR: {e}", err=True); raise typer.Exit(1)` for `OrchestratorError` (matching the
`validate` command's existing pattern) while still swallowing bare `SystemExit` as before —
minimal, necessitated directly by my own change, not an unrelated refactor.

Tests added: `tests/test_validate_run_control.py` (22 cases) — one rejection test per rule
(1-5, 7-11), a false-positive guardrail for rule 5 (declared `depends_on` + explicit `join` must
NOT trip R2 even when the shared path also matches producer/consumer — explicitly required by the
ticket), rule 6's WARN-not-reject behavior (both a direct unit test and a CliRunner exit-0-with-
warning test), rule 7's satisfiable-vs-unsatisfiable pair, a clean positive multi-endpoint +
breaker spec (unit + CliRunner), two CliRunner E2E tests for rule 5 (R2) and rule 8 (unreachable
endpoint) per the ticket's explicit ask (memory `engine-api-tests-dont-cover-cli`), and one
CliRunner regression-guard test proving a cyclic workflow now fails `ao validate` too (previously
only caught by `ao run`).

Verification: `uv run pytest -q` → **551 passed, 3 skipped** (529 baseline + 22 new, zero
regressions — confirmed exact baseline match before adding new tests, then net +22 after).
`uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy .` all clean on every file
this ticket touched (`spec.py`, `cli.py`, `tests/test_validate_run_control.py`); `mypy .` across
the full repo surfaces 6 pre-existing errors in `test_engine_budget.py`/`test_project_config.py`
(both unmodified relative to HEAD — pure baseline debt, not from any sibling ticket) and
`test_e2e_cli.py` (modified by the already-landed T-m2h5t7, not touched by this ticket) — all
outside this ticket's scope, none introduced by this change.

Scope stayed exactly to `spec.py` + `cli.py` (wiring only, per the ticket's guardrail) + one new
test file. `engine.py`/`breakers.py` untouched, as directed.

By: developer · Role: developer · Date: 2026-07-09 · Comment: T-w6p2c8
validate-routing-breaker-checks DONE. All 11 LLD §4.4 rules implemented and wired into
`cli._load_all`; rule 7 (any-join satisfiability) implemented as an exact route-selection
simulation rather than a heuristic after confirming the single-hop membership-overlap approach is
mathematically vacuous. One necessary side-fix: `run`/`resume`/`status`'s pre-existing
`except (OrchestratorError, SystemExit): return` silently swallowed errors with exit 0 — split
into a properly-exiting `OrchestratorError` branch (mirroring `validate`'s existing pattern) since
my own `_load_all` change would otherwise have newly regressed `test_cycle_detected_via_cli`.
