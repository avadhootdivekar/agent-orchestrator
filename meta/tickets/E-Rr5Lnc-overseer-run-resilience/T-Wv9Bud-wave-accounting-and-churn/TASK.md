# TASK: T-Wv9Bud-wave-accounting-and-churn

## Metadata
- Task ID: `T-Wv9Bud-wave-accounting-and-churn`
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
Runs exhaust max_waves at a fraction of budget (small waves, bookkeeping churn). Impact: drives the T-Mw4Clo failures; 3 of 4 hit max_waves at 24-46% budget.

## Evidence
- o-6w86nm: 40 waves used at $183.70 of $400 (46%); ~3.5 units/wave vs wave_size 8; many units
  are status-sync / gate-record / re-review bookkeeping. Sentinel early checkpoints (graph
  edits `early-ck-NN`) close waves after 1-2 of 4 units.
- o-12ln4x hit max_waves=30 at 42.8% budget.

## Root cause (as analysed 2026-11-11)
max_waves counts checkpoints, and early checkpoints plus small waves consume it faster
than budget. Ticket/STATUS reconciliation is emitted as separate units each wave.

## Proposed fix (hypothesis; evaluate before implementing)
- Scale max_waves with budget or count only "full" waves; or make early checkpoints not
  consume a wave.
- Batch doc/status sync into the closeout tail or one unit per N waves.
- Report units/wave and $/wave in the digest so the overseer sees the trend.

## Acceptance Criteria
1. Analysis of 3+ completed runs recorded; chosen accounting change has a test.
2. Re-analysis notes and the chosen option (with rejected alternatives) are recorded in STATUS.md.
3. `pytest -q`, `ruff check .`, `mypy src` green; template change also reflected in
   `overseer-contract.md.tmpl` / instructions / README where relevant.

## Dependencies
- See the epic's Task List for ordering.

## Artifacts
- Docs/comments: `meta/tickets/E-Rr5Lnc-overseer-run-resilience/T-Wv9Bud-wave-accounting-and-churn/`

## Comments
- By: Claude (Opus 5.5, interactive session) · Role: architect · Date: 2026-11-11 · Comment: Filed from the cross-workspace failure RCA in the epic. Fix not evaluated in depth.
