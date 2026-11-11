# TASK: T-Mo5Nud-missing-output-nudge

## Metadata
- Task ID: `T-Mo5Nud-missing-output-nudge`
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
Task fails on missing declared output without asking the agent once. Impact: 1 stop (plus the 4 deliberate cases in T-Aw6Inp).

## Evidence
- `ao-runner-ao` o-dw6zoz-objective w05-03-plan-ambitious-fix: `Task succeeded but declared
  outputs missing: [.../progress/w05-03-plan-ambitious-fix.json]` although the agent's final
  message says the breadcrumb was written.

## Root cause (as analysed 2026-11-11)
The agent believed it wrote the file (wrong path or never written); the engine fails the task
immediately. A one-turn follow-up in the same session would usually fix it for cents.

## Proposed fix (hypothesis; evaluate before implementing)
- On missing declared outputs: resume the session once with "these declared outputs are
  missing: <paths>; write them or write needs-input explaining why", then re-check.
- Distinguish a deliberate refusal (needs-input / abort file present) -> T-Aw6Inp status.

## Acceptance Criteria
1. Fake-tier test: an agent that forgets its output is nudged once and the task succeeds.
2. Re-analysis notes and the chosen option (with rejected alternatives) are recorded in STATUS.md.
3. `pytest -q`, `ruff check .`, `mypy src` green; template change also reflected in
   `overseer-contract.md.tmpl` / instructions / README where relevant.

## Dependencies
- See the epic's Task List for ordering.

## Artifacts
- Docs/comments: `meta/tickets/E-Rr5Lnc-overseer-run-resilience/T-Mo5Nud-missing-output-nudge/`

## Comments
- By: Claude (Opus 5.5, interactive session) · Role: architect · Date: 2026-11-11 · Comment: Filed from the cross-workspace failure RCA in the epic. Fix not evaluated in depth.
