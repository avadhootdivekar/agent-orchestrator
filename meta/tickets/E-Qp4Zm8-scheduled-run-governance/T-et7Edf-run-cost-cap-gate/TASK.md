# TASK: T-et7Edf-run-cost-cap-gate

## Metadata
- Task ID: `T-et7Edf-run-cost-cap-gate`
- Epic ID: `E-Qp4Zm8-scheduled-run-governance`
- Owner: developer agent (engine-experienced)
- Created: 2026-11-10
- Last Updated: 2026-11-10
- Status: Draft
- Estimate: 3 days

## Requirements Mapping
- Requirement IDs: FR-3, NFR-2, NFR-4 (see `../EPIC.md`); owner decision D-5

## Description
The mid-run USD cost cap, as a general engine feature independent of schedules: `ao run --cost-cap-usd X` and
`ao resume --cost-cap-usd X`. The cap is persisted in `RunState` and is **sticky on resume**. It is enforced by a
**non-latching** built-in gate at every dispatch and every task boundary. On reach it records
`TrippedBreaker(id="builtin.cost_cap", condition="run_cost_usd", action="pause")`, sets resumable `failed`, and halts
through the existing drain path.

Read HLD §9 (all of it) and ADR-0025 D5/D6 first. Also read `breakers.py` (latching in `evaluate_breakers`,
`record_trip`), `engine.py` around the dispatch-prep budget gate (`BUILTIN_BUDGET_EXHAUSTED` site), the breaker call
site (`evaluate_breakers(workflow, state, ...)`), `_drain_remaining`, `_start_llm_lane`, and `run_options.py`
(`budget_total` is the template for a CLI-only option).
  **Sequencing (M4):** start only after T-Rk4Wn7 (A3-D3, stranding fix) has merged. Both edit `_settle_completed_task` and
  the run loop, and D3 changes the run-end semantics this gate must coexist with. **No E-Sc9Rt4 dependency.**

Files you own (edit only at the named sites):
- `src/agent_orchestrator/models.py`: add `BUILTIN_COST_CAP = "builtin.cost_cap"` next to the other built-in ids;
  add `RunState.cost_cap_usd: float | None = None`; add the defaulted per-task cost-visibility fields
  `TaskRunState.actuals_available: bool = False` and `TaskRunState.attempts_without_actuals: int = 0` (H2/M1, see AC13).
  There is **no** `cost_cap_history`: cap changes go through the existing `effective_settings_history` (M5b, AC14).
  Nothing else.
- `src/agent_orchestrator/engine.py`: (a) a module-level pure helper `cost_cap_reached(state) -> dict | None`;
  (b) one call in dispatch prep, **before** the budget gate/charge, for every dispatch kind; (c) one call at the task
  boundary immediately **before** `evaluate_breakers`, **only while non-terminal work remains** (H1, AC5); (d) `_start_llm_lane` cap = min(breaker cap,
  `state.cost_cap_usd`). No other engine edits.
- `src/agent_orchestrator/runstate.py`: `prepare_resume` carries `actuals_available` / `attempts_without_actuals`
  through the task reset, next to `cumulative_cost_usd` (`runstate.py:540-546`). Nothing else.
- `src/agent_orchestrator/executors/fake.py`: add `cost_usd` to `token_outputs` (M6, AC15). Nothing else.
- `src/agent_orchestrator/run_options.py`: one `Option("cost_cap_usd", "float", flag="--cost-cap-usd",
  help=..., ui_group=GROUP_CONCURRENCY, ui_label="Cost cap (USD)", sticky_on_resume=True)`. It is CLI-only: no `env`,
  no `config`. Also adds the `Option.sticky_on_resume: bool = False` attribute and a `SOURCE_STATE` provenance (M5a, AC14).
