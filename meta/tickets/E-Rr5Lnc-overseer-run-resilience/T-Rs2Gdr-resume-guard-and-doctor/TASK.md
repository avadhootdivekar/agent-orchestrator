# TASK: T-Rs2Gdr-resume-guard-and-doctor

## Metadata
- Task ID: `T-Rs2Gdr-resume-guard-and-doctor`
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
`ao resume` re-runs into an unchanged failure; no diagnosis aid. Impact: 2 wasted resumes (o-95avzh); manual RCA needed for every stop.

## Evidence
- o-95avzh resumed twice into the identical OV-CFG-3 error within minutes.
- Each RCA in this epic required reading hook stderr files by hand across run dirs.

## Root cause (as analysed 2026-11-11)
Resume does not know why the run stopped or whether anything changed; the failure class and
the fix are not summarised anywhere a user looks first.

## Proposed fix (hypothesis; evaluate before implementing)
- Record a structured `stop_reason` (class: tool-bug / config / llm-output / awaiting-input /
  infra / limit; message; hook stderr path; suggested fix) in status.json at run end.
- `ao doctor <run>` (and a dashboard panel) prints it with the suggested action.
- `ao resume` refuses (overridable) when the stop was deterministic (config/tool) and the
  relevant inputs (config, tool copy) are unchanged.

## Acceptance Criteria
1. status.json carries stop_reason for each failure class above (tests per class).
2. Resuming an unchanged CFG-3 failure is refused with the reason.
3. Re-analysis notes and the chosen option (with rejected alternatives) are recorded in STATUS.md.
4. `pytest -q`, `ruff check .`, `mypy src` green; template change also reflected in
   `overseer-contract.md.tmpl` / instructions / README where relevant.

## Dependencies
- See the epic's Task List for ordering.

## Artifacts
- Docs/comments: `meta/tickets/E-Rr5Lnc-overseer-run-resilience/T-Rs2Gdr-resume-guard-and-doctor/`

## Comments
- By: Claude (Opus 5.5, interactive session) · Role: architect · Date: 2026-11-11 · Comment: Filed from the cross-workspace failure RCA in the epic. Fix not evaluated in depth.
