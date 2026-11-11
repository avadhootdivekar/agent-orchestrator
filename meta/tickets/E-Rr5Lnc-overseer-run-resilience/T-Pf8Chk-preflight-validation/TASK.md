# TASK: T-Pf8Chk-preflight-validation

## Metadata
- Task ID: `T-Pf8Chk-preflight-validation`
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
Configuration errors are found only after launch (sometimes after hours). Impact: 5 stops would have been caught before spending.

## Evidence
- `ao-runner-ai-models` o-95avzh-model-build-try-2 intake pre_hook:
  `OV-CFG-3: max_injected_tasks=160 < need=213` (failed 3 times: launch + 2 resumes).
- o-92c2lh / finplan o-kpor5a: branch_policy contradiction / dirty tree found by the LLM
  git-operator after launch.
- o-z6n5ka / o-1gk30c: `final_push` enabled on a workspace that is not a git repo.
- Raising max_waves on resume requires a matching max_injected_tasks (need =
  max_waves*(wave_size+1+expanders*(sub_wave_size+1))+3); done by hand for o-6w86nm.

## Root cause (as analysed 2026-11-11)
`ao new --validate-only` validates the DAG but not the template's own config rules or the
environment the run needs (git state, disk). The overseer tool's `load_config` checks run
only inside hooks after the run has started.

## Proposed fix (hypothesis; evaluate before implementing)
- Run the template's config validation (overseer_tool load_config / CFG-*) at
  `ao new --validate-only` and at run start/resume.
- Deterministic environment preflight: repo is git iff git stages are enabled, no dirty
  tracked tree when a fresh branch is required, remote exists when final_push, free disk
  above a threshold, branch_policy sanity (e.g. "no git" + final_push).
- When max_waves is raised, auto-derive or warn about max_injected_tasks.

## Acceptance Criteria
1. The o-95avzh config fails at `ao new --validate-only` with the CFG-3 message.
2. final_push on a non-git workspace fails preflight with an actionable message.
3. Re-analysis notes and the chosen option (with rejected alternatives) are recorded in STATUS.md.
4. `pytest -q`, `ruff check .`, `mypy src` green; template change also reflected in
   `overseer-contract.md.tmpl` / instructions / README where relevant.

## Dependencies
- See the epic's Task List for ordering.

## Artifacts
- Docs/comments: `meta/tickets/E-Rr5Lnc-overseer-run-resilience/T-Pf8Chk-preflight-validation/`

## Comments
- By: Claude (Opus 5.5, interactive session) · Role: architect · Date: 2026-11-11 · Comment: Filed from the cross-workspace failure RCA in the epic. Fix not evaluated in depth.