- `src/agent_orchestrator/cli.py`: the `run` and `resume` commands only. Thread the option, apply the sticky rule,
  record the change in `effective_settings_history`, and print the WARNING when the new cap is ≤ current spend. The
  `_effective_settings_snapshot` helper (`cli.py:876`) reports a kept cap as `SOURCE_STATE`.
- `tests/test_engine_cost_cap.py` (new), `tests/test_e2e_cli_cost_cap.py` (new)

Do NOT touch: `budget.py`, `breakers.py` (import `record_trip` only), `executors/` (except the `fake.py` seam above), `schedules/`, `service/`,
`ui/processes.py` (the registry entry makes it allow-listed automatically), `specs/workflow.schema.json`,
result-cache code.

## Acceptance Criteria
1. `ao run --cost-cap-usd 0.5` stores `state.cost_cap_usd == 0.5` and an `effective_settings_history` entry with the
   cap (`source: cli`). A value `<= 0`, NaN or inf ⇒ exit 2 with a clear message. **Validate the raw typer value before
   `resolve_option`** (L1): `resolve_option` treats falsy CLI values as unset (`run_options.py:13-15`), so `--cost-cap-usd 0`
   would otherwise silently mean "no cap". The test includes the `0` case.
2. **Sticky on resume:** `ao resume --run-id R` without the flag keeps the persisted cap (asserted by reading
   `state.json`), even if env/config would resolve a different value. No env/config layer exists for this option. With
   `--cost-cap-usd Y`, the cap becomes `Y` (absolute) and an `effective_settings_history` entry (`source: cli`, resume) is
   appended; the same raw-value validation as AC1 applies. If `Y <=` current
   run cost, the CLI prints `WARNING: cost cap $Y <= spend $S; the run will halt at its first dispatch` and proceeds.
3. `cost_cap_reached(state)` returns `{"cost_usd": c, "cap_usd": cap}` iff `state.cost_cap_usd is not None and
   compute_run_usage_totals(state).cost_usd >= cap`, and returns `None` otherwise. It is pure and **never calls the
   clock**. A stepping-clock test asserts that the clock call count is identical with and without the helper when the
   cap is unset (the `evaluate_breakers` no-op guarantee).
4. Dispatch gate: when the helper returns non-None at dispatch prep, the engine calls `record_trip(state,
   BUILTIN_COST_CAP, condition="run_cost_usd", action="pause", detail=...)`, sets `state.status = "failed"`, saves,
   and returns the existing halt signal, so no new task is dispatched. This covers static, injected, loop-clone and
   redispatch (conflict ladder / self-heal / quota requeue) dispatches, with one test each for injected and
   redispatch.
5. Boundary gate **(H1 — trip only with remaining work)**: after a settle whose cost crosses the cap **and while at
   least one task is still non-terminal (pending, ready or blocked)**, the run halts at that boundary (before
   `evaluate_breakers`), and in-flight siblings are drained (settled and persisted), never killed. If the settle that
   crosses the cap leaves **no** non-terminal task, the gate does **not** trip: no `builtin.cost_cap` record, the run
   finishes normally, and the overshoot is recorded by the ledger (`overshoot_usd`, HLD §10.2). The dispatch-prep check
   alone already prevents new work, so the boundary check is only an early halt. **Test:** a 1-task run whose only
   task crosses the cap ends `succeeded` with cost > cap and no `builtin.cost_cap` trip; a 2-task serial run crossing
   at task 1 halts `failed` with task 2 pending. A test with
   `max_parallel=2` asserts the drained sibling's cost is recorded and the final cost exceeds the cap by exactly that
   sibling's cost.
6. **Latch regression (the reason this task exists):** trip the cap → `ao resume` with no flag → zero dispatches, halt,
   and a *second* `builtin.cost_cap` `TrippedBreaker` appended (re-armed, not latched). `ao resume --cost-cap-usd
   <higher>` → the run continues and completes.
7. The gate never reaches the monitor consult: with a `recommend`-mode monitor configured, a cost-cap trip is
   never consulted (assert `monitor_decisions` is unchanged).
