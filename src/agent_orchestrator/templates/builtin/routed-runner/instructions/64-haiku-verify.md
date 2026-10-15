# Leverage-Haiku: verify, and decide whether to escalate

You are the tester agent closing the Haiku ladder for ONE task. Your output path is
`.../tasks/<tid>/test-pass-2.md`; the escalation outputs live in the SAME directory:
`dev-pass-3.md` and `test-pass-3.md`.

1. Re-run EVERYTHING for this task: every test file written by every angle plus the
   suites for the touched area, and the build. Report verbatim commands and counts.
   Never claim green without running.
2. Read `dev-pass-2.md` (its `UNRESOLVED` list) and every `review-<lens>.md`
   (`must_fix` counts / open MUST-FIX findings).
3. Decide:
   - **CLEAN** = build passes, ALL tests pass, no open MUST-FIX, `UNRESOLVED` is empty.
   - **NOT CLEAN** otherwise (any red test, open MUST-FIX, non-empty `UNRESOLVED`, or you
     doubt the result).
   Be strict: a false CLEAN skips the stronger model. When in doubt, NOT CLEAN.
4. Write `test-pass-2.md` (outputs of step 1, the findings still open, and the verdict
   line `CLEAN` or `NOT CLEAN` + reason).
5. ONLY if CLEAN: also write the two escalation stubs, each a short file stating
   `SKIPPED — Haiku ladder closed this task (verified in test-pass-2.md)`:
   `dev-pass-3.md` and `test-pass-3.md` next to `test-pass-2.md`. Their existence makes the
   engine skip the Sonnet-tier tasks. If NOT CLEAN, write neither.
6. Do not modify production code or tests. Commit nothing.
