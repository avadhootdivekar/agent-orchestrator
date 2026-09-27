# STATUS

- ID: `T-AZzgT8-spawn-provenance`
- Updated At: `2026-09-27`
- State: `Done`
- Owner: `Dev A (developer) + reviewer pairing`
- Scope: `MVP` · Sprint: `S1` · Estimate: `14 h`

## This update
- By: architect · Role: architect · Date: 2026-09-27 · Comment: Task created as part of the
  E-k3AMEr design package (design + tickets only; **no code written**). Acceptance criteria,
  pseudocode, and interfaces are in `TASK.md`, and the authoritative design is
  `docs-md/run-graph-canvas-hld.md`. Hardened by the Phase-4 consultations (HLD §23.1).
- By: developer · Role: developer · Date: 2026-09-27 · Comment: Moved to `In Progress`.
  Merged `ad/run-graph-canvas` (commit `66c4625`) into this worktree first to pick up the
  epic design package, per CLAUDE.md's "merge the latest target branch before starting
  work". Implemented exactly the `TASK.md` pseudocode: `SpawnRecord` +
  `SPAWN_ORIGIN_INJECTED`/`SPAWN_ORIGIN_LOOP` + `RunState.spawned_by` in `models.py`;
  `_inject`'s new keyword-only `parent_task_id`/`loop_id`/`iteration` params and the
  `state.spawned_by[...]` write (same per-spec loop step as `injected_tasks.append`, per
  the R-1 coordination note for `E-Grpp0X`) in `engine.py`; the two call sites updated;
  `origin` carried across all three wholesale `TaskRunState` replaces
  (`runstate.py::prepare_resume` ~278, `engine.py` worktree-collision ~1051 and
  missing-inputs ~1112). `route` deliberately **not** carried (F-2, out of scope).
- By: developer · Role: developer · Date: 2026-09-27 · Comment: All 12 acceptance criteria
  verified with real tests (see Evidence below); no deviations from `TASK.md`. Moved to
  `Done`. Gate G1 (reviewer + tester sign-off before `T-M4qboy` starts) is still open —
  flagging for `dev-epic`/reviewer, not self-certifying it here.

## Evidence

