# STATUS

- ID: `T-mzT3BW-dag-edge-iterator`
- Updated At: `2026-09-27`
- State: `Implemented — Reviewer sign-off required before merge`
- Owner: `Dev A (developer) + reviewer sign-off`
- Scope: `MVP` · Sprint: `S1` · Estimate: `8 h`

## This update
- By: architect · Role: architect · Date: 2026-09-27 · Comment: Task created as part of the
  E-k3AMEr design package (design + tickets only; **no code written**). Acceptance criteria,
  pseudocode, and interfaces are in `TASK.md`, and the authoritative design is
  `docs-md/run-graph-canvas-hld.md`. Hardened by the Phase-4 consultations (HLD §23.1).
- By: developer · Role: developer · Date: 2026-09-27 · Comment: Implemented per `TASK.md`/HLD
  §8.3.1. `src/agent_orchestrator/dag.py` now exports `DependencyEdge`, `EDGE_KIND_EXPLICIT`,
  `EDGE_KIND_LOOP`, `EDGE_KIND_INFERRED`, and `iter_dependency_edges`; `build_dag` is refactored
  to consume the iterator. All 6 acceptance criteria pass with evidence below. **This task is
  NOT mergeable without reviewer sign-off** (Owner note / scheduling-order risk, ADR-0017 Risk
  R-2) — flagging for Gate G1 as instructed; state left at `Implemented`, not `Done`, pending
  that review.

## Acceptance criteria — results

1. **PASS** — `dag.py` exports `DependencyEdge` (NamedTuple: `source: str, target: str, kind:
   str, via: str | None`), `EDGE_KIND_EXPLICIT`/`EDGE_KIND_LOOP`/`EDGE_KIND_INFERRED`, and
   `iter_dependency_edges` (`src/agent_orchestrator/dag.py:18-36`, `:158-215`).
2. **PASS — oracle equality.** `tests/test_dag_edge_iterator_oracle.py` keeps the verbatim
   pre-refactor `build_dag` body as `_oracle_build_dag` (lines 58-109). Coverage: **12 specs**
   (>= the 10-spec floor, enforced by an assertion at collection time, line 170):
   - 10 from `specs/` (every file `load_workflow` accepts under `specs/**/*.json|yaml|yml`):
     `specs/examples/{workflow,workflow-budget,workflow-dry-run-flag,workflow-dynamic-fanout,
     workflow-dynamic-pipeline,workflow-dynamic,workflow-hooks,workflow-loop,
     workflow-monitoring,workflow-routing-breakers}.json`.
   - 0 from `tests/**/*.json|yaml` (the only 3 such files —
     `tests/fixtures/claude_usage.json`, `tests/fixtures/state_pre_routing_breakers.json`,
     `tests/bench/data/gold.feasibility-gold.json` — are not workflow specs and are correctly
     rejected by `load_workflow`; none loadable, so none included).
   - 2 from the builtin templates' rendered example specs, via the real `instantiate()` path
     (`routed-runner`, `overseer-runner`, each with only the one required param `repo_set`).
   `test_adjacency_matches_oracle_including_order` asserts
   `json.dumps(new.adjacency()) == json.dumps(oracle.adjacency())` (list order included) and
   `json.dumps(new.output_to_task()) == json.dumps(oracle.output_to_task())` for all 12.
   Evidence: `uv run pytest -q tests/test_dag_edge_iterator_oracle.py` → **32 passed** (12 specs
   × 2 oracle-equality tests = 24, + 7 synthetic-case tests + 1 warning-text test = 32).
3. **PASS — synthetic cases** (`TestSyntheticEdgeKinds`, lines 242-300), one test per case in
   TASK.md AC-3: explicit dep; loop-id dep resolving to `review__iter3`/`kind="loop"`/
   `via="qa-loop"` at 3 materialized iterations; inferred dep; explicit+inferred on the same
   pair collapsing to one `explicit` edge; unknown dep yielding an edge plus `build_dag`'s
   phantom `adj["ghost"]` key; self-referential input/output yielding no edge; duplicate
   `depends_on` entry collapsing to one edge. All 7 pass.
4. **PASS — warning text byte-identical.** `TestInferredWarningTextMatchesOracle` (lines
   308-324) runs the oracle and the new `build_dag` under `caplog` on the same synthetic
   inferred-edge case and asserts the message lists are equal, pinned to
   `"Inferred edge a -> b (input out/x.txt matches output) not declared in depends_on"`.
5. **PASS — `topological_order()` identity.** `test_topological_order_matches_oracle` runs for
   all 12 AC-2 specs (parametrized alongside AC-2) and asserts equal order (or equal
   `CycleError.nodes` on the (unexercised, none of the 12 specs cycle) cycle path).
6. **PASS.**
   - `uv run pytest -q` (full suite): **4487 passed, 8 skipped**, 0 failed.
   - Named suites individually: `uv run pytest -q tests/test_loop_construct.py
     tests/test_dynamic_injection.py tests/test_dag.py tests/test_dag_edge_iterator_oracle.py`
     → **95 passed**. Routing/parallel suites (`test_builtin_routed_runner_assets.py`,
     `test_e2e_builtin_routed_runner.py`, `test_e2e_cli_max_parallel.py`,
     `test_engine_routing.py`, `test_route_cones.py`, `test_routing_breaker_models.py`) →
     **130 passed**. No regressions vs. baseline (pre-change, unmodified `main`/branch state
     also ran the full suite clean before this change; the +32 oracle/synthetic tests are the
     only delta).
   - `uv run ruff check .` → clean on both touched files (`src/agent_orchestrator/dag.py`,
     `tests/test_dag_edge_iterator_oracle.py`); the one repo-wide finding
     (`output/E-YAAGhk-overseer-runner-template/repro_emit_lost_on_breaker_trip.py`,
     import-sort) is a pre-existing scratch file outside this task's change boundary, excluded
     from scope.
   - `uv run ruff format --check .` → both touched files formatted clean (the same pre-existing
     scratch file above is the only repo-wide diff).
   - `uv run mypy src` → clean on `dag.py`; 4 pre-existing errors in `src/agent_orchestrator/
     _version.py` (untouched by this task) are excluded from scope.

## Evidence

- `git diff --stat` (this task's change boundary): `src/agent_orchestrator/dag.py | 138
  +++++++++++++++++++++++++++++++++---------` (108 insertions, 30 deletions) — only file
  edited; `tests/test_dag_edge_iterator_oracle.py` added (325 lines).
- No other file touched (`models.py`, `engine.py`, `runstate.py`, `ui/**` untouched, confirmed
  by `git status --porcelain`) — respects the parallel-task boundary with T-AZzgT8/T-l7t6TT.
- Final commit hash: see the commit created alongside this STATUS update (`git log -1`).

## Risks / Blockers

- Blockers: **none for implementation**, but per the Owner note this task **requires reviewer
  sign-off before merge** (scheduling-order risk, ADR-0017 Risk R-2) — that gate is not yet
  exercised; state is `Implemented`, not `Done`.
- Dependencies: none upstream. Downstream: blocks `T-M4qboy` (consumes `iter_dependency_edges`)
  at Gate G1.

## Next actions

1. Reviewer sign-off on the diff (`src/agent_orchestrator/dag.py`,
   `tests/test_dag_edge_iterator_oracle.py`) per the Owner note — Gate G1.
2. On sign-off, move state to `Done` and unblock `T-M4qboy`.
