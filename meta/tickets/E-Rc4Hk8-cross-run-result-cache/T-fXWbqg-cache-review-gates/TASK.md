# TASK: T-fXWbqg-cache-review-gates

## Metadata
- Task ID: `T-fXWbqg-cache-review-gates`
- Epic ID: `E-Rc4Hk8-cross-run-result-cache`
- Owner: `reviewer` + `dev-security`
- Created: `2026-10-04`
- Last Updated: `2026-10-05` (Rev 3)
- Status: `Done`
- Estimate: `24 focus hours (3 days)`: G1a 8 h / G1b 8 h / G2 8 h

## Requirements Mapping
- Requirement IDs: NFR-2, NFR-8, NFR-9, NFR-10 (M-1…M-16), NFR-11
- HLD: §7.7 (threat model), §22.4 (gate definitions), §24.2 (merge notes)
- ADR-0019: all D-decisions (conformance)

## Description
Run three independent, evidence-based review passes. Each gate writes a report under
`output/E-Rc4Hk8-cross-run-result-cache/` and records its verdict in this task's `STATUS.md` and
in the epic `STATUS.md`.

| Gate | When | Scope | Report |
|------|------|-------|--------|
| **G1a** | after the core set | T-FJH6LI, T-28J9oR, T-OeRYSO, T-QgQy08, T-8tr1H4, T-uoYW6b, T-U7ckfd, T-u3jG8F, T-HjxNQ0 (key, store and restore core) | `review-g1a.md` |
| **G1b** | after the engine set | T-gDNjN2, T-XpF1pF, T-eyn5UG, T-o95l1M, T-ZTxN1x, T-JCOAsq Part 1 (coordinator, engine, CLI wiring, reporting) | `review-g1b.md` |
| **G2** | after T-JCOAsq Part 3 | everything, including T-nPMuz4, T-6tRKml and T-bLpoze; dev-security reviews the delta | `review-g2.md` |

Findings use MUST-FIX / SHOULD-FIX / NIT, each with file:line; each MUST-FIX is re-verified after
its fix.

## File scope (exclusive)
- `output/E-Rc4Hk8-cross-run-result-cache/review-g1a.md`, `review-g1b.md`, `review-g2.md`
- This task's ticket files; verdict lines in the epic `STATUS.md`.

## Inputs / Outputs
- **Inputs:** the code at each gate; HLD §7.7, §22.4; T-JCOAsq evidence.
- **Outputs:** gate verdicts (PASS, or FAIL with the MUST-FIX list).

## Acceptance Criteria
1. **G1a PASS.** 0 open MUST-FIX. M-1…M-9, M-13 and M-14 verified with file:line evidence. The
   hostile corpus, the AST guard, U-ST15 (unsafe paths never followed), U-G7 (repository
   detection stops at the workspace root), U-E27 (`unknown_agent_field`), U-S4 (policy flip
   point) and U-A* are green; GV-1 is reproduced; `verify` deletes nothing.
2. **G1b PASS.** 0 open MUST-FIX. M-10, M-11, M-12 and M-16 verified. I-1, I-2 (serial and
   `max_parallel=3`), U-LZ1, U-AST-E, U-CO18 (lazy guard 3) and U-CO19 (no evict on unsafe paths)
   green. The engine diff is net ≤ +110 formatted lines with ≤ 12 added lines inside existing
   functions, every line ≤ 100 columns, and the `_reverse_stale_charge` extraction is
   behaviour-identical (existing budget tests unedited and green).
3. **G2 PASS.** 0 open MUST-FIX. Full-suite numbers pasted; the CI coverage step passes (package
   ≥ 85%, each core module ≥ 90%); the merge notes (HLD §24.2) match the diff file by file; the
   approval-ordering check (when E-Ag7Pw3 code is present, its gate runs before seam (c);
   otherwise the seam comment and the checklist are in place); CLI prefix handling; dashboard
   text-only rendering; the UI bundle is a separate commit; the G0 protocol doc exists and does
   not claim G0 was executed.
4. **Traceability.** Every verdict is mirrored in this task's `STATUS.md` and the epic `STATUS.md`
   on the same day.

## Test requirements
- The reviewers run the suites named in each gate; there are no new test files.

## Risks
- **Fix-up loops overrun the plan.** Mitigation: S3 slack (HLD §22.1); G1a and G1b happen early.

## Dependencies
- G1a: the core set. G1b: the engine set and T-JCOAsq Part 1. G2: T-JCOAsq Part 3 (which follows
  every implementation task).

## Pseudocode / Algorithm
```text
for gate in (G1a, G1b, G2):
    collect diff + test evidence -> check the threat-model items in scope -> list findings
    owners fix MUST-FIX -> re-verify -> PASS at 0 MUST-FIX -> write report + sync STATUS files
```

## Schemas / Interface Notes
- **Report format:** a markdown table (id, severity, file:line, issue, fix/verification) plus
  the verdict.

## Handoff Boundary
- **Upstream:** the implementation tasks and T-JCOAsq.
- **Downstream:** T-bdQZW4 (after G2 PASS); the parent's merge.

## Artifacts
- **Docs/comments:** `meta/tickets/E-Rc4Hk8-cross-run-result-cache/T-fXWbqg-cache-review-gates/`
- **Large outputs:** `output/E-Rc4Hk8-cross-run-result-cache/review-*.md`

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: G1 and G2 gates.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2: G1 split into G1a/G1b with
  evidence rules; approval-ordering check in G2.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 3 (manager E): G1a covers
  the key, store and restore core; G1b covers coordinator, engine, CLI wiring and reporting; exit
  criteria name the Rev 3 tests (U-ST15, U-G7, U-E27, U-S4, U-CO18, U-CO19, U-LZ1) and the honest
  engine budget.
