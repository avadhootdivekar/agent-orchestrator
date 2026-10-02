# STATUS

- ID: `T-f0YWSy-tab-model-and-persistence`
- Updated At: 2026-10-02
- State: Done
- Owner: dev-epic

## This update
- By: dev-epic · Role: manager · Date: 2026-10-02 · Comment: Done.

## Evidence
- ui/src/tabs/model.ts, storage.ts; ui/src/test/tabs-model.test.ts (64 tests: kind allowlist incl. __proto__/constructor, param allowlists, traversal/control/bidi/oversize rejection, hash codec round-trip + 14 hostile hashes, persistence repair, reducer open/navigate/close/move/evict, throwing localStorage). Self-found defect fixed: an invalid optional file path was dropped (turning a hostile link into 'browse root'); now rejects the whole tab.

## Next actions
1. None (Phase 2 shipped).
