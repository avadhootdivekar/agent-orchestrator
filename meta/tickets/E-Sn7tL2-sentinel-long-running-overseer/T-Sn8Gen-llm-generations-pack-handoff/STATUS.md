# STATUS

- ID: `T-Sn8Gen-llm-generations-pack-handoff`
- Updated At: `2026-11-09`
- State: `Done`
- Owner: `developer (ao run o-bi0rvg-sentinel-dashboard-impl, unit w07-01-sentinel-generations)`

## This update
- By: claude-sonnet-5-5 · Role: developer · Date: 2026-11-09 · Comment: Implemented LLM generations: `SentinelDriver` ABC +
  `ClaudeCliSentinelDriver`, 1-worker lane outside `max_parallel`, `sentinel/gNN/{pack,handoff.json,report.md,memory-append.md}`,
  deterministic size-capped pack, tool-owned memory merge, rotation (TTL / max triggers / tokens / handoff grace), ungraceful
  handling (incl. resume reconcile), `sentinel-gNN` cost rows, budget stop, consecutive-rejection auto-pause, trigger cooldowns +
  per-hour cap, rendered `sentinel-contract.md`. As-built: `docs-md/sentinel-hld.md` §8.6.

## Evidence
- `tests/test_sentinel_generations.py` (34 tests, scripted fake driver + fake `claude` executable, no real LLM): pack byte
  determinism + caps/manifest flags, memory merge, handoff validation, hash-chained ledger, triggers/cooldown/hour cap,
  forced rotation reasons, ungraceful, budget stop with ticks continuing, e2e ≥3 generations with handoff carried and an
  ungraceful generation, cost in `compute_run_usage_totals` + `aggregate_usage`, auto-pause, resume (counter, memory, last handoff,
  no duplicate proposal), lane error disables only the LLM lane, disabled leaves no trace.
- Gates: see the unit report `outputs/waves/w07/w07-01-sentinel-generations.md` for the full-suite numbers.
- Default-off golden tests unchanged and green; registry schema snapshot regenerated (+5 additive `sentinel.llm.*` options).

## Risks / Blockers
- Live LLM generation not exercised (T-SnXAcc live smoke). `max_tokens` stop only effective when a driver reports progress.
- Triggers `new_signal`, `stage_change`, `idle_slots_no_backlog` are not wired yet (need T-Sn7Bkl / T-Sn9Pow).

## Next actions
1. T-Sn9Pow: powers (`inject`, `edit_pending`, LLM kill gating) in the checker; routed-runner L2 planner.
2. T-Sn5Ui1: surface generation / spend / pause reason (`ao ctl status` already returns generation + spend).
