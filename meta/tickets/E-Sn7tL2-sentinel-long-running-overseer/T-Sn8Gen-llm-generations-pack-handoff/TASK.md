# TASK: T-Sn8Gen-llm-generations-pack-handoff

## Metadata
- Task ID: `T-Sn8Gen-llm-generations-pack-handoff`
- Epic ID: `E-Sn7tL2-sentinel-long-running-overseer`
- Owner: `unassigned`
- Created: `2026-11-09`
- Last Updated: `2026-11-09`
- Status: `Draft`
- Estimate: `< 3 days`
- Phase: P3
- Agent: developer

## Requirements Mapping
- Requirement IDs: FR-14, FR-17, FR-18 (LLM part)

## Description
`SentinelDriver` ABC (`start(pack_dir, limits)->Handle`, `poll`, `stop`) + `ClaudeCliSentinelDriver`; 1-worker lane
executor outside `max_parallel`; generation dirs `sentinel/gNN/{pack/,handoff.json,report.md,memory-append.md}`;
deterministic pack assembly with size caps; tool-owned `memory.md` merge (cap, pinned items, seq-tagged expiry);
rotation by TTL / max triggers / tokens / explicit handoff; ungraceful handling; generation task rows `sentinel-gNN`
with cost in `run_cost_usd`; sentinel budget stops new generations; consecutive-rejection auto-pause; triggers with
cooldowns + per-hour caps; `sentinel-contract.md` rendered from the schemas.

## Acceptance Criteria
1. Pack bytes deterministic under fixed clock; caps enforced (tests).
2. Rotation e2e with scripted fake driver: ≥3 generations, handoff carried, ungraceful path covered.
3. Cost rows counted in `run_cost_usd` and `ao usage`; budget cap stops generations, ticks continue.
4. Resume restores generation counter, memory, last handoff; no duplicate proposals.

## Risks
- Driver output parsing of cost — reuse executor usage parsing.

## Dependencies
- T-Sn3Tck
- T-Sn2Set

## Pseudocode / Algorithm
```text
tick: if needs_llm_trigger and not alive and budget_ok and not paused: start gen N+1 with pack
poll: alive? ttl? -> stop; on exit: validate handoff, merge memory, record cost row, ledger events
```

## Schemas / Interface Notes
- `ao.sentinel.handoff/v1`
- pack manifest
- SentinelDriver ABC

## Handoff Boundary
- Upstream: T-Sn3Tck
- Downstream: T-Sn9Pow

## Artifacts
- Docs/comments: `meta/tickets/E-Sn7tL2-sentinel-long-running-overseer/T-Sn8Gen-llm-generations-pack-handoff/`
- Large outputs: `N/A`

## Standard gates (every task)
- `pytest -q`, `ruff check . && ruff format --check .`, `mypy .` green; UI changes also `npm test` + `npm run build` in `ui/`.
- New behaviour off / absent settings ⇒ byte-identical behaviour (golden tests stay green).
- Work only in `./agent-orchestrator` on the epic branch; test against temp workspaces; never `install.sh` / restart `ao.service`.
- Update this TASK.md + STATUS.md and the epic STATUS.md rollup together.
