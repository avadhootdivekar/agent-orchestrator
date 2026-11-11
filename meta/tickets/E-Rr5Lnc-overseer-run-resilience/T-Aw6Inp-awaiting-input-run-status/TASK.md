# TASK: T-Aw6Inp-awaiting-input-run-status

## Metadata
- Task ID: `T-Aw6Inp-awaiting-input-run-status`
- Epic ID: `E-Rr5Lnc-overseer-run-resilience`
- Owner: `unassigned`
- Created: `2026-11-11`
- Last Updated: `2026-11-11`
- Status: `Draft`
- Priority: `P1`
- Estimate: `< 3 days (re-estimate after the re-analysis below)`

## READ FIRST: re-analysis required before implementing
This ticket was filed from a log-level RCA of past failed runs (2026-11-11). The root cause
below is evidence-backed, but the **proposed fix is a starting hypothesis, not a decided
design.** Whoever picks this task up must, before writing code:
1. Re-read the cited evidence (run logs / hook stderr) and re-confirm the root cause against
   the CURRENT code (the overseer tool and engine change often; line numbers here will drift).
2. Look for other runs/workspaces with the same failure signature since this was filed.
3. Write down at least two fix options with trade-offs (safety, determinism, cost, contract
   impact on `overseer-contract.md` / instructions / existing runs on disk) and pick one,
   recorded in this ticket's STATUS.md, before implementing.
4. Add a regression test that reproduces the original failure first (fails before the fix).
5. Check compatibility with in-flight runs: per-run copies of `tools/overseer_tool.py` are
   frozen at scaffold time, so a template fix does not reach existing runs.

## Summary
Planned human-input stops are reported as run failures (and trip breakers). Impact: 8 of 21 stops were deliberate pauses shown as 'failed'.

## Evidence
- HOLD (overseer asked the owner a question), ckpt-prep stderr `HOLD: overseer requested human
  input: see .../needs-input/ck-NN.md; write your answer to control/hold-answer.md then ao resume`:
  o-bi0rvg ck-03, o-q715f4 ck-10, o-yohvjn ck-05 and ck-13 (`ao-runner-ao`).
- git-branch-off deliberately aborted (wrote git-abort.md): o-92c2lh (contradictory branch_policy,
  no repo), finplan o-kpor5a (dirty tracked tree).
- final-push correctly refused (no git repo / no remote) and wrote needs-input + control/pause.flag,
  but the engine marked `Task succeeded but declared outputs missing` -> failed -> `breaker.trip`:
  o-z6n5ka, o-1gk30c (`ao-runner-ao`).

## Root cause (as analysed 2026-11-11)
The engine has one terminal status for both "broken" and "waiting for a human". The HOLD
mechanism is implemented as a failing pre-hook, and agents signal a deliberate refusal by
NOT writing their output, which the engine can only read as failure. The dashboard, status
counts and breakers therefore treat normal human-in-the-loop pauses as errors.

## Proposed fix (hypothesis; evaluate before implementing)
- Add a run/task status such as `awaiting_input` (or `paused`) with a reason and the
  question file path; HOLD, git-abort.md, needs-input/*.md + control/pause.flag map to it.
- Do not trip failure breakers for awaiting_input; show it distinctly on the dashboard
  with the answer-and-resume action.
- Keep the resumability invariant (ao resume continues from the same point).

## Acceptance Criteria
1. A HOLD and a git-abort end the run in `awaiting_input`, not `failed`, with the question path in status.json.
2. No breaker trips on awaiting_input; dashboard shows the question and resume action.
3. Re-analysis notes and the chosen option (with rejected alternatives) are recorded in STATUS.md.
4. `pytest -q`, `ruff check .`, `mypy src` green; template change also reflected in
   `overseer-contract.md.tmpl` / instructions / README where relevant.

## Dependencies
- See the epic's Task List for ordering.

## Artifacts
- Docs/comments: `meta/tickets/E-Rr5Lnc-overseer-run-resilience/T-Aw6Inp-awaiting-input-run-status/`

## Comments
- By: Claude (Opus 5.5, interactive session) · Role: architect · Date: 2026-11-11 · Comment: Filed from the cross-workspace failure RCA in the epic. Fix not evaluated in depth.
