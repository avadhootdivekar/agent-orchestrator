# TASK: T-xefapr-cli-budget-flags

## Metadata
- Task ID: `T-xefapr-cli-budget-flags`
- Epic ID: `E-j4gno6-token-budget-rate-limit`
- Owner: TODO
- Created: 2026-06-18
- Last Updated: 2026-06-18
- Status: Draft
- Estimate: < 2 days

## Requirements Mapping
- Requirement IDs: FR-7, FR-10, NFR-6

## Description
Surface budget config on the CLI and construct the `DefaultBudgetManager` +
`HeuristicTokenEstimator`, injecting them into the `Orchestrator` in
`ao run` and `ao resume`.

Add typer options to `run` and `resume`:
- `--budget-total INT` (overrides `budget.total_tokens`)
- `--rate-tokens INT` and `--rate-window [minute|ten_minutes|hour]` (overrides `budget.rate`)
- `--on-exhaustion [stop|wait]` (overrides `budget.on_exhaustion`)
- `--pessimism-buffer FLOAT` (overrides estimator buffer; optional)

Build an **effective** `BudgetSpec` by merging CLI flags over `wf.budget` (CLI > spec).
If neither spec nor CLI define any budget, pass `budget_manager=None` (no-op path).
Construct `DefaultBudgetManager(effective_budget, clock)` and `HeuristicTokenEstimator()`
and pass both into `Orchestrator(...)`. Reuse the same `clock` for the manager and the
`RunStateStore` so all time math is consistent.

## Acceptance Criteria
1. Given a spec with `budget.total_tokens=5000` and CLI `--budget-total 1000`, When `ao run` builds the effective budget, Then the effective total is `1000` (CLI wins).
2. Given a spec with no `budget` and no budget CLI flags, When `ao run` runs, Then `budget_manager=None` is passed and the run behaves exactly as today.
3. Given CLI `--on-exhaustion wait`, When the run hits a rate limit, Then it waits (verified via the engine path; CLI just plumbs the flag).
4. Given `--rate-tokens 500 --rate-window minute`, When the effective budget is built, Then `rate.tokens==500`, `rate.window=="minute"`.
5. Given an invalid combination (e.g. `--rate-tokens` without `--rate-window` and no spec rate window), Then a clear CLI error is printed and exit code is non-zero (re-uses the cross-validation from T-oh5gl5).
6. `ao resume` accepts the same flags and applies the same precedence, so a resumed run can change `on_exhaustion`/limits. `ruff`/`mypy`/`pytest` green.

## Risks
- Precedence bugs: ensure CLI > spec at the field level (a CLI `--budget-total` must not wipe a spec `rate` block). Merge per-field, not whole-object replace.
- Constructing two clocks (manager vs runstate store) could drift — share one clock instance.

## Dependencies
- Upstream: T-algywf (Orchestrator accepts `budget_manager`/`estimator`/`clock`), T-7kp8iv, T-n7hmwj, T-oh5gl5.
- Downstream: T-67kiia (CLI-level tests optional; core covered at engine level).

## Pseudocode / Algorithm
```text
FUNCTION build_effective_budget(wf, cli) -> BudgetSpec | None:
  base = wf.budget or BudgetSpec()
  total = cli.budget_total if cli.budget_total is not None else base.total_tokens
  rate  = merge_rate(base.rate, cli.rate_tokens, cli.rate_window)
  on_ex = cli.on_exhaustion or base.on_exhaustion
  est   = base.estimator.model_copy(update={"pessimism_buffer": cli.pessimism_buffer} if cli.pessimism_buffer else {})
  IF total is None AND rate is None: RETURN None       # no-op path
  eff = BudgetSpec(total_tokens=total, rate=rate, on_exhaustion=on_ex, estimator=est)
  cross_validate_budget(eff)                           # reuse T-oh5gl5 validation
  RETURN eff
```

## Schemas / Interface Notes
- Interface / API (CLI): `ao run/resume --budget-total --rate-tokens --rate-window --on-exhaustion --pessimism-buffer`.
- Spec / data schema: builds an effective `BudgetSpec`.
- Triggers / events: N/A.
- Artifacts: none.

## Handoff Boundary
- Upstream: engine constructor + budget models.
- Downstream: end users / integration tests.
