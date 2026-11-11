# TASK: T-Ng1Wsp-no-git-workspace-mode-for-overseer

## Metadata
- Task ID: `T-Ng1Wsp-no-git-workspace-mode-for-overseer`
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
overseer-runner always runs an LLM git-branch-off stage, even with no repo / no-git mode. Impact: 1 stop (o-92c2lh); blocks git-free use such as the Harbor benchmark ao arm.

## Evidence
- `ao-runner-ao` o-92c2lh-minibots...: git-branch-off aborted ("this template needs a pushed
  branch"; workspace root not a git repo; branch_policy said "No git changes ... commit and push").
- Harbor ao arm (T-Hb3Adp) wants local workspace-only mode with no git changes; it currently
  relies on branch_policy wording for the LLM git-operator to write git-go-ahead.md.

## Root cause (as analysed 2026-11-11)
ADR-0022 makes repo_set optional at engine level, but the overseer-runner workflow
template unconditionally contains the `git-branch-off` task (an LLM agent whose instruction
says to abort when there is no branch) and, with final_push, the push tail. There is no
deterministic way to say "no git".

## Proposed fix (hypothesis; evaluate before implementing)
- Add an explicit template param (e.g. `git_mode: branch|none`) or derive from workspace-only
  mode; in `none`, git-branch-off is replaced by a deterministic hook writing git-go-ahead.md
  (or removed), final_push is forced off, and instructions tell units not to commit.
- Validate the combination in preflight (T-Pf8Chk).

## Acceptance Criteria
1. An overseer run on a non-git directory with git_mode=none reaches intake with no LLM git stage and makes no git changes (e2e).
2. Re-analysis notes and the chosen option (with rejected alternatives) are recorded in STATUS.md.
3. `pytest -q`, `ruff check .`, `mypy src` green; template change also reflected in
   `overseer-contract.md.tmpl` / instructions / README where relevant.

## Dependencies
- See the epic's Task List for ordering.

## Artifacts
- Docs/comments: `meta/tickets/E-Rr5Lnc-overseer-run-resilience/T-Ng1Wsp-no-git-workspace-mode-for-overseer/`

## Comments
- By: Claude (Opus 5.5, interactive session) · Role: architect · Date: 2026-11-11 · Comment: Filed from the cross-workspace failure RCA in the epic. Fix not evaluated in depth.
