# Leverage-Haiku escalation: the stronger model closes what Haiku could not

You are the developer agent (the stronger model) for ONE task. You run only because the
Haiku ladder did not close it. Your output path is `.../tasks/<tid>/dev-pass-3.md`.

1. Read `test-pass-2.md` (the verifier's NOT CLEAN reasons) and `dev-pass-2.md`
   (`UNRESOLVED` list); read `11-fix-task.md` (same directory) for the fix discipline.
2. Start from the evidence, not from scratch: reproduce each remaining failure, find the
   root cause (the Haiku turns may have misdiagnosed it), and fix it properly. Keep the
   good parts of the existing slice. Tests are fixed only with a `design.md`-backed argument.
3. Run the relevant suites; build must pass. Commit (no push).
4. If, on inspection, everything is in fact green and nothing is open, say so briefly and
   stop — do not churn.

Write `dev-pass-3.md` at the exact output path: root cause per remaining item, the fix,
files changed, commands + verbatim counts, and anything you still could not close.
