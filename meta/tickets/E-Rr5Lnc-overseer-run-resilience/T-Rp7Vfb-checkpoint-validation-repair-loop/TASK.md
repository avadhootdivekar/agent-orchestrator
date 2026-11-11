# TASK: T-Rp7Vfb-checkpoint-validation-repair-loop

## Metadata
- Task ID: `T-Rp7Vfb-checkpoint-validation-repair-loop`
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
A rejected checkpoint verdict/manifest fails the whole run instead of being repaired. Impact: all 7 checkpoint post_hook failures ended the run on the first rejection.

## Evidence
- Every `ov-ckpt-check` failure above (OV-R14 x4, OV-R10 in finplan o-k6shov ck-13 cycle-2)
  went `task.post_hook.failed` -> `monitor.decision: accept_failure` -> `run.end failed`.
- `RuleBasedMonitor.decide_task_failure` retries only when the failure text matches a
  transient pattern; validation errors are deterministic text, so they are never retried.

## Root cause (as analysed 2026-11-11)
The checkpoint is the single most expensive-to-lose step (it gates the whole run) and its
validator is strict, but there is no feedback path: the exact violation text is never shown
back to the overseer session, and there is no deterministic fallback decision. One malformed
field ends a multi-hour run.

## Proposed fix (hypothesis; evaluate before implementing)
- On `ov-ckpt-check` exit 2: resume the same checkpoint session (or re-dispatch) with the
  violation list appended ("your verdict was rejected: ...; fix and rewrite"), up to 2
  repair attempts, before failing the task.
- If still invalid: the tool writes a deterministic safe decision (e.g. `continue` with a
  verify/stabilize wave, or `hold` with the violation as the question) instead of failing.
- Count repairs in the digest/ledger so systematic prompt issues are visible.
- Generalise: a hook result class "llm-correctable" vs "infra" so the monitor can choose.

## Acceptance Criteria
1. Fake-tier test: a checkpoint whose first verdict violates R10 is repaired in-session and the run continues.
2. After N failed repairs the run takes the documented safe fallback, not run failure.
3. Repairs are visible in the digest/ledger and the dashboard.
4. Re-analysis notes and the chosen option (with rejected alternatives) are recorded in STATUS.md.
5. `pytest -q`, `ruff check .`, `mypy src` green; template change also reflected in
   `overseer-contract.md.tmpl` / instructions / README where relevant.

## Dependencies
- See the epic's Task List for ordering.

## Artifacts
- Docs/comments: `meta/tickets/E-Rr5Lnc-overseer-run-resilience/T-Rp7Vfb-checkpoint-validation-repair-loop/`

## Comments
- By: Claude (Opus 5.5, interactive session) · Role: architect · Date: 2026-11-11 · Comment: Filed from the cross-workspace failure RCA in the epic. Fix not evaluated in depth.
