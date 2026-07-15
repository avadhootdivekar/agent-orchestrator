# STATUS

- ID: `T-H8Mmog-docs-adr-examples-review`
- Updated At: 2026-07-15
- State: Done
- Owner: dev-epic agent

## This update
- By: dev-epic agent
- Role: developer
- Date: 2026-07-15
- Comment: All deliverables complete. `docs-md/lld-agent-monitoring-self-healing.md` (full
  HLD+LLD, exact hook-site references, edge-case matrix, assumption log) and
  `docs-md/adr/ADR-0004-agent-monitoring-guardrail-modes.md` (8 decisions + rejected
  alternatives) written. Cross-reference sweep performed across every `docs-md/*.md` file that
  mentions circuit breakers/monitoring (`multi-endpoint-circuit-breaker-hld.md`,
  `lld-run-control-routing-breakers.md`, `token-budgeting-hld.md`,
  `guide-dynamic-task-injection.md`, plus the top-level `hld-agent-orchestrator.md`) — no
  actively-contradicted claims found; 2 additive updates made (top-level HLD's feature-doc
  index, and a new §17 addendum in `lld-run-control-routing-breakers.md` explaining how the
  monitor-driven and operator-driven breaker-extension mechanisms relate). Example spec
  `specs/examples/workflow-monitoring.json` written and validated via `uv run ao validate`
  (OK). Config example snippet included in the LLD (§7). 3 new learnings appended to
  `meta/learnings.md` + distilled into `meta/learning-compact.md`.
  - **Early gate** (design-level, before any code existed): `reviewer` + `architect` run in
    parallel, no showstoppers; findings incorporated into the design before implementation
    (see epic doc's "Early gate" section) — this ran as part of the epic's overall process,
    not this task specifically, but is recorded here for completeness.
  - **Late gate — `reviewer`** (full working-tree diff): 1 Critical, 2 Warnings, 3 Suggestions.
    The Critical finding (a self-heal retry silently discarding the pre-heal cycle's real
    cost/token actuals from `TaskRunState.cumulative_*`, empirically verified by the reviewer
    via a standalone repro script) was fixed in `engine.py` (accumulate actuals across a
    healed retry instead of overwriting) with 2 new regression tests
    (`tests/test_monitoring_self_heal.py::TestSelfHealAccumulatesActualsAcrossHealedCycles`).
    Warning #1 (duplicated project-config-load logic in `cli.py`) fixed via an extracted
    `_load_project_config_or_none()` helper. Suggestion #1 (vacuous-truth-on-empty-list edge
    case in `_consult_breaker_trips`) fixed with an explicit guard. Warning #2 (ticket-sync
    staleness) and Suggestions #2/#3 were accepted as already-expected/already-considered,
    no further action needed — see the epic doc's Iteration 4 evidence log entry for full
    disposition of every finding.
  - **Late gate — `tester`** (representative workflow, real CLI): verified end-to-end via
    `uv run ao resume` in a scratch workspace — a `mode: "recommend"` breaker extended and
    let the run succeed (with `monitor.consult`/`monitor.decision`/`breaker.extend` events and
    the correct `breaker_overrides`/`monitor_decisions` state), and the same breaker declared
    `mode: "hard"` halted immediately with zero monitor consultation — proving the
    byte-identical-by-default guarantee holds through the real CLI. `ao status` also verified
    working. No discrepancies found.

## Evidence
- `uv run pytest -q --cov=agent_orchestrator --cov-report=term-missing` → **785 passed, 3
  skipped**; coverage **TOTAL 92%** (2824 stmts, 217 missed; baseline was 91%/2542/217).
  `monitoring.py`: 100% (130/130 stmts).
- `uv run ruff check .` (whole repo) → 2 errors, confirmed identical to the pre-existing
  baseline (`tests/test_e2e_cli.py`, unrelated).
- `uv run ruff format --check .` (whole repo) → 68 files clean.
- `uv run mypy src` (whole package) → 4 errors, confirmed identical to the pre-existing
  baseline (`_version.py`, unrelated).
- `uv run ao validate --workflow specs/examples/workflow-monitoring.json --reposets
  specs/examples/reposet.json --agents specs/examples/agents.json` → `OK: all specs valid`.
- Late-gate reviewer's full report and tester's full report both on file (this agent's
  transcript); key findings + dispositions summarized in "This update" above and in the epic
  doc's Iteration 4 evidence log entry.

## Risks / Blockers
- None.

## Next actions
1. None — epic complete.
