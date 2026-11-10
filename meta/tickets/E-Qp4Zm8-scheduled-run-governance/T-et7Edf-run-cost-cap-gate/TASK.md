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
(`budget_total` is the template for a CLI-only option). **No E-Sc9Rt4 dependency.**

Files you own (edit only at the named sites):
- `src/agent_orchestrator/models.py`: add `BUILTIN_COST_CAP = "builtin.cost_cap"` next to the other built-in ids;
  add `CostCapChange(BaseModel){at: str, value: float, source: Literal["start","resume"]}`; add
  `RunState.cost_cap_usd: float | None = None` and `RunState.cost_cap_history: list[CostCapChange] = []`. Nothing else.
- `src/agent_orchestrator/engine.py`: (a) a module-level pure helper `cost_cap_reached(state) -> dict | None`;
  (b) one call in dispatch prep, **before** the budget gate/charge, for every dispatch kind; (c) one call at the task
  boundary immediately **before** `evaluate_breakers`; (d) `_start_llm_lane` cap = min(breaker cap,
  `state.cost_cap_usd`). No other engine edits.
- `src/agent_orchestrator/run_options.py`: one `Option("cost_cap_usd", "float", flag="--cost-cap-usd",
  help=..., ui_group=GROUP_CONCURRENCY, ui_label="Cost cap (USD)")`. It is CLI-only: no `env`, no `config`.
- `src/agent_orchestrator/cli.py`: the `run` and `resume` commands only. Thread the option, apply the sticky rule,
  append `cost_cap_history`, and print the WARNING when the new cap is ≤ current spend.
- `tests/test_engine_cost_cap.py` (new), `tests/test_e2e_cli_cost_cap.py` (new)

Do NOT touch: `budget.py`, `breakers.py` (import `record_trip` only), `executors/`, `schedules/`, `service/`,
`ui/processes.py` (the registry entry makes it allow-listed automatically), `specs/workflow.schema.json`,
result-cache code.

## Acceptance Criteria
1. `ao run --cost-cap-usd 0.5` stores `state.cost_cap_usd == 0.5` and `cost_cap_history == [{source: "start", ...}]`.
   A value `<= 0`, NaN or inf ⇒ exit 2 with a clear message.
2. **Sticky on resume:** `ao resume --run-id R` without the flag keeps the persisted cap (asserted by reading
   `state.json`), even if env/config would resolve a different value. No env/config layer exists for this option. With
   `--cost-cap-usd Y`, the cap becomes `Y` (absolute) and a `{source: "resume"}` entry is appended. If `Y <=` current
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
5. Boundary gate: after a settle whose cost crosses the cap, the run halts at that boundary (before
   `evaluate_breakers`), and in-flight siblings are drained (settled and persisted), never killed. A test with
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
10. Backward compatibility: a run without the flag produces a `state.json` identical to before apart from
    `"cost_cap_usd": null, "cost_cap_history": []`, and the full existing suite passes unmodified.
11. `ao status` (existing tripped-breakers trailer) shows `builtin.cost_cap` with cost and cap. No new code is needed
    if the trailer is generic, so assert it in the e2e.
12. Tests green, delta reported. ruff/format clean. mypy no regression. Coverage of the new helper and both call sites
    is 100 %.

## Risks
- `engine.py` is 5 400 lines with stepping-clock tests. AC3 exists to keep them green, so do not call `self._clock()`
  inside the helper.
- Cost is only known at settle, so overshoot ≈ in-flight work (HLD §9.3). Do **not** attempt to kill in-flight tasks.
  That is explicitly rejected (ADR-0025 D6).
- `summary_cost_usd` is not in `RunState`. The gate deliberately excludes it (documented). Do not try to read
  summary meta from the engine.
- Concurrent engine epics: keep the diff to the four named sites, because T-lTW88y reviews the `git diff` scope.

## Dependencies
- None upstream. Downstream: `T-PpE5lf` (launcher passes the option), `T-nI4DBf` (resume passes a raised cap),
  `T-88wSLC`.

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

# task boundary (before evaluate_breakers): same, returning SettleResult(signal="halt")

# cli resume:
if cost_cap_usd is not None: existing.cost_cap_usd = cost_cap_usd; history.append(resume)
# else: leave existing.cost_cap_usd untouched (sticky)
```

## Schemas / Interface Notes
- Interface / CLI: `ao run --cost-cap-usd FLOAT`; `ao resume --cost-cap-usd FLOAT`.
- Spec / data schema: `RunState.cost_cap_usd`, `RunState.cost_cap_history[]`; `TrippedBreaker{id:"builtin.cost_cap",
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
