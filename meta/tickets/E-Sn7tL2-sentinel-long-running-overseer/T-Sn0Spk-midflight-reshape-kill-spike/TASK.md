# TASK: T-Sn0Spk-midflight-reshape-kill-spike

## Metadata
- Task ID: `T-Sn0Spk-midflight-reshape-kill-spike`
- Epic ID: `E-Sn7tL2-sentinel-long-running-overseer`
- Owner: `unassigned`
- Created: `2026-11-09`
- Last Updated: `2026-11-09`
- Status: `Draft`
- Estimate: `< 3 days`
- Phase: P0 (blocking for P2)
- Agent: developer (spike) + reviewer

## Requirements Mapping
- Requirement IDs: FR-5, FR-8, FR-12, NFR-8 (de-risk)

## Description
Throwaway spike in a temp workspace (no merge of spike code unless trivially reusable) answering four questions,
with measured numbers, and a go / go-with-changes / no-go recommendation recorded in the epic STATUS.md:
(a) can `build_dag` + `_recompute_order` + `_predecessors` + `_ready_ids` run correctly with tasks **in flight**
    (inject additive tasks, add/remove edges between pending tasks, while 3 FakeExecutor tasks are running)?
(b) cooperative kill of a real `claude -p` (or a stand-in subprocess tree) via process group, and is stdout/stream-json
    growth a reliable liveness signal? Sample real run logs under `ao-runner-ao/.orchestrator/runs/*/` for the longest
    silent gaps of healthy tasks (calibrates `idle_kill_minutes`).
(c) per-tick cost of the main-thread tick on a synthetic 1k-task RunState (target < 50 ms) and `RunState.save` cost.
(d) what SIGTERM to `ao run` does today (graceful unwind or hard death; `cancel_fn` is unwired, `engine.py:371`).

## Acceptance Criteria
1. Spike note `output/E-Sn7tL2-sentinel-long-running-overseer/spike/RESULT.md` answers (a)–(d) with commands, numbers and code refs.
2. For (a): explicit list of invariants that break (if any) and the minimal engine change to fix them; recommends soft-barrier design or the §4.4.4 fallback.
3. For (b): histogram/percentiles of silent gaps in healthy tasks; recommended default `idle_kill_minutes`.
4. Epic STATUS.md updated with the go/no-go and any changes to FR-8/FR-10/FR-12.

## Risks
- Spike shows in-flight rebuild unsafe → P2 uses fallback (§4.4.4); record, do not force.

## Dependencies
- None

## Pseudocode / Algorithm
```text
wf = temp workspace with 6 slow fake tasks + 1 emitter
engine.run(..) with hook after 3 dispatched: inject 2 tasks + add edge pendingA->pendingB, rebuild graph
assert: no double dispatch, running tasks unaffected, order deterministic, resume of saved state equal
measure: tick() over 1k synthetic tasks x 1000 iterations
```

## Schemas / Interface Notes
- No interfaces committed; findings feed T-Sn1Adr.

## Handoff Boundary
- Upstream: EPIC §2 findings
- Downstream: T-Sn1Adr (design), T-Sn4Rec (kill design), T-Sn6Brr (go/no-go)

## Artifacts
- Docs/comments: `meta/tickets/E-Sn7tL2-sentinel-long-running-overseer/T-Sn0Spk-midflight-reshape-kill-spike/`
- Large outputs: `output/E-Sn7tL2-sentinel-long-running-overseer/spike/`

## Standard gates (every task)
- `pytest -q`, `ruff check . && ruff format --check .`, `mypy .` green; UI changes also `npm test` + `npm run build` in `ui/`.
- New behaviour off / absent settings ⇒ byte-identical behaviour (golden tests stay green).
- Work only in `./agent-orchestrator` on the epic branch; test against temp workspaces; never `install.sh` / restart `ao.service`.
- Update this TASK.md + STATUS.md and the epic STATUS.md rollup together.
