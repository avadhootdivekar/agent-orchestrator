# STATUS

- ID: `T-Sn6Brr-soft-barrier-and-pending-edits`
- Updated At: `2026-11-09`
- State: `Done` (AC5 UI styling deferred)
- Owner: `developer (claude-sonnet-5-5)`

## This update
- By: claude-opus-5-5 · Role: architect · Date: 2026-11-09 · Comment: Task created from epic breakdown (owner-approved design).

- By: claude-sonnet-5-5 · Role: developer · Date: 2026-11-09 · Comment: **Implemented** per spike GO-WITH-CHANGES (I1–I6): soft barrier
  (`TaskSpec.barrier`), shared checker `graph_check.py` (`GE-*` rule ids), `inject`/`edit_pending` inbox kinds via
  `Orchestrator._make_edit_applier`, persisted `RunState.graph_edits` replayed in `prepare_resume`, FILL priority,
  `SPAWN_ORIGIN_SENTINEL`. As-built: `docs-md/sentinel-hld.md` §6.8. AC1–4 met by `tests/test_soft_barrier.py` +
  `tests/test_graph_check.py`; AC5 (graph UI styling of sentinel nodes/edited edges) NOT done — UI is another unit's scope;
  the graph API already passes `origin="sentinel"` through. FR-19 cap/breaker is T-Sn7Bkl.

## Evidence
- Tests: `tests/test_graph_check.py` (26: FR-11 stage matrix, every FR-12 rule, commit/replay), `tests/test_soft_barrier.py` (9: soft
  emitter overlaps 3 in-flight tasks with no double dispatch, manifest edge into running task rejected, priority/add-edge change
  dispatch order, bad edit quarantined `GE-NOT-PENDING`, inject => sentinel origin, LLM actor `GE-DISABLED`, resume keeps edits,
  consumed-id replay not applied twice).

## Risks / Blockers
- None

## Next actions
1. Pick up after: T-Sn3Tck and a GO from T-Sn0Spk (else implement §4.4.4 fallback)
