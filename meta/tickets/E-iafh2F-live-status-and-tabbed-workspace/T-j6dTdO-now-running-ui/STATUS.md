# STATUS

- ID: `T-j6dTdO-now-running-ui`
- Updated At: 2026-10-02
- State: Done
- Owner: dev-epic

## This update
- By: dev-epic · Role: manager · Date: 2026-10-02 · Comment: Done.

## Evidence
- ui/src/components/NowRunning.tsx, usePolling.ts, RunDetail/RunsList wiring, styles.css; ui/src/test/now-running.test.tsx (cap at 0/1/3/5/30 rows, inline fixed height, internal scroll, no toggle controls, empty state, hidden-pause polling, RunDetail + RunsList integration); 269 vitest pass. Also fixed a pre-existing unclosed `.task-detail-outputs {` rule in styles.css that swallowed the run-prompt CSS.

## Next actions
1. None (Phase 1 shipped).
