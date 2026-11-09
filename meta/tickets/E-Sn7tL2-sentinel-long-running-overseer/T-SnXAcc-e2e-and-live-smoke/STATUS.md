# STATUS

- ID: `T-SnXAcc-e2e-and-live-smoke`
- Updated At: `2026-11-09`
- State: `In Progress` (AC1 + AC3 met; AC2 live smoke blocked on owner budget — see HANDOFF.md)
- Owner: `unassigned`

## This update
- By: claude-opus-5-5 · Role: architect · Date: 2026-11-09 · Comment: Task created from epic breakdown (owner-approved design).

## Evidence
- By: claude-sonnet-5-5 · Role: tester · Date: 2026-11-09 · Comment: non-live e2e matrix done — `tests/test_sentinel_e2e_matrix.py` (17 pass), see `MATRIX.md`. Live smoke (FR-23) pending owner budget confirmation; AC2/AC3 open.
- Findings: mypy gate red (pre-existing `_version.py`/`overseer_tool.py`, `mypy .` duplicate conftest; `ui/graph.py:263` from another unit's uncommitted edit).

## Risks / Blockers
- None

## Next actions
1. **Owner** confirms the smoke budget (proposal $15 run cap, sonnet/haiku) — steps in `HANDOFF.md`; then run FR-23 smoke, write `smoke/README.md`.
2. ~~Reconcile HLD 'Deviations'~~ done (W11). Flip mechanical-sentinel default only after evidence (T-SnXDoc).

- By: claude-sonnet-5-5 · Role: developer · Date: 2026-11-09 · Comment: **W11 (w11-02): AC3 met** — HLD §21 Deviations re-checked against the code (edited-edge styling now done, stage-policy scope, `GE-EDGE-INTO-NONPENDING` reachability, ctl help strings, open §11 boxes). **AC2 still open**: live smoke NOT run; owner handoff with exact steps, proposed $15 cap and evidence list written to `HANDOFF.md`. Gate re-run (see unit report w11-02): `mypy src` is clean now (the `ui/graph.py:263` and `_version.py`/`overseer_tool.py` errors from the Findings line are gone); `mypy .` is red by construction (1097 errors in tests/ and `output/` scratch scripts — never a green gate here).
