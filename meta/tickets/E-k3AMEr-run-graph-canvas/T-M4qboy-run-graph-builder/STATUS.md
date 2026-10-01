# STATUS

- ID: `T-M4qboy-run-graph-builder`
- Updated At: `2026-09-27`
- State: `Done`
- Owner: `Dev A (developer)`
- Scope: `MVP` · Sprint: `S2` · Estimate: `14 h`

## This update
- By: architect · Role: architect · Date: 2026-09-27 · Comment: Task created as part of the
  E-k3AMEr design package (design + tickets only; **no code written**). Acceptance criteria,
  pseudocode, and interfaces are in `TASK.md`, and the authoritative design is
  `docs-md/run-graph-canvas-hld.md`. Hardened by the Phase-4 consultations (HLD §23.1).
- By: developer · Role: developer · Date: 2026-09-27 · Comment: Moved to `In Progress`. Merged
  `ad/run-graph-canvas` (fast-forward to `729922d`, the Sprint 1 Gate G1 close) into this
  worktree first, per CLAUDE.md's "merge the latest target branch before starting work" — this
  worktree had been created off `main`, so `T-AZzgT8`/`T-l7t6TT`/`T-mzT3BW`'s merged work
  (`RunState.spawned_by`/`spec_sessions`, `dag.iter_dependency_edges`) was not yet present until
  the fast-forward. Implemented `src/agent_orchestrator/ui/graph.py` exactly to the `TASK.md`/HLD
  §8.3.2 pseudocode: `build_run_graph`, `compute_graph_version`, `display_text`, `label_for`, the
  6 dataclasses (`RunGraph`, `GraphNode`, `GraphDependencyEdge`, `GraphSpawnEdge`, `GraphLoop`,
  `GraphRouter`), and the named constants. Added `tests/test_ui_graph.py` (42 tests). No file
  under `src/agent_orchestrator/` other than the new `ui/graph.py` was touched; nothing under
  `ui/` (the frontend project, a parallel `T-aHktGB` worktree) was touched.
- By: developer · Role: developer · Date: 2026-09-27 · Comment: All 10 acceptance criteria
  verified with real, passing tests (see Evidence below); no material deviations from `TASK.md`
  (two intentional additions noted below). Moved to `Done`. Gate G1 (reviewer + tester sign-off)
  is still open — flagging for `dev-epic`/reviewer, not self-certifying it here, per the Sprint 1
  precedent (`T-AZzgT8`/`T-l7t6TT`/`T-mzT3BW`).

## Evidence

### AC-1 — dataclasses + constants
`src/agent_orchestrator/ui/graph.py`: `RunGraph`, `GraphNode`, `GraphDependencyEdge`,
`GraphSpawnEdge`, `GraphLoop`, `GraphRouter` — all `@dataclass(frozen=True)`, field names and
order matching HLD §14.2 exactly (verified against `ui/src/test/fixtures/run-graph.json`'s key
order by inspection). Constants `GRAPH_SCHEMA_VERSION=1`, `GRAPH_MAX_NODES=5000`,
`GRAPH_LABEL_MAX_CHARS=200`, `GRAPH_VERSION_HEX_CHARS=16`, `GRAPH_SOURCE_SNAPSHOT="snapshot"`,
`GRAPH_SOURCE_UNAVAILABLE="unavailable"` all present with the exact required values.
Verified by `TestDataclassesAndConstants` (6 tests): frozen-ness of every dataclass, exact field
name/order for `RunGraph` and `GraphNode`, constant values, and a round-trip
`json.dumps(asdict(g))` smoke test.

### AC-2 — edges come only from `dag.iter_dependency_edges` (DRY, ADR-0017 D3)
`TestNoReimplementedEdgeDerivation::test_no_own_dependency_iteration_or_output_input_matching`
greps the committed `ui/graph.py` source for the literal substrings `"depends_on"` and
`"output_to_task"` and asserts neither appears anywhere in the file (including comments/
docstrings — every explanatory comment about ADR-0017 D3 was deliberately phrased without
those literals so the grep stays meaningful rather than needing an exclusion list). A companion
test confirms `iter_dependency_edges(` is actually called, so the grep isn't just "vacuously
true because nothing about edges is implemented at all."

