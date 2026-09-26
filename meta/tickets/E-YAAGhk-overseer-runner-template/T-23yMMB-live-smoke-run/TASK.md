# TASK: T-23yMMB-live-smoke-run

## Metadata
- Task ID: `T-23yMMB-live-smoke-run`
- Epic ID: `E-YAAGhk-overseer-runner-template`
- Owner: tester
- Created: 2026-09-26
- Last Updated: 2026-09-26
- Status: Draft (MVP since Rev 2)
- Estimate: 1 day (8 h). The real spend is capped at $25.

## Requirements Mapping
- Requirement IDs: FR-17, NFR-8, NFR-9 (legibility check), and real-LLM adherence to FR-3/FR-6/FR-9
- Design: `docs-md/overseer-runner-hld.md` §18 (Live), §23.3 rows 1 and 6

## Description
In a scratch workspace (never a sibling `ao-runner-*` repo), with the freshly installed `ao`, check
`ao --version` or `install.sh --check`. The memory notes the global snapshot goes stale. Run
`ao new overseer-runner` against a toy repo with a **two-ask** `prompt.md`:
- ask 1: a small code change, for example a CLI flag plus a test
- ask 2: a short doc

Use these params: `--param run_budget_usd=25 --param task_budget_usd=6 --param wave_size=3 --param
max_waves=5`, keeping the default stage percentages. Run it to completion with real `claude_cli`
agents.

Collect under `output/E-YAAGhk-overseer-runner-template/smoke/`:
- the ledger
- every `ck-*/{digest,verdict,report}`
- `final/{verify,closeout}.md`
- `state.json`
- a `summary.md`

`summary.md` records:
- total spend
- overseer spend, as the sum of `ck-*` costs, and its % of the total (NFR-8 target ≤ ~10%)
- the stage transitions
- the number of checker rejections, per rule id, with each self-corrected via dry-run or causing a
  failed checkpoint
- the signals raised and the responses given
- whether each ask ended usable
- tuning recommendations: `GOAL_SIMILARITY_REJECT`, `stall_waves`, and default costs

If the budget is too small to reach the stabilize stage organically, run a second scenario with
`run_budget_usd=12`, so that the stabilize and closeout stages are exercised.

## Acceptance Criteria
1. The evidence files above exist. The run reached `outputs/final/closeout.md`, or the failure is
   diagnosed in `summary.md` with a follow-up ticket filed.
2. `summary.md` reports the overseer cost %. If it is > 10%, a recommendation is recorded
   (`overseer_effort` or model) and handed to T-gbccdr.
3. At least one run exercised a stage transition beyond `explore`.
4. The real spend is ≤ $25 in total across the runs, per `state.json` cumulative costs.
5. The findings are recorded in STATUS.md and linked from the epic STATUS.md.

## Risks
- Real-LLM non-determinism. This is evidence, not a CI gate.
- The cost of the smoke run itself. It is capped.

## Dependencies
- T-WruPiv (harness-proven template), all implementation tasks, and a fresh `ao` install.

## Pseudocode / Algorithm
```text
N/A — operational run + analysis.
```

## Schemas / Interface Notes
- N/A

## Handoff Boundary
- Upstream: all implementation tasks.
- Downstream: T-gbccdr (tuning notes, deviations).

## Artifacts
- `output/E-YAAGhk-overseer-runner-template/smoke/`
