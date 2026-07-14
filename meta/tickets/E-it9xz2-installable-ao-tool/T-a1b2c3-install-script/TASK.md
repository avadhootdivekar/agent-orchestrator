# TASK: T-a1b2c3-install-script

## Metadata
- Task ID: `T-a1b2c3-install-script`
- Epic ID: `E-it9xz2-installable-ao-tool`
- Owner: dev-epic
- Created: 2026-06-16
- Last Updated: 2026-06-16
- Status: Done
- Estimate: < 1 day

## Requirements Mapping
- Requirement IDs: FR-1, NFR-2

## Description
Write `install.sh` at the repo root — a one-command, idempotent installer.

## Acceptance Criteria
1. Script checks for `uv`; installs it via the official installer if missing.
2. Installs `ao` globally via `uv tool install --editable .`.
3. Prints a success message with next steps.
4. Safe to re-run (idempotent).
5. Contains a comment explaining how to adapt for web-hosted distribution.
6. `bash -n install.sh` reports no syntax errors.

## Artifacts
- `install.sh` (repo root)
