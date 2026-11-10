# TASK: T-Sn7Bkl-backlog-and-early-checkpoint

## Metadata
- Task ID: `T-Sn7Bkl-backlog-and-early-checkpoint`
- Epic ID: `E-Sn7tL2-sentinel-long-running-overseer`
- Owner: `developer (claude-sonnet-5-5)`
- Created: `2026-11-09`
- Last Updated: `2026-11-09`
- Status: `Done`
- Estimate: `< 3 days`
- Phase: P2
- Agent: developer (engine + overseer template/tool)

## Requirements Mapping
- Requirement IDs: FR-9, FR-10, FR-19; D1

## Description
1. Overseer template: checkpoint verdict may emit `outputs/backlog.json` (`ao.overseer.backlog/v1`); tool checker
validates entries as wave units; instructions (`20-checkpoint.md`) + contract updated (backlog, carry-over handling);
checkpoints marked `barrier: soft`.
2. Engine tick: L1 release when `running < limit` and nothing ready → inject `s-NN-<slug>` via shared checker; expiry
at next checkpoint.
3. Early checkpoint trigger (FR-10) rewriting pending checkpoint deps to settled predecessors, passing `carry_over[]`
into its digest; ≤1 early checkpoint in flight; counts toward `max_waves`.
4. Separate breaker `sentinel_injected_task_count` with default formula.

## Acceptance Criteria
1. Heavy-tail e2e fixture (lognormal durations, P=6): idle-slot-seconds-with-work-available reduced ≥50% vs disabled (NFR-13), report numbers.
2. Backlog entries expire; expired entries never released; checker rejects invalid entries.
3. Early checkpoint never re-schedules carry-over units (checker rule + test).
4. Overseer tool unit tests for backlog schema; template README updated.

## Risks
- Duplicated work between early checkpoint and stragglers — carry-over rule + digest visibility.

## Dependencies
- T-Sn6Brr

## Pseudocode / Algorithm
```text
tick: if idle>0 and not ready: release(backlog.pop_by_priority())
       elif backlog empty and idle>=min_idle for grace and frac_settled(ck)>=f and no early ck in flight:
            edit_pending(ck.depends_on = settled); digest.carry_over = unsettled
```

## Schemas / Interface Notes
- `ao.overseer.backlog/v1`
- digest `carry_over[]`, `sentinel{...}` block

## Handoff Boundary
- Upstream: T-Sn6Brr
- Downstream: T-SnXAcc

## Artifacts
- Docs/comments: `meta/tickets/E-Sn7tL2-sentinel-long-running-overseer/T-Sn7Bkl-backlog-and-early-checkpoint/`
- Large outputs: `N/A`

## Standard gates (every task)
- `pytest -q`, `ruff check . && ruff format --check .`, `mypy .` green; UI changes also `npm test` + `npm run build` in `ui/`.
- New behaviour off / absent settings ⇒ byte-identical behaviour (golden tests stay green).
- Work only in `./agent-orchestrator` on the epic branch; test against temp workspaces; never `install.sh` / restart `ao.service`.
- Update this TASK.md + STATUS.md and the epic STATUS.md rollup together.
