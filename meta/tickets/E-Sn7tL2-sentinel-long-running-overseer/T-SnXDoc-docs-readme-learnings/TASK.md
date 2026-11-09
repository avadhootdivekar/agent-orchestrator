# TASK: T-SnXDoc-docs-readme-learnings

## Metadata
- Task ID: `T-SnXDoc-docs-readme-learnings`
- Epic ID: `E-Sn7tL2-sentinel-long-running-overseer`
- Owner: `unassigned`
- Created: `2026-11-09`
- Last Updated: `2026-11-09`
- Status: `Draft`
- Estimate: `< 3 days`
- Phase: each phase close
- Agent: developer / architect

## Requirements Mapping
- Requirement IDs: Docs gates (EPIC §11)

## Description
Reconcile `docs-md/sentinel-hld.md`, ADR-0024, both builtin template READMEs (settings, params, recommended
defaults), `docs-md/guide-dynamic-task-injection.md` (soft barrier, inbox, sentinel origin), CLI reference (`ao ctl`,
new flags), dashboard docs, `.ao/config.yaml` sample; add learnings to `meta/learnings.md` / `learning-compact.md`
only where genuinely new; final ticket + STATUS rollup sync. After FR-23 evidence: flip mechanical sentinel default to
on in both builtin templates (decision §10.1), LLM stays opt-in.

## Acceptance Criteria
1. Docs match shipped behaviour (reviewer check).
2. Default flip done only after evidence is recorded.
3. All ticket files consistent.

## Risks
- Docs drift across phases — run at each phase close, not only at the end.

## Dependencies
- phase completion

## Pseudocode / Algorithm
```text
N/A
```

## Schemas / Interface Notes
- N/A

## Handoff Boundary
- Upstream: each phase
- Downstream: epic close

## Artifacts
- Docs/comments: `meta/tickets/E-Sn7tL2-sentinel-long-running-overseer/T-SnXDoc-docs-readme-learnings/`
- Large outputs: `N/A`

## Standard gates (every task)
- `pytest -q`, `ruff check . && ruff format --check .`, `mypy .` green; UI changes also `npm test` + `npm run build` in `ui/`.
- New behaviour off / absent settings ⇒ byte-identical behaviour (golden tests stay green).
- Work only in `./agent-orchestrator` on the epic branch; test against temp workspaces; never `install.sh` / restart `ao.service`.
- Update this TASK.md + STATUS.md and the epic STATUS.md rollup together.
