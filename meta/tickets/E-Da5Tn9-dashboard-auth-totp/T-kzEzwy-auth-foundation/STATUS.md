# STATUS

- ID: `T-kzEzwy-auth-foundation`
- Updated At: `2026-10-05`
- State: `Draft`
- Owner: `developer` (lane B)
- Scope: `MVP` · Sprint: `S1` · Estimate: `1 d`

## This update
- By: architect · Role: agent · Date: 2026-10-05 · Comment: Task created in the v2 re-baseline.
  - **Why it exists:** in v1, `constants.py`, `errors.py` and `seams.py` were spread across S1
    tasks, and the hermetic env fixture landed too late (developer finding D-3, tester finding T-1).
  - **What it holds:** constants, error vocabulary (including `insecure_transport`), seams
    (`CLOCK_BOOTTIME`), the state and event vocabulary, the test helpers, the root hermetic fixture
    (moved here from T-PlEROT), and the import-boundary skeleton (reviewer finding R-1, rule R4).
  - Root `errors.py` gains `EXIT_CONFIG` (reviewer finding R-10).
  - Design and tickets only; **no code written**.

## Evidence
- None yet.

## Risks / Blockers
- None. Must land on S1 day 1: lanes A, B, C and Q depend on it.

## Next actions
1. developer: implement, run the TASK.md verification (including the whole suite with the AC-7
   environment exported), and record the results and coverage here.