8. Sentinel: with `sentinel.llm.enabled` and `budget_pct=10`, `GenerationLane.run_cost_cap_usd ==
   min(run_cost_usd breaker thresholds ∪ {state.cost_cap_usd})`.
9. `launch_run(options={"cost_cap_usd": 1.5})` renders `--cost-cap-usd 1.5` (assert via `ui.processes._render_options`,
   read-only use). The run-options schema (`GET /api/schema/run-options`) lists it under "Concurrency & budget".
10. Backward compatibility: a run without the flag produces a `state.json` identical to before apart from the defaulted
    new fields (`"cost_cap_usd": null` and, per task, `"actuals_available": false, "attempts_without_actuals": 0`), and the
    full existing suite passes unmodified.
11. `ao status` (existing tripped-breakers trailer) shows `builtin.cost_cap` with cost and cap. No new code is needed
    if the trailer is generic, so assert it in the e2e.
12. Tests green, delta reported. ruff/format clean. mypy no regression. Coverage of the new helper and both call sites
    is 100 %.
13. **(H2, M1) Cost visibility persisted per task.** `_accumulate_actuals` (`engine.py:4052-4073`) sets
    `TaskRunState.actuals_available = True` (sticky OR) when a settle's `TaskResult.actuals_available` is true, and
    increments `attempts_without_actuals` for every settled attempt that reports none (including `killed` attempts, e.g.
    sentinel recovery kills, `sentinel_tick.py:570-590`). `prepare_resume` (`runstate.py:540`) carries both through the reset.
    Tests: a result with `cost_usd` sets the flag; a killed attempt bumps the counter and leaves cost unchanged; a
    resume preserves both; a resumed task that later reports does **not** clear the earlier blind-attempt count (the
    `cumulative_cost_usd > 0` heuristic is rejected because it would mask that).
14. **(M5) Sticky is a registry concept, not a CLI special case.** (a) `Option.sticky_on_resume: bool = False` and a
    `SOURCE_STATE` provenance; `cost_cap_usd` sets it, and `_effective_settings_snapshot` (`cli.py:876`) reports a kept
    cap as `SOURCE_STATE` ("kept from state"). Update the `Option.resumable` docstring (`run_options.py:85-87`). (b) No
    `cost_cap_history`: use the existing `effective_settings_history` (`models.py:1950-1951`) and keep
    `RunState.cost_cap_usd`, because `effective_settings` is observational (`cli.py:880`). (c) **Rejected with reason:**
    the review's proposed `RunState.cost_cap_source: "cli"|"schedule"`. A source value could not be forwarded by a launch,
    because only allow-listed registry options reach argv and a hidden flag would be silently dropped
    (`_render_options`). The generic-resume refusal is instead keyed on the authoritative fire record (`find_by_run_id`),
    implemented in T-8knqVe AC10. CLI `ao resume --cost-cap-usd` stays the explicit escape hatch (HLD §16) and prints a
    WARNING when the run is schedule-governed (T-nI4DBf supplies the lookup; no-op when no fire store exists).
15. **(M6) Scriptable cost for the CLI e2e tier.** `FakeExecutor.token_outputs` entries accept `cost_usd` (and set
    `actuals_available=True` when present). A named seam, a fixture `fake_cost_script(...)` in `tests/conftest.py`
    that patches the router's fake-executor factory (`executors/__init__.py:14`), lets a `CliRunner` test make each task
    cost e.g. $0.40. T-88wSLC and the A1.5/A2.6 CLI-e2e tier reference it. Test: a 3-task run scripted at $0.40/task
    reports `compute_run_usage_totals(...).cost_usd == 1.20`.
16. **(M4) Seam with T-Rk4Wn7 D3.** The cost-cap halt uses the immediate `DispatchPrep(signal="halt")` /
    `SettleResult(signal="halt")` path. D3's invariant "a run with runnable work does not end failed with pending
    tasks" **exempts breaker, budget and cost-cap halts** (stated in T-Rk4Wn7 by its owner, see STATUS cross-epic
    requests). Test: a cost-cap halt with pending tasks ends `failed` and does not re-enter the run loop.
