# TASK: T-fXWbqg-cache-review-gates

## Metadata
- Task ID: `T-fXWbqg-cache-review-gates`
- Epic ID: `E-Rc4Hk8-cross-run-result-cache`
- Owner: `reviewer` + `dev-security`
- Created: `2026-10-04`
- Last Updated: `2026-10-05` (Rev 2)
- Status: `Draft`
- Estimate: `24 focus hours (3 days)`: G1a 8 h (end of Sprint 1) / G1b 8 h (end of Sprint 2) /
  G2 8 h (Sprint 3)

## Requirements Mapping
- Requirement IDs: NFR-2, NFR-8, NFR-10 (M-1…M-16), NFR-11
- HLD: §7.7 (threat model), §22.4 (gate definitions), §24.2 (merge notes)
- ADR-0019: all D-decisions (conformance)

## Description
Run three evidence-based review gates. Each gate writes a report under
`output/E-Rc4Hk8-cross-run-result-cache/` and records its verdict in this task's `STATUS.md` and
in the epic `STATUS.md`.

| Gate | When | Scope | Report |
|------|------|-------|--------|
| **G1a** | end of Sprint 1 | T-FJH6LI (`safeio`, parse boundary, AST guard), T-OeRYSO, T-8tr1H4, T-uoYW6b, T-U7ckfd | `review-g1a.md` |
| **G1b** | end of Sprint 2 | T-u3jG8F, T-HjxNQ0 (destructive maintenance), T-28J9oR + T-QgQy08 (kill switch, eligibility), T-gDNjN2, T-XpF1pF, T-o95l1M | `review-g1b.md` |
| **G2** | Sprint 3, after T-JCOAsq, T-6tRKml and T-bLpoze | everything; dev-security reviews the delta | `review-g2.md` |

Findings use the MUST-FIX / SHOULD-FIX / NIT scale. Each finding cites file:line, and each
MUST-FIX is re-verified after the fix.

## File scope (exclusive)
- `output/E-Rc4Hk8-cross-run-result-cache/review-g1a.md`, `review-g1b.md`, `review-g2.md`
- This task's ticket files. Verdict lines also go in the epic `STATUS.md`.

## Inputs / Outputs
- **Inputs:** the code at each gate; HLD §7.7 and §22.4; T-JCOAsq evidence.
- **Outputs:** gate verdicts (PASS / FAIL with MUST-FIX list).

## Acceptance Criteria
1. **G1a PASS.**
   - 0 open MUST-FIX.
   - M-2, M-4, M-6, M-7, M-9, M-13 and M-14 verified with file:line evidence.
   - The hostile corpus and the AST guard are green.
   - GV-1 Rev 2 is reproduced.
   - U-A* (argv identity) is green.
2. **G1b PASS.**
   - 0 open MUST-FIX.
   - M-1, M-3, M-5, M-8, M-10, M-12 and M-16 verified with evidence.
   - I-1 and I-2 green.
   - The engine diff is at most 80 formatted lines, with at most 8 inside existing functions.
   - `clear` and `prune` cannot delete foreign versions (U-SM5).
3. **G2 PASS.**
   - 0 open MUST-FIX.
   - The full-suite numbers are pasted (baseline plus new tests, no new failures).
   - Coverage is at least 85%.
   - The merge notes (HLD §24.2) match the actual diff, file by file.
   - The **approval-ordering check**: when E-Ag7Pw3 code is present on the merge branch, its gate
     runs before seam (c). Otherwise the seam comment and the merge checklist are in place.
   - The CLI argument handling (`rm --run`, prefixes) and dashboard text-only rendering are
     verified.
4. **Traceability.** Every gate verdict is mirrored in this task's `STATUS.md` and in the epic
   `STATUS.md` on the same day.

## Test requirements
- The reviewers run the suites named in each gate; there are no new test files.

## Risks
- **Fix-up loops overrun the sprint.** Mitigation: Sprint 3 slack (HLD §22.1); gates G1a and G1b
  happen early.

## Dependencies
- G1a: the Sprint 1 build tasks.
- G1b: the Sprint 2 build tasks.
- G2: T-JCOAsq Part 3, T-6tRKml and T-bLpoze.

## Pseudocode / Algorithm
```text
for gate in (G1a, G1b, G2):
    collect diff + test evidence -> check threat-model items in scope -> list findings (MUST/SHOULD/NIT, file:line)
    owners fix MUST-FIX -> re-verify -> verdict PASS when 0 MUST-FIX -> write report + sync STATUS files
```

## Schemas / Interface Notes
- **Report format:** a markdown table of findings (id, severity, file:line, issue,
  fix/verification) and the verdict.

## Handoff Boundary
- **Upstream:** the implementation tasks and T-JCOAsq.
- **Downstream:** T-bdQZW4 (starts after G2 PASS); the parent's merge.

## Artifacts
- **Docs/comments:** `meta/tickets/E-Rc4Hk8-cross-run-result-cache/T-fXWbqg-cache-review-gates/`
- **Large outputs:** `output/E-Rc4Hk8-cross-run-result-cache/review-*.md`

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: G1 and G2 gates.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2 changes:
  - the single G1 is split into G1a (end of Sprint 1) and G1b (end of Sprint 2), with evidence
    requirements (security S9); re-estimated from 16 h to 24 h;
  - the approval-ordering check is added to G2. It replaces the declined `gates_cleared` API
    (security S6).
