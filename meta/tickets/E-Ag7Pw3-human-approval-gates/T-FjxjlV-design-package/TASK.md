# TASK: T-FjxjlV-design-package

## Metadata
- Task ID: `T-FjxjlV-design-package`
- Epic ID: `E-Ag7Pw3-human-approval-gates`
- Owner: architect
- Created: 2026-10-04
- Last Updated: 2026-10-05
- Status: Draft rev 2 (Gate 1 findings applied; awaiting the re-gate by `reviewer` and `dev-security`)
- Estimate: 3 days (2 days rev 1 + 1 day Gate 1 revision)

## Requirements Mapping
- Requirement IDs: all FR-1..FR-13, NFR-1..NFR-9 (design coverage)
- Design: `docs-md/human-approval-gates-hld.md` (all sections, incl. the §0.5 Gate 1 changelog),
  `docs-md/adr/ADR-0020-human-approval-gates-signed-decisions.md`

## Description
Produce the complete architecture package for human approval gates: requirements and scope, threat
model, HLD/LLD for every module (pseudocode, contracts, schemas, edge cases), ADR, test plan, sprint plan,
touchpoint inventory, and the ticket tree. Record Phase-4 consultations. Revise the package for every
ACCEPT/MODIFY item of the early design Gate 1 and record REJECT items with reasons (rev 2).

## Acceptance Criteria
1. HLD sections 0–26 exist; every brief question Q-A..Q-R appears in the §0.3 table with a decision and a
   section reference; every deviation from the manager's recommended answer states its reason.
2. The threat model (§7) covers every attack path the brief lists (forged file, `ao approve` by an agent,
   signing oracle incl. the anonymous case, key theft as residual risk, `state.json` tamper across
   crash+resume, spec edit before resume, replay, TOCTOU and drift, audit forgery/flooding/truncation,
   `emit_tasks` smuggling and bypass, untrusted rendering, clickjacking/CSRF, DoS, key bootstrap race,
   clock skew), each with mitigation, residual risk and a named test — plus the Gate 1 additions TM-29..TM-34.
3. ADR-0020 records D1–D13 with alternatives and the corrected threat-model summary including RR-1 and
   RR-5.
4. One ticket folder per task with `TASK.md` + `STATUS.md`; every task ≤ 3 days with owner, requirement
   IDs, testable acceptance criteria, dependencies, risks, files (new vs shared, exclusive during its
   stage) and verification commands.
5. Gate 1 (2026-10-05): dev-security FAIL and reviewer PASS-WITH-CHANGES → every finding mapped in HLD §0.5
   (ACCEPT/MODIFY applied, REJECT recorded). **The re-gate** must return no open BLOCKER (reviewer) and no
   open CRITICAL/HIGH (dev-security); any MAJOR finding is either fixed in the package or explicitly
   deferred with a reason recorded in HLD §23.
6. The parent session confirms the decision points of HLD §23.3 (OQ-1, OQ-2 modified, OQ-12, OQ-13, OQ-14,
   OQ-15, CE-1) or the design is updated accordingly.

## Risks
- Reviewers may contest the Q-A deviation or the gate-scoped policy (OQ-1, OQ-2 modified): both are
  isolated design points with documented alternatives.

## Dependencies
- Epic brief (manager), base commit `bb6d8a0`; Gate 1 findings and decisions (manager, 2026-10-05).

## Pseudocode / Algorithm
```text
n/a (design task)
```

## Schemas / Interface Notes
- All interfaces and schemas: HLD §9 and §14.

## Handoff Boundary
- Upstream: manager's epic brief; Gate 1 findings.
- Downstream: manager runs the re-gate, then starts `T-AGO2L6-spec-model-validation`.

## Artifacts
- `docs-md/human-approval-gates-hld.md`, `docs-md/adr/ADR-0020-human-approval-gates-signed-decisions.md`,
  `meta/tickets/E-Ag7Pw3-human-approval-gates/**`
