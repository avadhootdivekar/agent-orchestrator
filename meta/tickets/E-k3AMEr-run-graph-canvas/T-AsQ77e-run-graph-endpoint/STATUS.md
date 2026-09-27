# STATUS

- ID: `T-AsQ77e-run-graph-endpoint`
- Updated At: `2026-09-27`
- State: `Done`
- Owner: `Dev A (developer)`
- Scope: `MVP` · Sprint: `S2` · Estimate: `6 h`

## This update
- By: architect · Role: architect · Date: 2026-09-27 · Comment: Task created as part of the
  E-k3AMEr design package (design + tickets only; **no code written**). Acceptance criteria,
  pseudocode, and interfaces are in `TASK.md`, and the authoritative design is
  `docs-md/run-graph-canvas-hld.md`. Hardened by the Phase-4 consultations (HLD §23.1).
- By: developer · Role: developer · Date: 2026-09-27 · Comment: Moved to `In Progress`. Merged
  `ad/run-graph-canvas` (fast-forward `191da69` -> `64b5877`, Sprint 1 + Sprint 2 wave 1) into this
  worktree first, per CLAUDE.md's "merge the latest target branch before starting work" -- this
  worktree started off an older commit, so `T-l7t6TT`'s snapshot loader, `T-mzT3BW`'s edge
  iterator, and `T-M4qboy`'s pure builder (`ui/graph.py`) were not yet present until the
  fast-forward. Wired the builder to HTTP exactly to HLD §8.4's pseudocode: `RunRepository.
  load_graph` (the ONE place that does I/O for this feature -- loads `state.json` via the
  existing `load_state`/`run_dir` guard, then loads the latest `spec_sessions` snapshot via the
  SHARED `runstate.load_workflow_snapshot_at`, never a launch-record fallback, never a new engine
  `RunStateStore`), `DashboardService.run_graph` (the same `RunNotFoundError` -> `DashboardError`
  translation every other run-scoped method already does), and `GET /api/runs/{run_id}/graph` in
  `app.py`, matching the existing `/runs/{run_id}/log`-style route. Additive-only: `RunDetail.
  graph_version = compute_graph_version(state)` (computed in `RunRepository.detail`, the SAME
  function `/graph` uses, on the SAME loaded `state` -- equal by construction, reviewer
  MUST-FIX) and `TaskStat.dispatch_cycle`/`.not_taken_reason` (straight mirrors of fields that
  already existed on the engine's `TaskRunState`, just never surfaced on the dashboard view).
  Added `tests/ui/test_run_graph_endpoint.py` (AC-1..AC-6, 24 tests) and `tests/ui/
  test_graph_contract.py` (AC-7, 13 tests). No file under `src/agent_orchestrator/` other than
  `ui/runs.py`/`ui/service.py`/`ui/app.py` was touched; `ui/graph.py` (T-M4qboy's pure builder),
  `models.py`, `engine.py`, `runstate.py`, `dag.py` were only read, never edited; nothing under
  `ui/` (the frontend TypeScript project, T-pAi0Cv's parallel worktree) was touched.
- By: developer · Role: developer · Date: 2026-09-27 · Comment: All 8 acceptance criteria
  verified with real, passing tests (see Evidence below); no material deviations from `TASK.md`.
  Moved to `Done`. Gate G1 (reviewer + tester sign-off) is still open -- flagging for
  `dev-epic`/reviewer, not self-certifying it here, per the Sprint 1/Sprint 2 precedent
  (`T-AZzgT8`/`T-l7t6TT`/`T-mzT3BW`/`T-M4qboy`).

## Evidence

### AC-1 -- thin, faithful wrapper
`tests/ui/test_run_graph_endpoint.py::TestSnapshotBackedHappyPath` (2 tests):
`test_repo_load_graph_equals_build_run_graph_on_the_same_inputs` builds a snapshot-backed run,
calls `RunRepository.load_graph` and separately reloads state+snapshot and calls
`build_run_graph` directly, then asserts the two `RunGraph` dataclasses are equal by `==`
(structural equality, not an eyeball) plus explicit node-id-set/edge-list comparisons;
`test_http_graph_response_equals_build_run_graph_on_the_same_inputs` does the same comparison
through a real `TestClient` HTTP round-trip (`response.json() == asdict(build_run_graph(...))`).
Both assert `source="snapshot"` and `schema_version=1`.

### AC-2 -- 404s, existing `run_dir` guard reused
`TestNotFound` (8 tests: 2 direct + a 4-way parametrized traversal-guard test + a 2-way
parametrized service-layer test): unknown run id
via `TestClient` -> `404 {"detail": "run not found: no-such-run"}` (exact match); unreadable
`state.json` (`{not json` on disk, same pattern as `test_runs.py`'s own corrupt-state test) ->
same 404 shape; `..%2F..%2Fetc`/`../x`/`..`/`../runs` passed as literal `run_id` strings directly
to `RunRepository.load_graph` (proven at the repository layer rather than through an HTTP
client, which normalizes `..` segments out of the URL before the request is ever sent --
confirmed empirically: httpx collapses `../x` to `/api/x/graph`, which never reaches our route
at all) -> `RunNotFoundError`, i.e. the SAME guard `run_dir()` already enforces for
`detail`/`delete`, not a new one. A second parametrized test drives the same two ids through
`DashboardService.run_graph` -> `DashboardError`.

### AC-3 -- degraded is 200 + warnings, never 5xx
`TestDegraded` (5 tests), one per scenario, each via a real `TestClient` request:
pre-epic run (`write_run` with no `record_spec_session` call, so `spec_sessions == []`),
deleted snapshot file, oversized snapshot (`monkeypatch` on `runstate.WORKFLOW_SNAPSHOT_MAX_BYTES`
down to 4 bytes), sha-mismatched snapshot (body's `spec_sha256` field rewritten to
`"0" * 64`, filename left untouched -- a genuine name/body mismatch), and invalid snapshot JSON.
Every one asserts `status_code == 200`, `source == "unavailable"`, and non-empty `warnings`.
`caplog` (at `agent_orchestrator`, which covers both `ui.runs`' and `runstate`'s loggers)
confirms `ui.graph.degraded` fires exactly once for the 4 snapshot-related cases and zero times
for the pre-epic case (nothing was "missing or invalid" -- there was never a snapshot to begin
with).

### AC-4 -- version equality (reviewer MUST-FIX)
`TestVersionEquality` (5 tests) covers every run kind from AC-1 and AC-3 (snapshot-backed,
pre-epic, deleted snapshot, sha-mismatched, invalid JSON): for each, `GET /api/runs/{id}` and
`GET /api/runs/{id}/graph` are both fetched through the real `TestClient`, and
`detail["graph_version"] == graph["graph_version"]` plus `detail["graph_version"]` is truthy
(never empty/None on this backend). This holds by construction --
`RunRepository.detail` and `RunRepository.load_graph` both call `ui.graph.compute_graph_version`
on the same loaded `state`, never two independent derivations.

### AC-5 -- additive only
`TestAdditiveOnly` (2 tests): `test_only_the_three_documented_keys_are_new` diffs the full
`RunDetail`/`TaskStat` key sets against a hardcoded "before" set (the exact field list both
predated this task) and asserts the delta is exactly `{"graph_version"}` at the top level and
exactly `{"dispatch_cycle", "not_taken_reason"}` on a task row, with every pre-existing key still
present. `test_every_pre_existing_value_is_byte_identical_to_before` re-asserts the SAME numeric
fixture `tests/ui/test_runs.py::TestDetail` already pins (cost, tokens, duration, statuses, etc.)
value-by-value, not just key-presence, against the conftest's own default `make_run_state()`
fixture.

### AC-6 -- no leakage (security-relevant)
`TestNoLeakage` (2 tests): a workflow with a real hook (`HookSpec(type="command",
command=[<sentinel argv>])` wired via `TaskSpec.post_hook`), an `IntegrationSpec.verify_command`
sentinel, and a task `instruction` sentinel path. One test searches
`json.dumps(asdict(RunRepository.load_graph(...)))`, the other searches the raw
`TestClient` response text -- both assert none of the three sentinel strings appear anywhere in
the serialized `/graph` payload.

### AC-7 -- contract test
`tests/ui/test_graph_contract.py` (13 tests): loads `ui/src/test/fixtures/run-graph.json` and
asserts (a) its top-level key set equals `RunGraph.__dataclass_fields__.keys()` exactly; (b)
`schema_version` matches `GRAPH_SCHEMA_VERSION`; (c) `source`/`spawn_data` are in their closed
enums; (d) every node's key set equals `GraphNode`'s fields, and `origin` stays a non-empty
open-set string (the fixture's deliberate `"manual"` unknown-origin value is asserted present,
not rejected); (e) every dependency/spawn edge's key set equals `GraphDependencyEdge`/
`GraphSpawnEdge`'s fields, with `kind` checked against `dag.py`'s `EDGE_KIND_*` closed enum; (f)
every loop/router's key set equals `GraphLoop`/`GraphRouter`'s fields. A final test builds a
fresh `RunGraph` from a from-scratch `RunState` (not a re-parse of the fixture) and asserts its
`asdict()` top-level key set still equals the fixture's -- the "fails in both directions" half
TASK.md calls for, independent of which side (fixture or dataclass) drifted first.

### AC-8 -- CI UI coverage gate
Exact CI invocation, read from `.github/workflows/ci.yml` line 26:
```
uv run pytest tests/ui tests/test_general_instructions.py tests/test_e2e_cli_prompt_and_instructions.py \
  -q --cov=agent_orchestrator.ui --cov-report=term --cov-fail-under=80
```
Result: **462 passed**, gate **passes** at **93.24%** total coverage (floor 80%). Per-module:
`ui/runs.py` 99% (195 stmts, 2 missed -- both pre-existing, in `exists()`'s except branch,
unrelated to this task's new `load_graph`/additive-field code, which this same run's
`--cov=agent_orchestrator.ui.runs --cov-report=term-missing` confirms is the ONLY gap: lines
211-212, pre-dating this task); `ui/app.py` 95%; `ui/service.py` 93%; `ui/graph.py` 86% (this CI
command does not run `tests/test_ui_graph.py`, which is T-M4qboy's own 99%-branch-coverage suite
for that module -- not a gap introduced or owned by this task).

## Full validation

Baseline (this worktree, after the `ad/run-graph-canvas` fast-forward to `64b5877`, before this
task's changes -- matches T-M4qboy's own reported post-fast-forward baseline exactly):
- `uv run pytest -q` -> **4573 passed, 8 skipped**

After this task's changes:
- `uv run pytest -q` -> **4610 passed, 8 skipped** (+37 = exactly the new tests in
  `tests/ui/test_run_graph_endpoint.py` (24) and `tests/ui/test_graph_contract.py` (13); **0
  regressions, 0 new failures**)
- `uv run ruff check .` -> 1 pre-existing error, same file as every prior sibling task's baseline
  (`output/E-YAAGhk-overseer-runner-template/repro_emit_lost_on_breaker_trip.py`, an architect
  scratch repro, untouched by this task); 0 new findings in any file this task touched
- `uv run ruff format --check .` -> same 1 pre-existing reformat candidate, same file; 0 new (all
  of this task's own files are format-clean)
- `uv run mypy src` -> same 4 pre-existing errors, all in `src/agent_orchestrator/_version.py`
  (auto-generated version stamp, unrelated); **0 new errors**. `uv run mypy src/agent_orchestrator/
  ui/runs.py src/agent_orchestrator/ui/service.py src/agent_orchestrator/ui/app.py` (the three
  files this task actually edited) -> `Success: no issues found in 3 source files`.
- AC-8's CI coverage-gate command -> **462 passed**, **93.24%** total (see above).

## Deviations from TASK.md
None material. One clarification, resolved by treating HLD §8.4 as authoritative over the
task-brief's own paraphrase (per the task's own framing -- "or however the HLD §8.4 pseudocode
structures it"): `RunRepository.load_graph(run_id)` returns the fully-built `RunGraph` directly
(matching §14.1's own interface listing, `ui.runs.RunRepository.load_graph(run_id: str) ->
RunGraph`), not a `(state, snapshot)` tuple -- the pseudocode's own signature and body
(`RETURN build_run_graph(state, snapshot)`) settle this unambiguously. Likewise,
`RunDetail.graph_version` is computed inside `RunRepository.detail` (exactly where HLD §8.4's
pseudocode places it, under `RunRepository.detail(run_id) (additive)`), not inside
`DashboardService.run_detail` -- `DashboardService.run_detail` needed no change at all, since
`graph_version` flows through automatically as part of `RunDetail`'s own `asdict()`. This is the
more DRY placement (one computation site, not two) and is what makes AC-4's equality hold *by
construction* rather than by convention.

## Risks / Blockers
- Blockers: none. Implementation complete, all 8 ACs verified with real, passing tests.
- Dependencies: `T-M4qboy` (builder), `T-l7t6TT` (shared snapshot loader) -- both Gate G1
  `CLOSED, PASS` per their own STATUS.md files -- consumed exactly as documented
  (`build_run_graph`/`compute_graph_version`, `runstate.load_workflow_snapshot_at`), no changes
  requested or made to either. `T-adVpTj`'s fixture (`ui/src/test/fixtures/run-graph.json`) was
  read, never modified.
- Known, accepted risk from `TASK.md`'s own Risks section ("keep route registration consistent
  with the existing `/runs/{run_id}/log` style") -- mitigated directly: the new route is
  registered in the same file, same style, same `DashboardError` -> `HTTPException(404, ...)`
  mapping as `run_detail`'s own route immediately above it.
- Downstream: `T-F1caAt` (graph e2e verification) and the frontend tasks (`T-OjTS8O`,
  `T-aHktGB`, `T-adVpTj`, `T-pAi0Cv`, in their own parallel worktrees) may now build against the
  live `GET /api/runs/{run_id}/graph` endpoint and the additive `RunDetail`/`TaskStat` fields --
  not self-certifying either downstream task's own acceptance criteria, only that this task's own
  endpoint and interface are ready for them to consume.

## Next actions
1. Reviewer + tester sign-off (Gate G1), per the Sprint 1/Sprint 2 precedent -- not
   self-certified here.
2. `dev-epic` rolls up `EPIC.md`/epic `STATUS.md` and unblocks `T-F1caAt` once Gate G1 closes.
