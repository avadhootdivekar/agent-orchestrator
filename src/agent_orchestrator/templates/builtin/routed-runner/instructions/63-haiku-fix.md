# Leverage-Haiku, turn 2: fix everything found

You are the developer agent running the SECOND (and last) Haiku turn on ONE task. Your
output path is `.../tasks/<tid>/dev-pass-2.md`.

1. Read `11-fix-task.md` (same directory) and follow it in full. Here the findings come
   from SEVERAL independent sources, not one review: every `review-<lens>.md` AND every
   `test-<angle>.md` input. Consolidate them first (dedupe overlaps) into one numbered list.
2. Resolve every MUST-FIX and every failing test with a correct expectation — make the
   tests pass as written; never edit a test to turn it green (a wrong test needs a
   design.md-backed rejection). Where reviewers disagree, decide from `design.md`.
3. Items marked `NEEDS-STRONGER-MODEL` / `ESCALATE`, or that you cannot close after an
   honest attempt: do NOT thrash. Leave them, list them in a section **`UNRESOLVED`** with
   the exact failing test/finding and what you tried — the next stage (stronger model)
   starts from that list.
4. Run the full relevant suites; build must pass. Commit (no push).

Write `dev-pass-2.md` at the exact output path: consolidated findings table
`Source | Finding | Resolution (Fixed/Rejected/Deferred/UNRESOLVED) | Evidence`, files
changed, commands + verbatim counts.
