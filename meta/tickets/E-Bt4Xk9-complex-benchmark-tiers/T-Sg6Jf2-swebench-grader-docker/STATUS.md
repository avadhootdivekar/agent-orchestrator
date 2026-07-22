# STATUS

- ID: `T-Sg6Jf2-swebench-grader-docker`
- Updated At: 2026-07-22
- State: Draft (designed; not started)
- Owner: TBD (assigned at sprint start)

## This update
- SweBenchGrader: official Docker eval on extracted git-diff patch; resolved=solved; disk cleanup + Docker lock.

## Evidence
- Design + acceptance criteria specified in this folder's TASK.md; part of epic E-Bt4Xk9.

## Risks / Blockers
- Dependencies: T-Sw5Hd9, T-Wp4Nz5 (spec/registries sequencing); T-Pl3Rx7 (orthogonal lock)

## Next actions
1. Extract patch -> predictions -> run_evaluation (locked) -> parse resolved -> cleanup images.
