# STATUS

- ID: `T-mee0lL-component-props-refactor`
- Updated At: 2026-10-02
- State: Done
- Owner: dev-epic

## This update
- By: dev-epic · Role: manager · Date: 2026-10-02 · Comment: Done.

## Evidence
- FileBrowser({initialPath?,initialRoot?}), TaskDetailPanel optional runId (+ open-in-tab button, output paths as links, focus preventScroll), RunGraph passes runId, RunsList/RunDetail use TabLink/OpenInNewTabButton, usePolling gated by TabActiveContext; RunDetail/RunsList public props unchanged; all 269 pre-existing vitest tests still pass unmodified (except the Model/Turns header assertion from Phase 1).

## Next actions
1. None (Phase 2 shipped).