### AC-3 — fixture scenarios (a)-(k)
Each scenario is its own test in `TestFixtureScenarios`, all with fixed timestamps (`T0`/`T1`
module constants, no real clock):
- (a) `test_a_static_linear_chain_with_snapshot` — 3 nodes, dependency edges
  `[(a,b),(b,c)]`, no spawn edges, `spawn_data="none"`.
- (b) `test_b_overseer_shape_dependency_and_spawn_edges_differ` — spawn edges exactly
  `cp1→{u1,u2,u3,cp2}`; dependency edges exactly `{u1→cp2, u2→cp2, u3→cp2}`; explicit assertion
  the two sets differ (U-4).
- (c) `test_c_nested_emit_produces_spawn_depths_0_1_2` — `spawn_depth` 0/1/2 for a→b→c nested
  emit.
- (d) `test_d_loop_times_3_iteration_and_loop_id_and_loop_edge` — iteration-1 body nodes get
  `iteration=1, loop_id="L"` via the precomputed `body_loop_of` map; injected clones get
  `iteration`/`loop_id`/`parent_task_id` from their `SpawnRecord`; a `kind="loop"` edge
  `gate__iter3 -> downstream` (`via="L"`) exists because `downstream` declares `depends_on:
  ["L"]` — resolved entirely by `dag.iter_dependency_edges`/`_resolve_loop_dep`, not by any code
  in `graph.py`; `is_loop_gate` true for both `gate` and `gate__iter2`; `GraphLoop.
  iterations_materialized == 3` from `state.loop_iterations`.
- (e) `test_e_router_with_not_taken_cone` — `is_router` true only on the router task; `routers
  == [GraphRouter(id="classify", router_task_id="triage", selected=["bug"])]`.
- (f) `test_f_unknown_dependency_yields_phantom_node_and_warning` — phantom node `"zzz"` with
  `missing=True`, `origin="static"`, plus the exact warning text
  `"1 dependency id(s) reference unknown tasks and are shown as missing nodes."`.
- (g) `test_g_legacy_no_spec_sessions_no_spawned_by_injected_tasks_present` — `source=
  "unavailable"`, `spawn_data="not_recorded"`, dependency edges from the injected tasks present,
  exactly 2 warnings (pre-snapshot + spawn-not-recorded).
- (h) `test_h_spec_sessions_present_but_snapshot_none` — `source="unavailable"`, exactly 1
  warning containing `"missing or unreadable"`.
- (i) `test_i_spec_changed_mid_run_warning_names_latest_session` — two `SpecSession`s with
  different `spec_sha256`; warning names `"session 2"`.
- (j) `test_j_execution_ordinal_by_time_then_id_then_none` — 6 tasks: chronological ordinal
  assignment (`beta` at 10:00:01 ranks before `alpha` at 10:00:05 despite alphabetic order),
  an exact-tie pair (`delta`/`echo`, same `started_at`) broken by id, and a never-started task
  (`zeta`, `started_at=None`) getting `exec_ordinal=None`.
- (k) `test_k_origin_fallback_record_then_task_run_state_then_static` — one node in
  `spawned_by` → origin from the record (`"injected"`); one orphan in `state.tasks` only → origin
  from `TaskRunState.origin` (`"loop"`); one phantom (unknown dep) → origin falls back to
  `"static"`.

### AC-4 — sanitizer
`TestDisplayTextSanitizer` (4 tests): `display_text("a‮b​c") == ("abc", True)`; a
1,000-char id truncates to exactly 200 chars ending in `"…"` with `changed=True`; a plain id
passes through as `(id, False)`; and
`test_raw_id_field_never_sanitized_only_label_is` builds a real node from an id containing
`"leaf​"` and asserts `node.id` is byte-exact (unsanitized, the join key) while `node.label
== "leaf"` with `label_sanitized=True`.

### AC-5 — early cap
`TestEarlyCap::test_50000_tasks_builds_fast_and_caps_at_graph_max_nodes` — a 50,000-task
`RunState` (a single shared `TaskRunState` instance reused across all 50,000 dict values, to
keep fixture *construction* itself fast — only the `build_run_graph` call is timed) builds in
**0.027s measured** (assert `< 3.0s`, the 3× headroom the HLD's own perf gate asks for; HLD's
un-headroomed target is `< 1s`, comfortably met too), returns exactly `GRAPH_MAX_NODES` (5000)
nodes, `truncated=True`, and no node has `missing=True` (no phantom invented from a cut task).
A second, `monkeypatch`-based test (`test_truncation_splits_merged_then_orphans`, `GRAPH_MAX_NODES`
patched to 4) pins the merged-then-orphans split arithmetic precisely: 3 merged (static) tasks
all kept (under cap) + exactly 1 of 5 orphans admitted (`cap - len(merged)`).

