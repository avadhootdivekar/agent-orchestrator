# TASK: T-Dk4Hyg-disk-log-and-supervisor-hygiene

## Metadata
- Task ID: `T-Dk4Hyg-disk-log-and-supervisor-hygiene`
- Epic ID: `E-Rr5Lnc-overseer-run-resilience`
- Owner: `unassigned`
- Created: `2026-11-11`
- Last Updated: `2026-11-11`
- Status: `Draft`
- Priority: `P2`
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
Disk-full and supervisor-kill stops; run.log noise. Impact: 2 stops (ENOSPC; sentinel systemic kill alarm).

## Evidence
- `ao-runner-ai-models` e-k7zlr7 epic-push: every Bash call failed with `ENOSPC`.
- `ao-runner-finplan` o-k6shov: `sentinel.error: OSError(28, 'No space left on device'); sentinel
  lane disabled`, and earlier `sentinel.alarm: systemic -- 3 kills in 3600s` with a task
  killed (exit 143) -> run failed. The stuck-kill false positives are T-Rk4Wn7 (fixed on
  branch ad/enhancements-10nov by run o-6w86nm, NOT yet installed in the live ao).
- o-6w86nm run.log is 4.3 MB, dominated by `Inferred edge intake -> ... not declared in
  depends_on` warnings re-logged for every task on every dispatch.

## Root cause (as analysed 2026-11-11)
No free-space guard before or during runs; supervisor kill fixes not deployed; DAG
inference warnings are O(tasks) per dispatch.

## Proposed fix (hypothesis; evaluate before implementing)
- Free-space preflight and periodic check (pause run as awaiting_input below a threshold);
  `ao prune` policy for old run artifacts.
- Install the T-Rk4Wn7 fixes into the live ao once the branch merges (human step) and
  verify on a finplan run.
- Log each inferred edge once per run (or at debug level).

## Acceptance Criteria
1. Run pauses (not fails) below the free-space threshold.
2. Inferred-edge warning logged once per edge per run.
3. Re-analysis notes and the chosen option (with rejected alternatives) are recorded in STATUS.md.
4. `pytest -q`, `ruff check .`, `mypy src` green; template change also reflected in
   `overseer-contract.md.tmpl` / instructions / README where relevant.

## Dependencies
- See the epic's Task List for ordering.

## Artifacts
- Docs/comments: `meta/tickets/E-Rr5Lnc-overseer-run-resilience/T-Dk4Hyg-disk-log-and-supervisor-hygiene/`

## Comments
- By: Claude (Opus 5.5, interactive session) · Role: architect · Date: 2026-11-11 · Comment: Filed from the cross-workspace failure RCA in the epic. Fix not evaluated in depth.
