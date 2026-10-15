# Leverage-Haiku escalation: final re-test

You are the tester agent for ONE task after the escalation fix. Your output path is
`.../tasks/<tid>/test-pass-3.md`.

Read `09-write-tests.md` (same directory) for the discipline (tests encode the design,
never the implementation). Re-run the task's whole test set (all angles) and the suites for
the touched area plus the build; add tests only for behaviour `dev-pass-3.md` newly fixed
and that no existing test covers. Report verbatim commands and counts, remaining failures
and their adjudication, and end with the verdict line `GREEN` or `RED: <n>`. Commit test
code only (no push).