### AC-6 — forged spawn cycle
`TestForgedSpawnCycle` (2 tests): a 2-node cycle (`a→b`, `b→a`) — both depths `None`, no
exception; a 3-node cycle with no root at all (`a→b→c→a`) — all three depths `None`, no
exception. `TestSpawnDepthHelperDirectly::test_diamond_shape_exercises_the_already_visited_guard`
additionally white-box-tests `_compute_spawn_depths` directly with a synthetic diamond
(`r1→x`, `r2→x`, `x→y`) to exercise the BFS visited-set "already seen" guard the HLD pseudocode
calls for — this specific guard is provably unreachable through the public `RunState.
spawned_by: dict[str, SpawnRecord]` shape (one dict key per child means at most one recorded
parent per task, so no node can ever be pushed onto the BFS queue twice via that data model);
it is exercised here because `_compute_spawn_depths`'s own contract (`Iterable[tuple[str,
str]]`) is intentionally broader than that one call site.

### AC-7 — determinism
`TestDeterminism::test_building_twice_from_identical_inputs_is_byte_identical` — a
loop+router+injected-task combined fixture, built twice from the same `RunState`/
`WorkflowSnapshot` objects; `json.dumps(asdict(g), sort_keys=False)` is byte-identical both
times.

### AC-8 — version sensitivity
`TestGraphVersionSensitivity` (4 tests): `compute_graph_version` changes when
`injected_tasks` gains a new entry, when a new `SpecSession` with a new `spec_sha256` is
appended, and when `loop_iterations` changes; does **not** change when a task's `status`,
`attempts`, `started_at`, and `cumulative_cost_usd` are all changed simultaneously (the dict
*key* set is unchanged — `compute_graph_version` only ever reads `sorted(state.tasks)`, i.e.
the id set, never a `TaskRunState`'s field values).

### AC-9 — perf (200 nodes / 500 edges)
Deterministic layered edge generator (`_generate_edges`, no `random` per CLAUDE.md) produces
exactly 200 nodes / 500 distinct dependency edges (`test_generator_produces_exactly_200_nodes_
and_500_edges`). Measured build time (standalone script, best of 7 samples, warm-up excluded):
**0.89ms** — far under the HLD's 150ms dev-machine target. The committed CI test
(`test_build_under_ci_safe_450ms_budget`) asserts best-of-3 `<= 450ms` (the HLD's stated 3×
headroom over the 150ms target) to stay flake-safe while still catching a real regression.

### AC-10 — no web-framework import, no file I/O, coverage
`TestPureModuleBoundary` (2 tests) greps the committed source for `"fastapi"`, `"open("`,
`"read_text("`, `"write_text("`, `"Path("`, `"import os"`, `"from pathlib"`, `"import
pathlib"` and asserts none appear. Manually confirmed importable standalone:
```
uv run python -c "from agent_orchestrator.ui import graph; print('ok')"
```
succeeds in a fresh interpreter (the `[ui]`/`fastapi` extra is installed in this dev venv, but
`ui/graph.py` itself never imports it — the grep is the actual gate; T-AsQ77e's own suite would
need to run against a venv with `fastapi` genuinely absent to prove the extra boundary
end-to-end, which is out of this task's scope).

Coverage:
```
uv run pytest -q --cov=agent_orchestrator.ui.graph --cov-branch --cov-report=term-missing tests/test_ui_graph.py
```
```
Name                                 Stmts   Miss Branch BrPart  Cover   Missing
--------------------------------------------------------------------------------
src/agent_orchestrator/ui/graph.py     195      0     36      1    99%   276->275
--------------------------------------------------------------------------------
TOTAL                                  195      0     36      1    99%
42 passed in 0.25s
```
99% branch coverage (>= the 95% floor). The one remaining partial branch (`276->275`, the BFS
"child already visited at push-time" arc inside `_compute_spawn_depths`) is, by the same
single-parent-per-child argument as AC-6 above, unreachable through *any* well-typed
`RunState.spawned_by` value and was judged not worth a second synthetic multi-level DAG fixture
purely to move a coverage counter — a defensible, explicitly-justified gap rather than a
silent one.

## Full validation

Baseline (this worktree, before this task's changes, fast-forwarded onto `ad/run-graph-canvas`
@ `729922d`, the Sprint 1 Gate G1 close):
- `uv run pytest -q` → **4531 passed, 8 skipped** (matches the epic checkpoint exactly)
- `uv run ruff check .` → 1 pre-existing error (`output/E-YAAGhk-overseer-runner-template/
  repro_emit_lost_on_breaker_trip.py`, an architect scratch repro file, untouched by this task)
- `uv run ruff format --check .` → same 1 pre-existing reformat candidate, same file
- `uv run mypy src` → 4 pre-existing errors, all in `src/agent_orchestrator/_version.py`
  (auto-generated version stamp file, unrelated to this task)

After this task's changes:
- `uv run pytest -q` → **4573 passed, 8 skipped** (+42 = exactly the new
  `tests/test_ui_graph.py` tests; **0 regressions, 0 new failures**)
- `uv run ruff check .` → same 1 pre-existing error, same file; 0 new
- `uv run ruff format --check .` → same 1 pre-existing candidate, same file; 0 new (`ui/graph.py`
  and `tests/test_ui_graph.py` are themselves format-clean)
- `uv run mypy src` → same 4 pre-existing `_version.py` errors; **0 new errors** (`ui/graph.py`
  alone: `Success: no issues found in 1 source file`)
- `uv run mypy tests/test_ui_graph.py` (standalone-file invocation) → 3 `import-untyped` notes,
  the same pre-existing artifact of pointing mypy at an individual test file outside `src`
  documented by `T-AZzgT8`'s STATUS.md (every existing test file hits this identically); not a
  defect in this task's code. The task's real gate, `mypy src`, is clean.

## Deviations from TASK.md
None material. Two small, intentional additions beyond the letter of the pseudocode, both
staying inside this task's file boundary:
1. `_parse_started_at` tolerates a malformed `started_at` string (returns `None`, mirroring
   `ui/runs.py::_parse_iso`'s existing tolerant-parse convention) rather than raising — the HLD
   pseudocode's `parse(started_at[id])` doesn't specify failure behavior, and a pure builder
   that can raise on a corrupted `state.json` field would violate this module's own "no
   exceptions" spirit (AC-6's cycle handling holds to the same standard). Covered by
   `TestParseStartedAtDefensive`.
2. The HLD's `EMPTY_WORKFLOW_SHELL` sentinel is constructed inline (a plain `WorkflowSpec(...)`
   literal) inside `build_run_graph` rather than as a module-level singleton copied via
   `model_copy` — behaviorally identical, slightly more direct Python, and avoids a throwaway
   object at import time for a value that not every call needs.

## Risks / Blockers
- Blockers: none. Implementation complete, all 10 ACs verified with real, passing tests.
- Dependencies: `T-AZzgT8`, `T-l7t6TT`, `T-mzT3BW` (all Gate G1 `CLOSED, PASS` per their own
  STATUS.md files) — consumed as documented (`RunState.spawned_by`/`spec_sessions`,
  `dag.iter_dependency_edges`), no changes requested or made to any of their files.
- Known, accepted risk carried from `TASK.md`: "misreading the loop-dep semantics" — mitigated
  exactly as instructed, by reusing `dag.iter_dependency_edges` for 100% of edge derivation
  (AC-2's grep test pins this structurally, not just by convention). "Performance of repeated
  `loop_of_body_task` lookups" — mitigated by the precomputed `body_loop_of: dict[str, str]` map
  built once per `build_run_graph` call (`O(1)` per-node lookup instead of an `O(loops×body)`
  scan per node).
- Downstream: `T-AsQ77e` (dashboard `/graph` endpoint + contract test against `ui/src/test/
  fixtures/run-graph.json`) and `T-VcN4pt` (changes only `label_for`'s body) may now build on
  `ui.graph.build_run_graph`/`compute_graph_version`/`display_text` — not self-certifying that
  either downstream task's own acceptance criteria are met, only that this task's own module and
  interface are ready for them to consume.

## Next actions
1. Reviewer + tester sign-off (Gate G1), per the Sprint 1 precedent — not self-certified here.
2. `dev-epic` rolls up `EPIC.md`/epic `STATUS.md` and unblocks `T-AsQ77e`/`T-VcN4pt` once Gate G1
   closes.
