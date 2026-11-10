# STATUS

- ID: `T-Sn3Ctl-ao-ctl-cli`
- Updated At: `2026-11-09`
- State: `Done`
- Owner: `unassigned`

## This update
- By: claude-opus-5-5 · Role: architect · Date: 2026-11-09 · Comment: Task created from epic breakdown (owner-approved design).

- By: claude-sonnet-5-5 · Role: developer · Date: 2026-11-09 · Comment: **Done** — `ao ctl` group (`ctl_cli.py`, registered in cli.py) + shared writer `control.py` (atomic `ao.ctl/v1` entries, actor `cli:<os user>`, run-prefix lookup, `--wait`, `status --json` `ao.ctl.status/v1`). Docs: `docs-md/sentinel-hld.md` §12.1, README command list. `release` returns `not_enabled_yet` (exit 5) until T-Sn7Bkl; `kill` enablement is T-Sn4Rec's (engine side).

## Evidence
- `tests/test_ctl_cli.py`: 14 passed (builder/atomic write, client-side rejection, prefix resolution, wait timeout w/ fake clock, status snapshot, CLI error paths, live-engine CliRunner round-trip set/pause/note/kill/release/cancel with `--wait`).
- Note: ceiling is only validated client-side when the run snapshot records it (it does not yet); the engine's `CTL-CEILING` quarantine covers it (exit 3).

## Risks / Blockers
- None

## Next actions
1. Pick up after: T-Sn3Tck
