# TASK: T-SnXAcc-e2e-and-live-smoke

## Metadata
- Task ID: `T-SnXAcc-e2e-and-live-smoke`
- Epic ID: `E-Sn7tL2-sentinel-long-running-overseer`
- Owner: `unassigned`
- Created: `2026-11-09`
- Last Updated: `2026-11-09`
- Status: `In Progress`
- Estimate: `< 3 days`
- Phase: P3 close
- Agent: full-tester / tester

## Requirements Mapping
- Requirement IDs: FR-23, NFR-7, NFR-8, NFR-13, EPIC §11

## Description
Full e2e matrix via `CliRunner` + FakeExecutor (EPIC §11 list) and the FR-23 live smoke with real Claude CLI in a temp
workspace: short TTL to force ≥3 rotations, scripted stalled task (killed + retried), idle-slot scenario (backlog +
early checkpoint), one LLM pending edit, one live `max_parallel` change from the dashboard. Collect evidence:
ledger, handoffs, utilisation before/after vs EPIC §1 baseline, cost split work/overseer/sentinel, combined overhead.

## Acceptance Criteria
1. All §11 e2e scenarios present and green.
2. Evidence pack under `output/E-Sn7tL2-sentinel-long-running-overseer/smoke/` with a README summarising numbers.
3. HLD 'Deviations' section reconciled with findings.

## Progress (2026-11-09)
- AC1 met for the non-live part: all §11 e2e scenarios mapped + green (`MATRIX.md`, `tests/test_sentinel_e2e_matrix.py`).
- AC3 (HLD Deviations) met 2026-11-09 (W11).
- AC2 (live smoke evidence pack) OPEN — live smoke awaits owner budget confirmation; exact steps in `HANDOFF.md` (same folder).

## Risks
- Live smoke cost — cap run budget; use haiku/sonnet for work units.

## Dependencies
- T-Sn7Bkl
- T-Sn9Pow
- E-aYG5vn T-mQs7CQ (shared run-page checks)

## Pseudocode / Algorithm
```text
N/A
```

## Schemas / Interface Notes
- N/A

## Handoff Boundary
- Upstream: all P1–P3 tasks
- Downstream: T-SnXDoc, epic close

## Artifacts
- Docs/comments: `meta/tickets/E-Sn7tL2-sentinel-long-running-overseer/T-SnXAcc-e2e-and-live-smoke/`
- Large outputs: `output/E-Sn7tL2-sentinel-long-running-overseer/`

## Standard gates (every task)
- `pytest -q`, `ruff check . && ruff format --check .`, `mypy .` green; UI changes also `npm test` + `npm run build` in `ui/`.
- New behaviour off / absent settings ⇒ byte-identical behaviour (golden tests stay green).
- Work only in `./agent-orchestrator` on the epic branch; test against temp workspaces; never `install.sh` / restart `ao.service`.
- Update this TASK.md + STATUS.md and the epic STATUS.md rollup together.
