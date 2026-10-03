# Live run summary (expensive runs)

Once a run's cumulative **actual** cost crosses **$5**, the engine keeps a short Haiku-written
digest of the run current: what is done, what is in progress, the roadmap, and risks. It is
meant for long, costly runs where the task table alone is hard to skim.

## Behaviour

- **Fixed policy, not configurable.** Threshold (`SUMMARY_COST_THRESHOLD_USD = 5.0`), cadence,
  model (`haiku` alias = latest) and cost cap are constants in
  `src/agent_orchestrator/summarizer.py`. No workflow, `.ao/config.yaml`, env or CLI knob exists
  (a test asserts the config models carry none).
- **Cadence.** Refreshes after every 5 newly settled tasks, plus one final summary when the run
  ends (any outcome). Runs on a daemon thread, single-flight, so the scheduler never waits.
- **Marginal cost.** Interim refreshes stop once summary spend reaches 2% of the run cost
  (`SUMMARY_MAX_COST_FRACTION`); the final summary is exempt. Input is a compact digest
  (task ids, statuses, costs, pending ids) plus the previous summary, never transcripts. The
  summary's own spend is reported in `summary.json` and is *not* added to task/run cost totals
  (so it can't trip `run_cost_usd` breakers or distort budgets).
- **Advisory only.** Any failure (executor error, no output, crash) is logged and swallowed; the
  previous summary stays. A run that has a summary on disk stays "expensive" across resume.
- **NFR-1.** The engine never reads the summary body: Haiku reads its previous summary as an
  input artifact and writes the next one; readers (CLI, dashboard) load the files.

## Where it lives

`<workspace>/.orchestrator/runs/<run_id>/summary/` — `summary.md` (digest), `summary.json`
(`updated_at`, `final`, `run_cost_usd`, `summary_cost_usd`, `calls`, ...), plus `context.json`,
`previous.md`, `capture/` for audit.

## Viewing

- CLI: `ao summary --run-id <id> [--workspace DIR] [--json]` (exit 1 if none yet).
- Dashboard: "Run summary" panel on the run page (polled with the page; hidden for cheap runs);
  API `GET /api/runs/{id}/summary` → `{available, text, meta}`. Text is rendered as plain text.
