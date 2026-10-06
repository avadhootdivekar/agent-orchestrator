# STATUS

- ID: `T-AGO2L6-spec-model-validation`
- Updated At: 2026-10-05
- State: Draft — Not started
- Owner: developer

## This update
- By: architect · Role: agent · Date: 2026-10-04 · Comment: Ticket created from HLD §9.1/§9.2. Starts
  after the design gates pass.
- By: architect · Role: agent · Date: 2026-10-05 · Comment: Gate 1 revision: rev-2 models (gate-scoped
  `ApprovalPolicyRecord`/`GatePolicy`, `WaitSpan`/`closed_waits`, closed refusal enums + mapping tables,
  all new constants), `"approval": null` parity (suggestion S-01), wrap serializer + gate-free golden
  (S-02), AG-5 (S-11), W-AG-9 (S-07), W-AG-10 (S-09), sole ownership of `test_import_layering.py`
  (parallelisation map). Estimate 2.5 d → 3 d.
- By: architect · Role: agent · Date: 2026-10-05 · Comment: Gate 2 revision (rev 3, frozen):
  `approvals/models.py` gains `GatedMarker`, the signed `previous_sha256` on `ApprovalPolicyRecord` and the
  `GATED_MARKER_*` constants (S-11); `MAX_REHASH_BYTES_PER_POLL` and `MAX_UNREVIEWED_ITEMS` dropped (R-10);
  errors gain `GatedMarkerInvalid`/`GatedMarkerUnwritable`; one W-AG-7 call site for `T-Mdk27e` (W-AG-8
  withdrawn); `conftest.py` holds only the autouse hermeticity fixtures and `test_hermeticity.py` is added
  (R-09); W-AG-10 uses the `integration.*` field paths (R-12). Estimate unchanged (3 d).

## Evidence
- None yet. Design-time checks behind this ticket (HLD §9.1.2/§9.1.3): the rev-2 schema fragment gives
  29/29 designed verdicts; the wrap serializer keeps all 10 example specs byte-identical.

## Risks / Blockers
- Not blocked by the design gates any more: rev 3 is frozen (Gate 2). Starts when the manager opens Sprint 1;
  the parent-session decision points (HLD §23.3) stay recorded.

## Next actions
1. Deliver `approvals/models.py` + `approvals/errors.py` first (unblocks `T-drPIif` and `T-1MgGb4`).
2. Record the gate-free spec goldens before touching `models.py`.
3. Then the `TaskSpec` validator/serializer, schema, `cross_validate`, manifest containment, layering test.
