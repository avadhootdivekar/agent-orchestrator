# TASK: T-ov4d04-ao-self-update-command

## Metadata
- Task ID: `T-ov4d04-ao-self-update-command`
- Epic ID: `E-6396jp-workspace-customization-overlays-and-upgrade-flow`
- Owner: `TODO (unassigned)`
- Created: `2026-10-16`
- Last Updated: `2026-10-16`
- Status: `Draft`
- Priority: `P3 (backlog)`
- Estimate: `< 3 days` (split further if discovery says otherwise)

## Requirements Mapping
- Requirement IDs: see EPIC.md

## Re-triage
Re-triage 2026-10-16: superseded for now by the `/ao-upgrade` skill, which keeps install a deliberate human step (matches the self-hosting rule). Promote only if the skill proves flaky.

## Description
Codify the manual procedure (build UI bundle if UI changed -> `install.sh --force --yes` -> restart `ao.service` -> health check) as a safe, scripted command, e.g. `ao self-update` (human-invoked; never run by workflow runs, preserving the self-hosting rule):
- pre-flight: refuse (or require `--force`) when any workspace has an active run; list them,
- install from a chosen checkout/ref, record installed commit,
- restart the service, wait for hub + every registered workspace dashboard to report healthy and report installed version per dashboard,
- run overlay verification (T-ov2b02) for every registered workspace and print drift/conflicts,
- report what changed: version, templates changed, workspaces affected, any overlay needing attention.

## Acceptance Criteria
1. Dry-run mode prints the plan without changing anything.
2. Refuses when runs are active unless forced.
3. After success: output lists each workspace's dashboard port, HTTP health, installed version, overlay status.
4. Failure rolls back or leaves the previous install usable (documented behaviour).
5. Documented in `CLAUDE.md` install/deploy path; workspace CLAUDE.md self-hosting rule updated to state it is human-invoked only.

## Risks
- Self-hosting safety: must not be callable from inside a run (guard on run environment variable).

## Dependencies
T-ov2b02 for the overlay report (can ship without it first).

## Handoff Boundary
- Upstream: T-ov2b02 for the overlay report (can ship without it first).
- Downstream: other tasks in `E-6396jp-workspace-customization-overlays-and-upgrade-flow`

## Artifacts
- Docs/comments: `meta/tickets/E-6396jp-workspace-customization-overlays-and-upgrade-flow/T-ov4d04-ao-self-update-command/`
- Large outputs: `output/...`
