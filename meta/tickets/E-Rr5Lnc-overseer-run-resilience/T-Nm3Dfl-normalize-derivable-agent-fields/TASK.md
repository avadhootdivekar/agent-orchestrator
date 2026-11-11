# TASK: T-Nm3Dfl-normalize-derivable-agent-fields

## Metadata
- Task ID: `T-Nm3Dfl-normalize-derivable-agent-fields`
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
Reject-on-missing for fields the tool can derive (barrier, breadcrumb unit_id). Impact: 2 run stops.

## Evidence
- `ao-runner-finplan` o-k6shov-ux-complete-500 ck-13 cycle-2 post_hook:
  `OV-R10: next checkpoint barrier must be 'soft', got None (tasks[10])`.
- `ao-runner-finplan` o-12ln4x-ux-revamp-3 ck-13 pre_hook:
  `OV-BC-1: unit_id=w13-02-auth-config-ac3-5 breadcrumb.unit_id=None` (a unit's progress
  breadcrumb omitted its own id; the NEXT checkpoint's prep failed the run).

## Root cause (as analysed 2026-11-11)
The validators reject values that have exactly one legal value (`barrier` must be `soft`) or
that are known from context (the breadcrumb's unit_id is its file name / owning task). These
are LLM omissions, but they carry no information and should not be able to stop a run. A bad
breadcrumb from one unit also fails a later checkpoint rather than the unit that wrote it.

## Proposed fix (hypothesis; evaluate before implementing)
- Normalise before validating: default `barrier` to `soft`; fill breadcrumb `unit_id` from the
  file name/owning task (log a warning instead of failing).
- Validate a unit's breadcrumb in that unit's own post-hook (so a retry targets the unit), and
  let ckpt-prep degrade a bad breadcrumb to a `breadcrumb_integrity` signal, not a crash.
- Inventory every OV-* rule and classify it: derivable (normalise), correctable (repair loop,
  T-Rp7Vfb), or fatal.

## Acceptance Criteria
1. Missing barrier / missing breadcrumb unit_id no longer fail a run (tests).
2. Rule inventory with classification recorded in STATUS.md.
3. Re-analysis notes and the chosen option (with rejected alternatives) are recorded in STATUS.md.
4. `pytest -q`, `ruff check .`, `mypy src` green; template change also reflected in
   `overseer-contract.md.tmpl` / instructions / README where relevant.

## Dependencies
- See the epic's Task List for ordering.

## Artifacts
- Docs/comments: `meta/tickets/E-Rr5Lnc-overseer-run-resilience/T-Nm3Dfl-normalize-derivable-agent-fields/`

## Comments
- By: Claude (Opus 5.5, interactive session) · Role: architect · Date: 2026-11-11 · Comment: Filed from the cross-workspace failure RCA in the epic. Fix not evaluated in depth.
