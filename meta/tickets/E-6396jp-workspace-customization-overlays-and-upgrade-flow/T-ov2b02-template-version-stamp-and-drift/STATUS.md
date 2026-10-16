# STATUS

- ID: `T-ov2b02-template-version-stamp-and-drift`
- Updated At: `2026-10-16`
- State: `Draft`
- Owner: `TODO (unassigned)`

## This update
- Ticket created; not started.
- Re-triage 2026-10-16: REFRAMED to the one change worth making in the tool: record a baseline content hash (and template version) for `keep_existing` assets at scaffold time, so `ao` can auto-refresh workspace files the workspace never modified and only flag the modified ones. Today those assets never refresh, silently staleing every workspace's instructions after an upgrade. Reporting/status UI is secondary.

## Evidence
- N/A

## Risks / Blockers
- See TASK.md.

## Next actions
1. Triage and pick when scheduled.