### AC-1 — model shape
`src/agent_orchestrator/models.py`: `SpawnRecord` (parent_task_id: str,
parent_dispatch_cycle: int, origin: str, injected_at: str, loop_id: str | None = None,
iteration: int | None = None), `SPAWN_ORIGIN_INJECTED`/`SPAWN_ORIGIN_LOOP` (typed
`Literal["injected"]`/`Literal["loop"]` so they satisfy engine.py's `_TaskOrigin`-typed
`_inject(origin=...)` param under mypy — independent of `SpawnRecord.origin` itself being
plain `str`, per D1's "open set" requirement), `RunState.spawned_by: dict[str,
SpawnRecord] = {}`. Verified by `tests/test_spawn_provenance.py::TestStateJsonCompatibility`
(AC-10) round-tripping real `SpawnRecord` instances through JSON.

### AC-2 — `_inject` signature, keyword-only required `parent_task_id`
`src/agent_orchestrator/engine.py::Orchestrator._inject` signature matches TASK.md exactly:
`_inject(new, workflow, state, origin, route=None, *, parent_task_id, loop_id=None,
iteration=None)`.

Manual mypy regression check (done, then reverted): removed `parent_task_id=tid,` from the
emit call site (`engine.py:~2065`) and ran:
```
uv run mypy src/agent_orchestrator/engine.py
```
Result: `src/agent_orchestrator/engine.py:2065: error: Missing named argument
"parent_task_id" for "_inject" of "Orchestrator"  [call-arg]` — confirms mypy fails as
AC-2 requires. Restored the kwarg; re-ran `uv run mypy src` → back to the 4 pre-existing
`_version.py` errors only (see "Full validation" below), 0 new.

### AC-3 — emit provenance
`tests/test_spawn_provenance.py::TestEmitProvenance::test_emit_batch_records_spawned_by_for_every_child`
— static emitter `a` emits `d,e,f`; asserts `parent_task_id="a"`,
`parent_dispatch_cycle==state.tasks["a"].dispatch_cycle`, `origin="injected"`,
`injected_at==fixed_clock().isoformat()`, `loop_id is None`, `iteration is None` for all
three, via a locally clock-injected `Orchestrator` (the shared `make_orchestrator` fixture
does not pass `clock=`, so a local `_make_orch` helper was added in the test file).
Also `test_emitter_emits_zero_tasks_no_records` (HLD §8.1 edge-case table row).

### AC-4 — nested emit
`TestNestedEmitProvenance::test_nested_emit_parent_chain` — `a` emits `b` (itself
`emit_tasks: true`), `b` emits `c`. Asserts `spawned_by["b"].parent_task_id == "a"` and
`spawned_by["c"].parent_task_id == "b"`.

### AC-5 — loop
`TestLoopProvenance::test_loop_clones_record_gate_as_parent_per_iteration` — loop `L`,
body `[dev, gate]`, gate `gate`, gate verdicts `[True, True, False]` → 3 iterations.
Asserts `dev__iter2`/`gate__iter2` have `parent_task_id="gate"`, `origin="loop"`,
`loop_id="L"`, `iteration=2`; `dev__iter3`/`gate__iter3` have `parent_task_id="gate__iter2"`,
`iteration=3`; and `"dev"`/`"gate"` (iteration 1) have no `spawned_by` record.

### AC-6 — invariant (fresh + resumed)
`_assert_spawn_invariant` helper (`state.spawned_by[id].origin == state.tasks[id].origin`
for every `id` in `state.injected_tasks`) is applied at the end of the AC-3, AC-4, AC-5
tests (fresh runs) and inside
`TestResumePreservesProvenance::test_resume_after_injected_task_failure_preserves_spawned_by_and_origin`
after the resumed run completes.

### AC-7 — resume preserves (all three reset sites)
- `runstate.py::prepare_resume` (~278):
  `test_resume_after_injected_task_failure_preserves_spawned_by_and_origin` — injected
  task `e` fails, resume via `rs_store.load` + `prepare_resume` + `orch.run`; asserts
  `prepared.tasks["e"].origin == "injected"` (post wholesale-replace, pre-second-run) and
  `state2.spawned_by == spawned_by_before` (byte-identical, via pydantic `__eq__`) after
  the resumed run succeeds.
- `engine.py` missing-inputs reset (~1112... now ~1120 after the added carry-forward
  comment): `test_missing_inputs_reset_preserves_origin` — an injected task declares an
  input that is never produced; fails at the missing-inputs check; asserts
  `state.tasks["orphan"].origin == "injected"` (previously silently reset to the
  `TaskRunState` default `"static"`).
- `engine.py` worktree-collision reset (~1051, now ~1057):
  `TestWorktreeCollisionResetPreservesOrigin::test_injected_task_worktree_collision_preserves_origin`
  — small local copy of `test_engine_isolation.py::TestWorktreeCollision`'s real-git-repo
  technique, applied to an emit-injected task declaring `isolation: worktree`. Pre-creates
  a colliding worktree at the exact path `WorktreeManager.ensure()` targets, forcing
  `WorktreeCollisionError`; asserts `state.tasks["injected_iso"].origin == "injected"`.

### AC-8 — duplicate id in batch
`TestDuplicateIdProvenance::test_duplicate_id_in_batch_raises_and_leaves_no_record_for_the_collider`
— calls `Orchestrator._inject` directly (unit-level, matching `test_loop_construct.py`'s
`TestCloneBody` convention) with batch `[x, y, x_dup]` where `x_dup.id == "x"`. Asserts
`InjectionError` raised, `state.injected_tasks` ids == `{x, y}`, `state.spawned_by.keys()
== {x, y}` (the pre-collision "x" record from the first occurrence is untouched, matching
today's partial-injection behavior), and `workflow.tasks` has exactly one `"x"` entry.

### AC-9 — `max_parallel`
`TestMaxParallelProvenance::test_max_parallel_4_produces_same_spawned_by_as_serial` —
runs the AC-3 emit scenario once at `max_parallel=1` and once at `max_parallel=4` (same
task ids, same fixed clock, distinct `wf_id`/run dirs to avoid collision); asserts
`state_serial.spawned_by == state_parallel.spawned_by` (settle runs on the main thread per
ADR-0007, so the maps are identical).

### AC-10 — backward/forward compatibility
`TestStateJsonCompatibility`:
- `test_pre_epic_state_json_loads_with_empty_spawned_by` — a hand-built pre-epic
  `state.json` (no `spawned_by` key) loads via `RunState.model_validate_json` with
  `spawned_by == {}`.
- `test_new_state_json_loads_into_pre_epic_shape_without_error` — a real run's
  `state.model_dump_json()` (with a populated, non-empty `spawned_by`) loads without error
  into `_PreEpicRunStateShape`, a local stand-in for the pre-epic `RunState` shape (no
  `spawned_by` field) — confirmed pydantic v2's actual default is `extra="ignore"` (checked
  directly: `M.model_validate({"a": "x", "unexpected_field": 123})` succeeds; no
  `model_config` override exists anywhere in `models.py`, confirmed by grep).
- `test_spawn_record_with_unknown_origin_value_loads` — `origin: "future-kind"` loads
  without error into a real `RunState`/`SpawnRecord`.

### AC-11 — no behavior change
Grep (re-verifying A-6), python-source only, excluding `ui/runs.py`:
```
grep -rn "\.origin\b" --include="*.py" src/agent_orchestrator/ | grep -v "src/agent_orchestrator/ui/runs.py"
```
Result — exactly the 4 lines this ticket's own carry-forward writes added, plus one
docstring comment:
```
src/agent_orchestrator/runstate.py:293:                    origin=ts.origin,
src/agent_orchestrator/models.py:959: (docstring/comment text, not code)
src/agent_orchestrator/engine.py:1054: (comment)
src/agent_orchestrator/engine.py:1057:  status="failed", dispatch_cycle=ts_pre.dispatch_cycle, origin=ts_pre.origin
src/agent_orchestrator/engine.py:1124:  status="failed", origin=prev.origin if prev else "static"
```
None of these branch control flow on `.origin`'s value (no `==`/`!=`/`in` comparison) —
they are plain carry-forward assignments, matching A-6's "no *behavioral* reader outside
`ui/runs.py`" invariant. Pinned as an automated regression test:
`TestNoBehaviorChange::test_origin_has_no_behavioral_reader_outside_ui_runs` (greps for
`\.origin\s*(==|!=|\bin\b)` outside `ui/runs.py`, asserts no hits).

Full validation (see "Full validation" section below for the complete commands/counts).

### AC-12 — status.json unchanged
`TestStatusJsonUnchanged::test_status_json_shape_unchanged_for_emit_run` — runs an emit
workflow, reads `status.json`, asserts the top-level key set and every per-task key set
match the pre-existing fixed sets exactly, and asserts `"spawned_by" not in snapshot`
(confirms `runstate.py::write_status`, which this ticket did not touch, still produces the
byte-identical derived shape).

## Full validation

Baseline (clean tree, stashed my changes via `git stash push -u -m
"T-AZzgT8-baseline-check-763950"`, applied by SHA, dropped after reapply):
- `uv run pytest -q` → **4455 passed, 8 skipped**
- `uv run ruff check .` → 1 pre-existing error (`output/E-YAAGhk-.../repro_emit_lost_on_breaker_trip.py`, an architect scratch repro file, untouched by this ticket)
- `uv run ruff format --check .` → 1 pre-existing reformat needed (same file)
- `uv run mypy src` → 4 pre-existing errors, all in `src/agent_orchestrator/_version.py` (auto-generated version stamp file, unrelated to this ticket)

After this ticket's changes:
- `uv run pytest -q` → **4469 passed, 8 skipped** (+14 = exactly the new
  `tests/test_spawn_provenance.py` tests; **0 regressions, 0 new failures**)
- `uv run ruff check .` → same 1 pre-existing error, in the same pre-existing file; 0 new
- `uv run ruff format --check .` → same 1 pre-existing reformat, same file; 0 new (the new
  test file and all touched `src/` files are themselves `ruff format`-clean)
- `uv run mypy src` → same 4 pre-existing `_version.py` errors; **0 new errors**

Note: `uv run mypy tests/test_spawn_provenance.py` (checked as a standalone file target)
reports 8 `import-untyped` "missing library stubs or py.typed marker" notes for
`agent_orchestrator.*` imports — confirmed this is a pre-existing artifact of pointing
mypy at an individual test file outside the `src` root (every existing test file hits the
identical error the same way, e.g. `uv run mypy tests/test_dynamic_injection.py`), not a
defect in this ticket's code. The task's own completion gate is `mypy src`, which is
clean. (`mypy .` from the repo root also fails, on an unrelated pre-existing
`benchmarks/suites/.../conftest.py` duplicate-module collision — confirms `mypy src` is
the correct, working scope for this repo, not a gap introduced here.)

Test file: `tests/test_spawn_provenance.py` (14 tests, all passing):
`TestEmitProvenance` (2), `TestNestedEmitProvenance` (1), `TestLoopProvenance` (1),
`TestResumePreservesProvenance` (2), `TestWorktreeCollisionResetPreservesOrigin` (1),
`TestDuplicateIdProvenance` (1), `TestMaxParallelProvenance` (1),
`TestStateJsonCompatibility` (3), `TestNoBehaviorChange` (1), `TestStatusJsonUnchanged` (1).

## Deviations from TASK.md
None. Implementation follows the TASK.md pseudocode verbatim: the `spawned_by` write
sits in the same per-spec loop step as `injected_tasks.append` (R-1 coordination note for
`E-Grpp0X`); `route` is not carried across any reset (F-2, explicitly out of scope);
`dag.py` and `ui/` were not touched.

## Risks / Blockers
- Blockers: none. Implementation complete, all 12 ACs verified.
- Dependencies: none (first task). **Gate G1**: reviewer and tester sign-off still
  required before `T-M4qboy-run-graph-builder` starts consuming `state.spawned_by` — not
  self-certified here.
- Known, unchanged risk carried from TASK.md: concurrent edit of `_inject` by `E-Grpp0X`
  (R-1) — mitigated by keeping the `spawned_by` write in the same per-spec step as
  `injected_tasks.append`, as instructed.

## Next actions
1. Reviewer + tester sign-off (Gate G1).
2. `dev-epic` rolls up `EPIC.md`/epic `STATUS.md` and unblocks `T-M4qboy-run-graph-builder`.
