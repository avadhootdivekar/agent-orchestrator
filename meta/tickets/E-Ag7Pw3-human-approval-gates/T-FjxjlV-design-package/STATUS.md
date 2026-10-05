# STATUS

- ID: `T-FjxjlV-design-package`
- Updated At: 2026-10-05
- State: Draft rev 2 (Gate 1 findings applied; awaiting the re-gate)
- Owner: architect

## This update
- By: architect · Role: agent · Date: 2026-10-04 · Comment: HLD (sections 0–26), ADR-0020 and the ticket
  tree written. Phase-4 consultations with `developer` (feasibility) and `tester` (testability) completed;
  their findings and the resulting design updates are in HLD §23.4. `reviewer` and `dev-security` are left
  to the manager's independent gates on purpose.
- By: architect · Role: agent · Date: 2026-10-05 · Comment: Gate 1 revision (rev 2). Every finding of
  `GATE1-FINDINGS-AND-DECISIONS.md` is mapped in HLD §0.5: resume integrity rebuilt (S-01 engine-known
  resume, S-02 status normalisation incl. gate ancestors, S-03 fail-closed missing policy with evidence,
  R-03 gate-scoped policy = OQ-2 modified, R-04 clone re-derivation); bounded parser (S-04); no traceback
  locals (S-05); file-browser denial (S-06); W-AG-8 + authoring rule (S-07); `pwd` home + 6-key denylist
  (S-08); per-poll re-hash budget (S-09); `GitRepo.version`/`probe` marked (S-10); `_open_ready_gates`
  pre-pass (R-01); hermetic key dir (R-02); `views.py` (R-05); `T-csusci` split into `T-drPIif` +
  `T-1MgGb4` (R-06); wait stat with the wall-time end marker (R-07); closed refusal codes + one mapping
  table (R-08); all suggestions (S-08 modified as T-15; the `Role: agent` note rejected); CUT 2/3/4/5;
  parallelisation map (HLD §22.3). Claims corrected in the HLD header, §7.3, §7.4 and the ADR threat
  table. New residuals RR-12..RR-15; new attack paths TM-29..TM-34; new concerns NC-1..NC-7 found while
  applying the changes. ADR-0020 now D1–D13.

## Evidence
- Files: `docs-md/human-approval-gates-hld.md` (rev 2), `docs-md/adr/ADR-0020-human-approval-gates-signed-decisions.md`
  (rev 2), `meta/tickets/E-Ag7Pw3-human-approval-gates/` (EPIC, STATUS, 15 task folders; `T-csusci`
  removed, `T-pfJiXw` renamed).
- Design-only; no code or tests changed.
- Rev 1 design-time checks (scratch scripts, nothing committed): full suite on the untouched base 5041
  passed / 8 skipped (2 known bench failures deselected); contract-pinning suites 191 passed; schema
  fragment 24/24; vitest 34 files / 406 tests; pydantic mirror of the validator; real `create_app`
  principal propagation and route-shadowing checks.
- Rev 2 design-time checks (2026-10-05, scratch scripts, the project's venv, worktree `src` first on
  `sys.path`):
  - Rev-2 JSON Schema fragment (null parity, `if` requires an object) patched into the real schema:
    **29/29** designed verdicts; the rewritten HLD §13 example validates.
  - Wrap serializer on subclasses of the real `TaskSpec`/`WorkflowSpec`: `canonical_spec_json` and
    `model_dump_json` of all **10/10** `specs/examples/workflow*.json` byte-identical; gate dumps keep
    `approval`; `"approval": null` loads as a non-gate and is omitted.
  - Parser bombs: plain `json.loads` raises `RecursionError` on a deep bracket bomb (escapes
    `except ValueError`) and `ValueError` on a 5,000-digit integer; the proposed bounded `parse_strict`
    turns both into `RecordMalformed` and accepts brackets inside strings; pydantic's
    `RunState.model_validate_json` turns both into a `ValidationError` (a `ValueError`).
  - Code facts re-verified for the revision: `ui/files.py` has no deny mechanism and every entry point goes
    through `FileBrowser.resolve`; the runs dir is always `<ws>/.orchestrator/runs`; `GitRepo.version`/
    `probe` call the runner with `env=None`; `GitRepo._run` applies `extra_env` after `_FORCED_ENV`;
    `prepare_resume` keeps `not_taken` verbatim and re-derives cone `not_taken` from `route_decisions`;
    `_apply_join` propagates `not_taken` through `join: all`; `_clone_body` is pure in its arguments;
    `write_status`'s `current_task` is first-match over running/pending.

## Risks / Blockers
- Acceptance criteria 5 (re-gate) and 6 (parent confirmations) are pending.

## Next actions
1. Manager: run the re-gate (reviewer + dev-security) on rev 2, starting from HLD §0.5.
2. Parent session: confirm the decision points of HLD §23.3.
3. Architect: address re-gate findings when resumed.
