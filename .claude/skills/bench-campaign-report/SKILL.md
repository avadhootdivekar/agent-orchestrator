---
name: bench-campaign-report
description: Run an ao-bench ranking campaign within a budget (bare claude models and ao workflow subjects such as the Sonnet overseer) and write the results report with scores, solve rates, $/task, $/solved, $/score point, wall time, per-probe-group scores, pairwise CIs and insights. Use when asked to benchmark/compare models or ao subjects, run a campaign, or write up benchmark results.
---

# Ranking campaign + report

Prerequisites: the `benchmarking` skill (rules), a difficulty vector chosen with the `bench-tune` skill, and a
ranking suite generated with fresh seeds whose reference self-check is N/N.

## 1. Preflight (free)
```bash
git status --short                         # clean tree: run.json stamps ao_version (no .dirty)
uv run ao-bench env --probe                # exit 0; baseline lists only built-ins + user config + bundle
make bench-refcheck SUITE=benchmarks/suites/<ranking>/suite.json   # must be N/N
uv run ao-bench validate --suite <suite> --subject <each subject.json>
```
Budget per subject = cap you were given. `run_suite` checks the cap *before* scheduling each task, so overshoot
is up to `max_parallel` in-flight tasks: use `--cost-budget-usd <cap - max_parallel * expected_task_cost>` or a
lower `--max-parallel` for expensive subjects. `ao` template subjects carry their own per-task budget
(`template_params.run_budget_usd`), which bounds them hard.

## 2. Run
Run subjects one by one (each with its own cap/parallelism) and keep a spend ledger (sum `aggregate.total_cost_usd`
of every run.json, including calibration and smoke runs):
```bash
uv run ao-bench run --suite <suite> --subject benchmarks/subjects/claude-haiku.json --cost-budget-usd <cap> --out-dir benchmarks/results
uv run ao-bench run --suite <suite> --subject benchmarks/subjects/ao-overseer-sonnet.json --cost-budget-usd <cap> --max-parallel 2 --out-dir benchmarks/results
...
uv run ao-bench report --run-dir benchmarks/results/<date>-<suite>-<subject> ... --out-dir benchmarks/results
```
`ao-bench campaign --repeats N` does the same with one shared cap and staged repeats; prefer per-subject runs
when subjects differ a lot in cost. Repeats (`--repeat 2`) only for undecided pairs, if budget remains.

While it runs: watch `/var/tmp/ao-bench/<uid>/ws/<run>/<subject>/<task>/` (capture/ or .orchestrator/) and the
task's `bench.log`. An `error` status is infra (excluded from rates) — read its `raw_error` before trusting
anything; a grader/probe error on every subject is a validity bug, not a result.

## 3. Report
`comparison.md` gives: per-subject table, per-task matrix, winners, **Cost & efficiency** ($/task mean and median,
$/solved, $/score point, wall/task, output tokens/task, mean pass fraction per probe group, rule-based
observations), parity, environment isolation, attempted-based rates, pairwise bootstrap CI + Wilcoxon p,
per-family/per-language roll-ups, infra/suspects.

Write the results entry in `benchmarks/README.md` (and the ticket STATUS) with:
1. Setup: suite id, vector, seeds, families/languages, parity fingerprint, effort, max_turns, subjects (models,
   ao template params), date, ao_version, total spend vs budget.
2. A table per subject: mean score, solve rate, $ spent, $/task, $/solved, $/score point, wall/task, core/trap/
   perf pass fractions.
3. Pairwise: which differences are decided (CI excludes 0) and which are not — say "not distinguishable at
   n=<k>" plainly.
4. Insights, each backed by a number or a transcript you read: where subjects differ (which probe group, which
   traps — read `grader_detail.probe_results` of the failed probes), cost/quality frontier (who is dominated),
   orchestration overhead (ao cost and wall vs its bare model, and what the extra spend bought), failure modes
   (timeouts, max_turns exhaustion, skipped tests).
5. Validity & fitting statement: env isolation table clean? any suspect (grading_access) tasks? any harness fix
   made mid-campaign (and was it model-blind)? any knob moved after a non-anchor subject ran? calibration seeds
   disjoint from ranking seeds? Limitations (n, single language, perf noise).
Commit the run dirs + comparison + README entry; update the ticket and epic STATUS.