17. **(L2)** Fix the `record_trip` docstring (`breakers.py:436`): "at most once, ever" is already false for budget
    built-ins (`engine.py:1763,2218`) and for this gate. The `ao status` tripped-breakers trailer (`cli.py:393`) collapses
    repeated ids to `builtin.cost_cap ×N`. If the docstring edit is judged outside the `breakers.py` boundary, record
    it as a T-Hq3vXd follow-up instead and say which in STATUS.

## Risks
- `engine.py` is 5 400 lines with stepping-clock tests. AC3 exists to keep them green, so do not call `self._clock()`
  inside the helper.
- Cost is only known at settle, so overshoot ≈ in-flight work (HLD §9.3). Do **not** attempt to kill in-flight tasks.
  That is explicitly rejected (ADR-0025 D6).
- `summary_cost_usd` is not in `RunState`. The gate deliberately excludes it (documented). Do not try to read
  summary meta from the engine.
- Concurrent engine epics: keep the diff to the four named sites, because T-lTW88y reviews the `git diff` scope.

## Dependencies
- Upstream (design review, M4): T-Rk4Wn7 (A3-D3, stranding fix) merged first; E-Sc9Rt4 T-Ri7Dz2's AC4 registry fix
  (`run_id` as a run-options `Option`) applied first, so `run_id` and `cost_cap_usd` follow one pattern.
  Otherwise no E-Sc9Rt4 dependency.
- Downstream: `T-Uz0Uol` (**depends on AC13's field**), `T-PpE5lf` (launcher passes the option), `T-nI4DBf` (resume
  passes a raised cap), `T-88wSLC`.

## Pseudocode / Algorithm
```text
def cost_cap_reached(state):
    cap = state.cost_cap_usd
    if cap is None: return None
    cost = compute_run_usage_totals(state).cost_usd
    return {"cost_usd": round(cost, 6), "cap_usd": cap} if cost >= cap else None

# dispatch prep (before budget gate):
hit = cost_cap_reached(state)
if hit: record_trip(state, BUILTIN_COST_CAP, "run_cost_usd", "pause", hit, self._clock, run_log)
        state.status = "failed"; self._runstate.save(state); return DispatchPrep(signal="halt")

# task boundary (before evaluate_breakers): same, returning SettleResult(signal="halt"),
#   but ONLY if any task is still pending/ready/blocked (H1); otherwise skip and let the run finish

# cli resume:
if cost_cap_usd is not None: existing.cost_cap_usd = cost_cap_usd; effective_settings_history.append(...)
# else: leave existing.cost_cap_usd untouched (sticky)
```

## Schemas / Interface Notes
- Interface / CLI: `ao run --cost-cap-usd FLOAT`; `ao resume --cost-cap-usd FLOAT`.
- Spec / data schema: `RunState.cost_cap_usd`; `TaskRunState.actuals_available`, `TaskRunState.attempts_without_actuals`; `TrippedBreaker{id:"builtin.cost_cap",
  condition:"run_cost_usd", action:"pause", detail:{cost_usd, cap_usd}}`.
- Triggers / events: the existing `breaker.trip` log event with `breaker_id="builtin.cost_cap"`.
- Artifacts: `.orchestrator/runs/<id>/state.json`.

## Handoff Boundary
- Upstream: HLD §9, ADR-0025 D5/D6.
- Downstream: `T-PpE5lf` depends on the option name `cost_cap_usd` and the trip id `builtin.cost_cap`. Both are now a
  public contract, so do not rename them.

## Artifacts
- Docs/comments: `meta/tickets/E-Qp4Zm8-scheduled-run-governance/T-et7Edf-run-cost-cap-gate/`
- Large outputs: N/A
