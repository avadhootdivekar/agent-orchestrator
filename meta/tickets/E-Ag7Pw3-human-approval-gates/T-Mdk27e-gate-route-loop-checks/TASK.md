# TASK: T-Mdk27e-gate-route-loop-checks

## Metadata
- Task ID: `T-Mdk27e-gate-route-loop-checks`
- Epic ID: `E-Ag7Pw3-human-approval-gates`
- Owner: developer
- Created: 2026-10-05 (rev 3, Gate 2 R-10)
- Last Updated: 2026-10-05
- Status: Draft
- Estimate: 1 day (stage E, off the critical path)

## Requirements Mapping
- Requirement IDs: FR-1 (W-AG-7), FR-7 (routes/loops/emit at engine level), NFR-2
- Design: HLD §9.9.7 (`gate_route_exposure`), §9.1.4 (W-AG-7 row; W-AG-8 withdrawn), §9.1.5–§9.1.7,
  §9.9.6 (`derivable_not_taken`), §17 rules 1 and 8, §7.4 TM-12/TM-29, RR-12

## Description
Gate × routing/loop checks, moved off the critical chain at Gate 2 (R-10):

1. **W-AG-7 (validate time):** new module `approvals/spec_warnings.py` with
   `gate_route_exposure(workflow) -> list[(gate, router, route)]` — for each router R and each route r,
   simulate `route_decisions = {R: all routes except r}` with R "succeeded" and report every gate in
   `policy.derivable_not_taken(...)` over `policy.quiet_graph` (reuse, never re-implement, the derivation
   `begin_session` trusts). Wire it into `approvals/spec_rules.validate_approval_gates` (the W-AG-7 call
   lines only) with the message of HLD §9.1.4. **W-AG-8 is not implemented** (withdrawn in rev 3; the
   guidance is documentation: §17 rule 7, RR-14).
2. **Engine-level routes/loops/manifest integration tests** (moved from `T-otHPGB`):
   `tests/approvals/test_engine_loops_routes.py` — a gate in an unselected route cone never gets an
   `approvals` entry or audit line; a gate `not_taken` by join propagation never opens; a loop-body gate
   over 3 iterations yields 3 distinct `request_id`s keyed `gate`, `gate__iter2`, `gate__iter3`, each built
   from the static base gate's spec; `test_loop_gate_iteration2_waits_for_in_body_producer` proves each
   clone opens only after that iteration's producer settled; an emitter whose manifest contains `approval`
   fails with `manifest_error` and the run halts.

Files — new: `approvals/spec_warnings.py`, `tests/approvals/test_spec_warnings.py`,
`tests/approvals/test_engine_loops_routes.py`. Changed (new-in-epic module): `approvals/spec_rules.py` (the
W-AG-7 wiring lines). Shared files of §26: none. **Exclusive files during stage E** (HLD §22.3): exactly
these; it starts after `T-AGO2L6` has merged `spec_rules.py` and after `T-vwIpSw` (engine) has merged.

## Acceptance Criteria
1. `test_w_ag_7_gate_in_router_cone`: W-AG-7 fires for a gate inside a route cone and for a gate that
   becomes `not_taken` through `join: all` propagation from a cone; it is silent for a gate placed after a
   `join: any` aggregator and for a workflow without routers; the message names the router and the route.
2. `gate_route_exposure` calls `policy.derivable_not_taken` (spy) — no second implementation of the routing
   rules; it logs nothing (quiet graph).
3. The HLD §13 example produces exactly W-AG-3 and W-AG-9 from `ao validate` (CliRunner).
4. `test_engine_loops_routes.py` cases of the Description pass with `FakeExecutor` and the engine helpers
   of `T-vwIpSw` (`tests/approvals/engine_helpers.py`).
5. Targeted tests green; `ruff`/`mypy` clean on the new module; full suite at the stage-E checkpoint;
   a `reviewer`-agent review is recorded in STATUS.md.

## Risks
- The simulation must use exactly the engine's cone and join rules; a divergence produces wrong warnings
  (mitigated by AC2: the shared function is reused, not copied).

## Dependencies
- `T-AGO2L6` (`spec_rules.py`), `T-1MgGb4` (`derivable_not_taken`, `quiet_graph`), `T-vwIpSw` (engine and
  `engine_helpers.py`).

## Pseudocode / Algorithm
```text
FOR router R IN workflow.branches: FOR route r IN R.routes:
    decisions = {R.id: [x for x in R.routes if x != r]}
    nt = derivable_not_taken(workflow, quiet_graph(workflow), cones, decisions, {R.router_task_id: succeeded})
    FOR gate IN gates & nt: warn W-AG-7(gate, R, r)
```

## Schemas / Interface Notes
- `gate_route_exposure(workflow) -> list[tuple[str, str, str]]`; warning text: HLD §9.1.4 W-AG-7.

## Verification
```
WT=/usr/avadhoot/mounted/agent-orchestrator/.claude/worktrees/agent-a513b4d78ac3b20bb
PY=/usr/avadhoot/mounted/agent-orchestrator/.venv/bin/python
cd $WT && $PY -m pytest -o pythonpath=src -p no:cacheprovider -q tests/approvals/test_spec_warnings.py tests/approvals/test_engine_loops_routes.py tests/approvals/test_spec_validation.py
cd $WT && $PY -m ruff check src/agent_orchestrator/approvals tests/approvals && $PY -m mypy src/agent_orchestrator/approvals
```

## Handoff Boundary
- Upstream: `T-AGO2L6`, `T-1MgGb4`, `T-vwIpSw`.
- Downstream: `T-pdLR96`.

## Artifacts
- Code and tests as listed; evidence in STATUS.md.
