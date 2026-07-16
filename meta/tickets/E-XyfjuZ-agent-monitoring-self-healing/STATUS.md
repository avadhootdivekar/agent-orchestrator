# STATUS

- ID: `E-XyfjuZ-agent-monitoring-self-healing`
- Updated At: 2026-07-15
- State: Done
- Owner: dev-epic agent

## This update
- By: dev-epic agent
- Role: developer
- Date: 2026-07-15
- Comment: Epic complete. All 7 tasks Done. Early gate (reviewer + architect, design-level,
  parallel, before any code existed) returned no showstoppers with actionable findings
  incorporated (D5 simplified to zero new file reads, D7 upgraded to a full tri-state CLI
  override, D9 added for derived monitor counters — see epic doc's "Early gate" section).
  Late gate (reviewer on the full diff + tester on a representative end-to-end CLI workflow,
  both independent, parallel) returned: reviewer found 1 Critical (a self-heal retry silently
  discarded the pre-heal cycle's real cost/token actuals from `TaskRunState.cumulative_*`,
  weakening `TaskCostUsdBreaker`/`RunCostUsdBreaker` — empirically verified via a standalone
  repro script) which was fixed in `engine.py` with 2 new regression tests, plus 2 Warnings
  (1 fixed — duplicated project-config-load logic in `cli.py`, extracted to a shared helper;
  1 accepted as already-expected process sequencing) and 3 Suggestions (1 fixed — a cheap
  defensive guard against a vacuous-truth edge case; 2 accepted as already-considered
  tradeoffs, no action). Tester independently verified via the real `ao` CLI: a
  `mode: "recommend"` breaker extends and lets a run succeed (with the expected
  `monitor.consult`/`monitor.decision`/`breaker.extend` events and `breaker_overrides`/
  `monitor_decisions` state), and a `mode: "hard"` breaker halts immediately with zero
  monitor consultation — proving the byte-identical-by-default guarantee holds through the
  real CLI, not just unit tests. Full findings + dispositions recorded in the epic context
  doc's Iteration 4 evidence log entry.

## Evidence
- Epic context doc: `docs-md/ai-epics/E-XyfjuZ-agent-monitoring-self-healing.md`
- `uv run pytest -q --cov=agent_orchestrator --cov-report=term-missing` → **785 passed, 3
  skipped** (baseline 686 passed/3 skipped → 99 net new tests, zero regressions). Coverage
  **TOTAL 92%** (2824 stmts, 217 missed; baseline 91%/2542/217). `monitoring.py`: 100%.
- `uv run ruff check .` (whole repo) → 2 errors, confirmed identical to the pre-existing
  baseline (`tests/test_e2e_cli.py`, unrelated, not touched by this epic).
- `uv run ruff format --check .` (whole repo) → 68 files clean.
- `uv run mypy src` (whole package) → 4 errors, confirmed identical to the pre-existing
  baseline (`_version.py`, unrelated, not touched by this epic).
- `uv run ao validate --workflow specs/examples/workflow-monitoring.json ...` → `OK: all
  specs valid`.
- Confirmed via `git diff --stat`: `breakers.py`/`spec.py`/`dag.py`/`runstate.py`/
  `artifacts.py` show ZERO diff — reused as-is, per the epic's narrow-change-scope mandate.
- New files: `src/agent_orchestrator/monitoring.py`; `tests/test_monitoring.py`,
  `tests/test_monitoring_breaker_consult.py`, `tests/test_monitoring_self_heal.py`,
  `tests/test_e2e_monitoring_cli.py`; `specs/examples/workflow-monitoring.json`;
  `docs-md/lld-agent-monitoring-self-healing.md`,
  `docs-md/adr/ADR-0004-agent-monitoring-guardrail-modes.md`.
- Modified files: `src/agent_orchestrator/{engine,models,cli,project_config}.py`,
  `specs/workflow.schema.json`, `tests/{test_routing_breaker_models,test_cli}.py`,
  `docs-md/{hld-agent-orchestrator,lld-run-control-routing-breakers}.md`,
  `meta/{learnings,learning-compact}.md`.

## Risks / Blockers
- None. Epic complete.

## Next actions
1. None — epic complete. See the epic context doc and this ticket tree for all evidence;
   final completion handoff delivered to the requesting thread.
