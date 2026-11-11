# STATUS

- ID: `T-Dw5Cal-deepswe-calibration-and-comparison`
- Updated At: 2026-11-11
- State: **In Progress** (Phase 0: ao arm end-to-end pending)
- Owner: Claude (manager) for avadhoot

## This update
- By: avadhoot · Role: user · Date: 2026-11-11 · Comment: first pass on DeepSWE to get a filtered solvable
  baseline (regression tests) and an unsolved set (grading); verify the complete comparison works, then move
  to SWE-Marathon. DeepSWE verification budget **$100**; check the first run after the current ao-arm Harbor
  pilot finishes.
- By: Claude · Role: manager · Date: 2026-11-11 · Comment: task opened with the roadmap in TASK.md. TB 2.1
  dropped as a primary suite (near saturated for Sonnet 5.5 / Opus 5.5).

## Spend ledger (list-price equivalent, subscription token)
| Job | Arm | Tasks | Cap/task | Spent | Note |
|---|---|---|---|---|---|
| pilot1-tb21-dna-cc-sonnet | plain Sonnet 5.5 | 1 | $10 | $0.25 | reward 1.0 (T-Hb3Adp pilot) |
| pilot1/pilot2 ao | ao + Sonnet 5.5 | 1 | $10 | $0.00 | harness errors before any LLM call (fixed) |

## Evidence
- Oracle: `/var/tmp/ao-harbor/jobs/oracle-pilot-tb21`, `oracle-pilot-deepswe` (5/5 PASS).

## Risks / Blockers
- None blocking; see TASK.md Risks.

## Next actions
1. Finish Phase 0 (ao arm end-to-end on `dna-assembly`, parity, commit adapter changes).
2. Phase 1a/1b on the 2 oracle-validated DeepSWE tasks; size 1c from the measured cost.
