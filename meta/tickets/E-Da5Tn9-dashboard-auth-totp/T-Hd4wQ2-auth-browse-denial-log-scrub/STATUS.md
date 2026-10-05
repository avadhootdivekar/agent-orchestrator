# STATUS

- ID: `T-Hd4wQ2-auth-browse-denial-log-scrub`
- Updated At: `2026-10-05`
- State: `Draft`
- Owner: `developer` (lane Q)
- Scope: `MVP` · Sprint: `S1` · Estimate: `2 d`

## This update
- By: architect · Role: agent · Date: 2026-10-05 · Comment: Task created in the v2.1 re-baseline
  (HLD §24, §28.9). It takes the file-browser denial (from T-jVqH8w), the `paths.py` denial helpers
  (from T-8NQP8J) and `scrub.py` (from T-CsT5gk), so those three tasks stay within 3 days after
  the gate fixes, and so the generic `denied_paths` mechanism lands in S1 ahead of the
  approval-gates epic (cross-epic row X2, design-review M1). New in v2.1: `~/.config/ao/service.env`
  is denied too (security L7). Design and tickets only; **no code written**.

## Evidence
- None yet.

## Risks / Blockers
- None. The cross-epic merge rule (X2: one deny helper, this epic lands first) is for the manager to
  relay to the approval-gates epic.

## Next actions
1. developer (lane Q): start `scrub.py` once T-kzEzwy lands; do items 1–3 once T-8NQP8J has merged
   `paths.py`. Run the verification and record the results here.
