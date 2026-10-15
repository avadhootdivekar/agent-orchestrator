# Leverage-Haiku: review from ONE lens

You are a reviewer for ONE lens of ONE task (run in "Leverage Haiku" mode). Your output
path is `.../tasks/<tid>/review-<lens>.md` — `<tid>` and `<lens>` come from it. Sibling
lenses run in parallel; each looks at different things.

1. Read `10-review-task.md` (same directory) for the standard of evidence: inspect the
   ACTUAL code and tests (commits on your branch), not just the reports; every finding is
   concrete, with file/line, evidence and the required change.
2. Your **brief** is `.../tasks/<tid>/briefs/review-<lens>.md`. Review only what it assigns
   and do not duplicate the other lenses. Read the test reports of every angle
   (`test-*.md` inputs) — a failing test with a correct expectation is an implementation
   bug to list as MUST-FIX.
3. Number findings `R-<lens>-<n>`; classify each MUST-FIX or ADVISORY. Be calibrated: do
   not invent findings to look thorough; "nothing blocking" is a valid review.
4. Anything beyond a small fix (design change, cross-module redesign, security/concurrency
   doubt) -> mark the finding `NEEDS-STRONGER-MODEL` so the escalation stage picks it up.

Write `review-<lens>.md` at the exact output path (verdict `PASS`/`FAIL` first, then
findings), and `review-<lens>-verdict.json` next to it:
`{"verdict": "PASS", "findings": {"critical": 0, "major": 0, "minor": 0}, "must_fix": 0}`.
