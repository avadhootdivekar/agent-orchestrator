# TASK: T-Sn1Adr-sentinel-adr-and-hld

## Metadata
- Task ID: `T-Sn1Adr-sentinel-adr-and-hld`
- Epic ID: `E-Sn7tL2-sentinel-long-running-overseer`
- Owner: `unassigned`
- Created: `2026-11-09`
- Last Updated: `2026-11-09`
- Status: `Done (owner sign-off recorded 2026-11-09)`
- Estimate: `< 3 days`
- Phase: P0
- Agent: architect (+ reviewer-opus / dev-critic for review)

## Requirements Mapping
- Requirement IDs: all FR/NFR (design authority); EPIC §0 D1–D4, §10 resolved decisions

## Description
Write ADR-0024 (check numbering) and `docs-md/sentinel-hld.md` from EPIC §4, incorporating spike results. Must cover:
main-thread tick + DRAIN timeout; control inbox schema `ao.ctl/v1` (all kinds, id/idempotency, quarantine); `killed`
status + recovery ladder state machine; soft-barrier semantics + carry-over; early checkpoint trigger; pending-edit
checker rules; LLM generation lifecycle (pack, handoff, memory); settings schema + precedence incl. live overrides
(source `live-override` in `state.effective_settings`, shared with E-aYG5vn T-zP28k9); separate injection cap;
authority split; observability events; DB revisit trigger; `ao ctl` command surface; dashboard control API + auth.

## Acceptance Criteria
1. ADR + HLD merged on the branch; every FR in EPIC §3 maps to an HLD section (traceability table).
2. JSON schemas drafted for `ao.ctl/v1` entries, `ao.sentinel.handoff/v1`, `ao.overseer.backlog/v1`, `state.sentinel` block.
3. EPIC §10 decisions recorded as resolved (owner approved 2026-11-09).
4. Owner sign-off recorded in epic STATUS.md before P1 code merges.

## Risks
- Scope creep into P4 items — keep them in a 'Deferred' section.

## Dependencies
- T-Sn0Spk

## Pseudocode / Algorithm
```text
N/A (design doc)
```

## Schemas / Interface Notes
- docs-md/adr/ADR-0024-sentinel-overseer-lane.md
- docs-md/sentinel-hld.md
- schemas inline in HLD appendix

## Handoff Boundary
- Upstream: T-Sn0Spk, EPIC.md
- Downstream: all implementation tasks

## Artifacts
- Docs/comments: `meta/tickets/E-Sn7tL2-sentinel-long-running-overseer/T-Sn1Adr-sentinel-adr-and-hld/`
- Large outputs: `N/A`

## Standard gates (every task)
- `pytest -q`, `ruff check . && ruff format --check .`, `mypy .` green; UI changes also `npm test` + `npm run build` in `ui/`.
- New behaviour off / absent settings ⇒ byte-identical behaviour (golden tests stay green).
- Work only in `./agent-orchestrator` on the epic branch; test against temp workspaces; never `install.sh` / restart `ao.service`.
- Update this TASK.md + STATUS.md and the epic STATUS.md rollup together.
