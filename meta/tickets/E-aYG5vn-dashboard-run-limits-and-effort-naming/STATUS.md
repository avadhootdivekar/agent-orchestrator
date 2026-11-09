# STATUS

- ID: `E-aYG5vn-dashboard-run-limits-and-effort-naming`
- Updated At: `2026-11-09`
- State: `Draft` (ready for implementation)
- Owner: `avadhoot`

## This update
- By: claude-opus-5-5 · Role: architect · Date: 2026-11-09 · Comment: Rev 2. Owner approved naming (effort = thinking
  effort, turn budget = max-turn tier) and first-class provider-neutral autocompact in CLI + dashboard. 6 task folders
  created; co-delivery with E-Sn7tL2 via `../E-Sn7tL2-sentinel-long-running-overseer/HANDOFF.md`.

## Task rollup
| Task | State |
|---|---|
| T-zP28k9-run-options-registry-and-settings-snapshot | Done |
| T-f0d7gm-turn-budget-rename-and-autocompact | Draft |
| T-pMpo4O-schema-driven-launch-forms | Draft |
| T-xNFBww-dashboard-limits-panels | Draft |
| T-mQs7CQ-dashboard-epic-e2e-and-docs | Draft |
| T-9ymKja-effort-means-thinking-flip | Blocked (owner gate: all workspaces migrated + one deprecation release) |

## Evidence
- EPIC §1 code findings.

## Risks / Blockers
- T-zP28k9 refactors central CLI wiring; must be behaviour-identical (registry regression test).

## Next actions
1. Start T-zP28k9 (in parallel with E-Sn7tL2 T-Sn0Spk).
