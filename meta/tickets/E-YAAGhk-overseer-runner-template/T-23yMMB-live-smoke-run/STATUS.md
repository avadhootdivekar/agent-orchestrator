# STATUS

- ID: `T-23yMMB-live-smoke-run`
- Updated At: 2026-09-27
- State: Done, AC3 deviation recorded
- Owner: tester → dev-epic

## This update
- Ticket created by the architect design pass (Rev 2, after the Phase-4 consultations). Sprint: S2 (MVP).

By: architect · Role: architect · Date: 2026-09-26 · Comment: Created from `docs-md/overseer-runner-hld.md` Rev 2. The ACs are pass/fail and agent-executable, and the task is sized ≤3 days. Not started.

By: dev-epic · Role: manager · Date: 2026-09-27 · Comment: Ran the live smoke test with
real spend, explicit user authorization obtained first (≤$25 cap per the architect's
design). Fresh `ao` install confirmed at this branch's HEAD (`e76caef`, `install.sh
--force --yes`); a scratch toy repo (`greet` CLI, never a sibling `ao-runner-*` repo)
with a real bare `origin` remote; real `claude_cli` agents for all 6 required roles
(config pattern adapted from `ao-runner-finplan/specs/agents.json`: `claude
--dangerously-skip-permissions --disallowedTools ScheduleWakeup,CronCreate,Monitor -p
{prompt}`).

Three real attempts, all counted against the cap:
- **run1** (`run_budget_usd=25`, the ticket's own literal value): real `git-branch-off`
  correctly aborted (no `origin` remote yet — my setup gap, fixed), then real `intake`
  itself computed that a 3-unit wave 1's projected cost (`3×8+5+3×8=$53`, using the
  template's **hardcoded, non-configurable** `default_unit_cost_usd=8`/
  `default_ckpt_cost_usd=5`) is 212% of `run_budget_usd=25`, forcing stage=`closeout`
  (`allowed_wave_size=0`) before any wave could even be emitted. The real agent wrote a
  `control/hold-request.json` explaining this and asking to either raise the budget or
  lower the defaults — correctly diagnosed by the real LLM, independently re-verified by
  dev-epic against `overseer_tool.py`'s own `derive_budget`/`compute_allowed_wave_size`.
  **Headline finding**: checked exhaustively across every `wave_size`/`final_push`
  combination, `run_budget_usd=25` AND the ticket's own suggested `run_budget_usd=12`
  fallback are BOTH unconditionally infeasible — the minimum viable budget for
  `wave_size=3`/`final_push=true` to reach even `explore` is ~$67 (~$59 for `converge`).
  Real cost: $1.6607 (git-branch-off + intake only, stopped there).
- **run2** (`run_budget_usd=62`, corrected): full real run succeeded end to end —
  `outputs/final/closeout.md` exists, real code (`--shout` flag) + real doc change, both
  independently re-verified by dev-epic (checked out the real branch, ran the toy repo's
  real `pytest` — 2/2 passed — and the real CLI functionally). Real cost: $3.5219.
  Overseer (`ck-01`) cost: $0.5219 = **14.8%** of total (above the NFR-8 ≤~10% target).
  Real stage stayed `explore` throughout (`ck-01`'s digest recomputed from real, ~20-25x
  cheaper per-unit costs than the hardcoded defaults, correctly self-correcting).
- **run3** (`run_budget_usd=57`, corrected, to probe the "stabilize"-band intake
  computation): also succeeded end to end, same independent re-verification (2/2 real
  pytest pass). Real cost: $3.2926. Overseer cost: $0.5523 = **16.8%** of total. A real
  signal fired for the first time (`S-01-01`, `breadcrumb_integrity`, high severity — the
  real `w01-02-readme-options` unit mislabeled its own repo id as `toy-repo` instead of
  the configured `target`), correctly diagnosed and `accept`-ed by the real `ck-01`
  checkpoint with a full rationale, correctly validated by `OV-R12` — a clean positive
  result demonstrating the signal-response design works end to end with a real LLM.

**Total real spend across all 3 attempts: $8.4752, well within the $25 cap (AC4 met).**

**AC3 ("at least one run exercised a stage transition beyond explore") is the one AC not
organically met** — judged, with full reasoning recorded in `summary.md`'s "Deviation"
section, not safely achievable within a responsible smoke-test budget given Finding 1
(the hardcoded-default-cost floor forces any survivable `run_budget_usd` to be so large
relative to this tiny task's real per-unit cost that real cumulative spend never
approaches even the `converge` threshold). Not fabricated or forced; flagged honestly.
Design-time coverage of the stage machine itself is separately and thoroughly covered by
`T-WruPiv`'s `test_scenario_b_stage_escalation` (scripted but against the real
`derive_budget` formula, independently reproduced in that ticket's own STATUS.md).

Full findings, exhaustive budget-math tables, AC-by-AC status, and tuning
recommendations: `output/E-YAAGhk-overseer-runner-template/smoke/summary.md`. Full
artifacts (real `state.json`, `ck-01/{digest,verdict,report,check-result}.json`,
`final/{verify,closeout}.md`, ledger) for all 3 attempts under the same directory.

## Evidence
- `output/E-YAAGhk-overseer-runner-template/smoke/summary.md` (full write-up).
- `output/E-YAAGhk-overseer-runner-template/smoke/run1-b25-diagnosed-failure/` — the
  real `INT-4` stray-hold-request finding, the real agent's own git-abort diagnosis.
- `output/E-YAAGhk-overseer-runner-template/smoke/run2-b62-success/` — full real
  successful-run artifacts.
- `output/E-YAAGhk-overseer-runner-template/smoke/run3-b57-success/` — full real
  successful-run artifacts, including the real `breadcrumb_integrity` signal.
- Independent verification (not just the run's own claims): real branch checkout + real
  `pytest` run (2/2 passed) + real functional CLI check, for both run2 and run3.

## Risks / Blockers
- No blockers. AC3 deviation is recorded above and in `summary.md`, with a clear
  recommendation for how to close it once Finding 1's tuning fix ships (or accept the
  scripted e2e coverage as sufficient for the stage machine's own correctness).

## Next actions
1. None remaining for this ticket. Downstream: `T-gbccdr` folds in Finding 1 (the
   default-cost-constant tuning recommendation, the headline finding), Finding 2
   (overseer_effort sizing), and the AC3 deviation note.
