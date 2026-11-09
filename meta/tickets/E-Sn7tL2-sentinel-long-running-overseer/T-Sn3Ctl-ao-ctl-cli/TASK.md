# TASK: T-Sn3Ctl-ao-ctl-cli

## Metadata
- Task ID: `T-Sn3Ctl-ao-ctl-cli`
- Epic ID: `E-Sn7tL2-sentinel-long-running-overseer`
- Owner: `unassigned`
- Created: `2026-11-09`
- Last Updated: `2026-11-09`
- Status: `Draft`
- Estimate: `< 3 days`
- Phase: P1
- Agent: developer

## Requirements Mapping
- Requirement IDs: FR-3 (writer side), FR-7, FR-22 (CLI)

## Description
New `ao ctl` command group (EPIC §4.8): `status`, `set`, `pause-llm`, `resume-llm`, `kill`, `note`, `release`, `cancel`.
Every mutating subcommand writes exactly one `ao.ctl/v1` entry atomically into the run's `control/inbox/` (actor = OS
user) and, with `--wait`, waits for the consume/reject record and prints the outcome. `status` reads `state.json`
(running/limit/ceiling, utilisation, per-task liveness age, ladder state, backlog, LLM generation + spend, pending
inbox, recent actions) with `--json`. Run resolution by run id prefix like other commands. `kill`/`release` return
`not_enabled_yet` until T-Sn4Rec/T-Sn7Bkl land.

## Acceptance Criteria
1. CliRunner e2e against a temp workspace with a live engine thread: set/pause/note/cancel round-trip with `--wait`.
2. Invalid keys/values rejected client-side using the registry schema (same validation as server side).
3. `ao ctl status --json` schema documented and covered by a snapshot test.
4. CLI reference docs updated.

## Risks
- Run directory resolution for runs started by the service vs. CLI — reuse existing run lookup helpers.

## Dependencies
- T-Sn3Tck

## Pseudocode / Algorithm
```text
ao ctl set RUN k=v... -> entry{kind:'set', payload:{k:v}} -> write tmp -> rename -> [--wait poll state.sentinel.inbox_consumed / inbox-rejected]
```

## Schemas / Interface Notes
- CLI: `ao ctl <sub> <run-id> [args] [--reason] [--wait] [--json]`

## Handoff Boundary
- Upstream: T-Sn3Tck inbox
- Downstream: T-Sn5Ui1 reuses the same entry builder (shared module `control.py`)

## Artifacts
- Docs/comments: `meta/tickets/E-Sn7tL2-sentinel-long-running-overseer/T-Sn3Ctl-ao-ctl-cli/`
- Large outputs: `N/A`

## Standard gates (every task)
- `pytest -q`, `ruff check . && ruff format --check .`, `mypy .` green; UI changes also `npm test` + `npm run build` in `ui/`.
- New behaviour off / absent settings ⇒ byte-identical behaviour (golden tests stay green).
- Work only in `./agent-orchestrator` on the epic branch; test against temp workspaces; never `install.sh` / restart `ao.service`.
- Update this TASK.md + STATUS.md and the epic STATUS.md rollup together.
