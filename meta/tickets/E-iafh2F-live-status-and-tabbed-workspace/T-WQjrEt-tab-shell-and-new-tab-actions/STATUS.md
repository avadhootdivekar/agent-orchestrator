# STATUS

- ID: `T-WQjrEt-tab-shell-and-new-tab-actions`
- Updated At: 2026-10-02
- State: Done
- Owner: dev-epic

## This update
- By: dev-epic · Role: manager · Date: 2026-10-02 · Comment: Done.

## Evidence
- ui/src/App.tsx, tabs/TabBar.tsx, TabLink.tsx, context.tsx; ui/src/test/tabs-app.test.tsx (13) + tabs-components.test.tsx (7): persist+hash on load, sidebar open/focus, plain/ctrl/middle/explicit new tab on run rows, task rows -> task tab, output path -> file viewer, close (x/middle/Delete), reorder (Alt+Arrow + drag-drop), corrupt/throwing localStorage, hash restore/hashchange/hostile hash, inactive tabs mounted but not polling. Real Chrome: reload restores tabs+active, fresh browser context opening a hash URL restores that tab, hostile hash ignored, 0 /api/runs requests in 7.5s while inactive.

## Next actions
1. None (Phase 2 shipped).
