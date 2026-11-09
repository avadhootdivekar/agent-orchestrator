# STATUS

- ID: `T-Sn6Brr-soft-barrier-and-pending-edits`
- Updated At: `2026-11-09`
- State: `Done` (AC5 closed in W10)
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

- By: claude-sonnet-5-5 · Role: developer · Date: 2026-11-09 · Comment: AC5 folded into T-Sn9Pow: sentinel-origin node badge/border + sentinel spawn-edge style + legend row done (UI tests green). Styling of *edited* dependency edges still open (graph API does not expose `graph_edits`).

- By: claude-sonnet-5-5 · Role: developer · Date: 2026-11-09 · Comment: **AC5 closed (W10 w10-01).** Graph API now returns `graph_edits`
  (`ui/graph.py::GraphEditView`, built via `graph_check.edit_view`; field `dependencies` = effective depends_on after `set_edges`;
  `graph_version` only changes when edits exist, so edit-free runs keep their fingerprint). UI: dependency edges into a `set_edges`
  target get class `graph-edge-edited` (thicker stroke) + Legend row (`RunGraph.tsx::editedTargetIds/isEditedEdge`); sentinel node badge
  / dashed spawn edge were already done. Tests: `tests/test_ui_graph.py::TestGraphEditsExposed`, `ui/src/test/graph-sentinel-styling.test.tsx`,
  fixture `run-graph.json`. `graph_edits` is optional in the TS type (older server tolerated).
  **CheckPolicy**: the policy is wired for backlog releases by T-Sn7Bkl (`sentinel_backlog.policy_for`); added engine-level tests
  `tests/test_sentinel_backlog.py::test_backlog_checkpolicy_enforced_through_engine` (closeout/stabilize/converge -> GE-STAGE-*). Not wired
  (by design, documented): soft-barrier emitter manifests and ctl/LLM `inject` pass no stage policy — the engine has no overseer stage
  context for them, the shared FR-19 cap still applies to every sentinel inject via `with_cap`.
