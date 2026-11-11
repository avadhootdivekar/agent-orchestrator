# TASK: T-Mw4Clo-forced-closeout-verify-contradiction

## Metadata
- Task ID: `T-Mw4Clo-forced-closeout-verify-contradiction`
- Epic ID: `E-Rr5Lnc-overseer-run-resilience`
- Owner: `unassigned`
- Created: `2026-11-11`
- Last Updated: `2026-11-11`
- Status: `Draft`
- Priority: `P0`
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
Forced closeout at max_waves contradicts OV-R14 early-closeout evidence rule. Impact: 4 run stops (most frequent single cause).

## Evidence
- `ao-runner-ao` o-6w86nm-sched-governance ck-20 (max_waves=20, 24% budget, stage explore).
- `ao-runner-ao` o-frajcr-offline-rpg-multiplatform... ck-12 (max_waves=12).
- `ao-runner-ao` o-k9r2ta-agro-konkan ck-30 (max_waves=30, 75.6% budget).
- `ao-runner-finplan` o-12ln4x-ux-revamp-3 ck-30 (max_waves=30, 42.8% budget).
- Each: `.orchestrator/runs/<run>/ck-NN/post_hook/stderr.txt` = `OV-R14: early closeout (stage='explore')
  requires a passing verify-kind ledger unit for ask 'AX' recorded after its last
  implement/fix/stabilize/document unit, but none was found`; the checkpoint transcript shows
  `must_close: true` with reason `K>=N (max_waves)`.
- Near miss: o-6w86nm ck-40 (K>=40, stage explore) would have hit it too; additionally the
  sentinel `early-ck-40` graph edit had removed the verify unit `w40-04-all-final-gate` from
  ck-40's depends_on, so the evidence could not exist when ck-40 ran.

## Root cause (as analysed 2026-11-11)
`compute_must_close()` forces `allowed_decisions=["closeout"]` when K >= max_waves (OV-R16
then rejects anything else). Budget staging is independent of waves, so a run that is
wave-limited (not budget-limited) is still in stage `explore`/`converge`.
`_check_r14` applies `_check_early_closeout_evidence` whenever the stage is in
`_EARLY_CLOSEOUT_STAGES = {explore, converge}`, without looking at `must_close`. Result:
the only allowed decision is rejected unless every ask happens to have a verify unit after
its last change. No LLM answer can pass; this is a deterministic contract contradiction,
not an LLM error. The sentinel's early-checkpoint narrowing makes it worse by letting the
final checkpoint start before the final verify unit.

## Proposed fix (hypothesis; evaluate before implementing)
- Exempt forced closeouts (must_close true for max_waves / max_stabilize_passes /
  forced_closeout) from the early-closeout evidence rule; instead record the missing
  per-ask verify evidence as an explicit warning in the closeout report and digest.
- And/or: at K == max_waves - 1 the tool plans the final wave so that it contains a
  verify-kind unit after the last change for every ask lacking evidence.
- Sentinel early-checkpoint narrowing must never drop a verify-kind unit from the deps of
  a checkpoint that is (or will be) must_close.
- Surface "waves left" in the digest so the overseer plans the last wave deliberately.

## Acceptance Criteria
1. A fixture run at K == max_waves in stage explore with an ask lacking verify evidence produces a valid closeout (no OV-R14 failure) and the closeout report lists the missing evidence.
2. Early-checkpoint narrowing never removes a verify-kind unit from a must_close checkpoint's deps (unit test).
3. Non-forced early closeout without evidence is still rejected (no regression of the original OV-R14 intent).
4. Re-analysis notes and the chosen option (with rejected alternatives) are recorded in STATUS.md.
5. `pytest -q`, `ruff check .`, `mypy src` green; template change also reflected in
   `overseer-contract.md.tmpl` / instructions / README where relevant.

## Dependencies
- See the epic's Task List for ordering.

## Artifacts
- Docs/comments: `meta/tickets/E-Rr5Lnc-overseer-run-resilience/T-Mw4Clo-forced-closeout-verify-contradiction/`

## Comments
- By: Claude (Opus 5.5, interactive session) · Role: architect · Date: 2026-11-11 · Comment: Filed from the cross-workspace failure RCA in the epic. Fix not evaluated in depth.
