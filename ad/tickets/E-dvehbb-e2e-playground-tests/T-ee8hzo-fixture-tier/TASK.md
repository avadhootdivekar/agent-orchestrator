# TASK: T-ee8hzo-fixture-tier

## Metadata
- Task ID: `T-ee8hzo-fixture-tier`
- Epic ID: `E-dvehbb-e2e-playground-tests`
- Owner: tester
- Created: 2026-07-01
- Last Updated: 2026-07-01
- Status: Done
- Estimate: `< 2 days`
- MVP: **yes**

## Requirements Mapping
- FR-3, FR-7 (foundation), NFR-2, NFR-4, NFR-5, R2.

## Description
Implement **Tier 1 — fixture-based tests** for the playground corpus, plus the shared
assertion helpers the deterministic tier (T-r21p4y) reuses. This tier validates the
static correctness of every example's specs + fixtures WITHOUT running a workflow, so
authoring errors are caught cheaply and deterministically.

Deliver `tests/playground/test_fixtures.py` (parametrized over discovered
`playground/*` examples) and extend `tests/playground/_playground.py` with fixture
loaders + structure-assertion helpers.

## Inputs / Outputs
- Inputs: `playground/sum-of-array/*` (from T-1vuzyi); `spec.load_workflow`,
  `config.load_reposets`/`load_agents`, `spec.cross_validate`, `dag.build_dag`, the
  JSON Schemas under `specs/`.
- Outputs: `tests/playground/test_fixtures.py`; `_playground.py` helpers
  `load_expected(example)`, `expanded_workflow(example)` (spine + manifest tasks merged),
  `assert_tree(root, expected_paths)`.

## Acceptance Criteria
1. Given each `playground/<ex>/workflow.json`+`reposet.json`+`agents.fake.json`, When
   loaded + cross-validated, Then no error (mirrors `ao validate`). Repeat for `agents.claude.json`.
2. Given each `workflow.json`, When validated against `specs/workflow.schema.json`
   (jsonschema), Then it passes; likewise reposet/agents against their schemas.
3. Given `fixtures/tasks-manifest.json`, When parsed, Then it is `{"tasks":[...]}`,
   every entry validates as a `TaskSpec`, ids are unique and disjoint from the spine ids,
   and every `instruction` path exists under the example dir.
4. Given the **statically expanded** graph (spine tasks + manifest tasks merged), When
   `build_dag(...).topological_order()` runs, Then it does NOT raise `CycleError` (proves
   R2 mitigation: unique paths → no spurious cycles) and the inferred+explicit edge set
   matches `fixtures/expected_paths.json`'s declared ordering expectations.
5. Given `fixtures/final-verdict.json`, When parsed with `read_gate`, Then it returns a bool.
6. Given `fixtures/expected_events.json`, When loaded, Then every listed event name is one
   the engine actually emits (cross-checked against a known event allow-list constant).
7. Every id in every example matches `^[a-z0-9][a-z0-9-_]*$` (regex assertion).
8. `pytest -q` green; `ruff`/`mypy` clean; no `src/` change (NFR-1).

## Risks
- Schema drift: if `specs/*.schema.json` changes, fixtures must too — keep this tier as the
  early-warning canary (fails fast on mismatch).
- Expanded-graph acyclicity depends on unique paths from T-1vuzyi; if a path is duplicated,
  AC-4 fails loudly (that is the intended guardrail).

## Dependencies
- `T-1vuzyi` (assets), `T-7592ux` (harness discovery of `playground/*`).

## Pseudocode / Algorithm
```text
@pytest.mark.parametrize("example", discover_examples())   # ["sum-of-array", ...]
def test_specs_validate(example): load + cross_validate (fake and claude agents)
def test_specs_match_json_schema(example): jsonschema.validate against specs/*.schema.json
def test_manifest_fixture_wellformed(example):
    m = json.load(fixtures/tasks-manifest.json); assert "tasks" in m
    for t in m["tasks"]: TaskSpec(**t); assert instruction file exists; assert id unique
def test_expanded_graph_is_acyclic(example):
    wf = load_workflow(); merge manifest tasks into wf.tasks; build_dag(wf).topological_order()
def test_ids_match_pattern(example): all ids ~ ^[a-z0-9][a-z0-9-_]*$
def test_expected_events_are_known(example): set(expected_events) <= KNOWN_ENGINE_EVENTS
```

## Schemas / Interface Notes
- Interface / API: tests + helpers only.
- Spec / data schema: consumes `specs/*.schema.json` + control-file schemas.
- Triggers / events: N/A.
- Artifacts: reads `playground/<ex>/*`; writes nothing at runtime.

## Handoff Boundary
- Upstream: `T-1vuzyi`, `T-7592ux`.
- Downstream: `T-r21p4y` reuses `load_expected`/`assert_tree`/`expanded_workflow`.

## Artifacts
- Docs/comments: this task folder.
- Large outputs: none.
