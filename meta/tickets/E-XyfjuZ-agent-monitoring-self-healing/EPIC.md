# EPIC: E-XyfjuZ-agent-monitoring-self-healing

## Metadata
- Epic ID: `E-XyfjuZ-agent-monitoring-self-healing`
- Title: Agent-based workflow monitoring & self-healing (guardrail modes)
- Owner: dev-epic agent
- Created: 2026-07-14
- Last Updated: 2026-07-15
- Status: Done

## Summary
- Goal: Add agent-based monitoring + self-healing to workflow runs, driven entirely through the
  existing `ao run`/`ao resume` commands. Guardrails come in two modes: `recommend` (monitor may
  extend a breaker's threshold once, bounded) and `hard` (never extendable/consultable — today's
  behavior, default). Task-failure self-healing is a separate opt-in (`monitoring.self_heal`)
  that lets a task which exhausted its retries get one bounded extra retry when the monitor
  judges the failure transient.
- Scope In: `CircuitBreakerSpec.mode` field + schema; new `monitoring.py` (`Monitor` ABC,
  `RuleBasedMonitor`, `AgentMonitor`); two engine consult points (breaker-trip, task-failure);
  `monitoring:` project-config section + minimal CLI/env toggle; observability events +
  `RunState` fields; validation; docs/ADR/example spec.
- Scope Out (Non-MVP, see epic context doc for full list): periodic/pulse monitoring, cross-run
  learning, spec/code-mutation healing, notifications, self-heal for `timed_out`/`cancelled`,
  any change to the existing `ao resume --extend-breaker` operator mechanism, `AgentMonitor`
  cost/budget integration (documented known limitation, see ADR-0004 Decision 8).

## Requirements
See full MVP/Non-MVP/Stretch breakdown with acceptance criteria + verification methods in the
epic context doc: [`docs-md/ai-epics/E-XyfjuZ-agent-monitoring-self-healing.md`](../../../docs-md/ai-epics/E-XyfjuZ-agent-monitoring-self-healing.md).

Headline requirements:
- FR-1: `CircuitBreakerSpec.mode: "hard"|"recommend"` (default `"hard"`, byte-identical)
- FR-2: Consult Point A — recommend-mode breaker-trip consult (extend/halt, bounded)
- FR-3: Consult Point B — opt-in task-failure self-heal (retry/accept_failure, bounded)
- FR-4: `Monitor` ABC + `RuleBasedMonitor` (default, deterministic) + `AgentMonitor` (agent-based)
- FR-5: `monitoring:` config section + minimal CLI/env knob (three-layer precedence, simplified)
- FR-6: Observability events + persisted `RunState.monitor_decisions`/counters
- FR-7: Schema/model validation kept derived from source of truth, not hand-copied
- FR-8: No new CLI subcommand
- NFR-1: Hard limits never extendable/consultable (structural + defensive enforcement)
- NFR-2: Monitor failure never regresses run safety vs. today (safe-default fallback)
- NFR-4/NFR-5: Backward-compat + `evaluate_breakers` byte-identical no-op guarantee preserved

## Task List
- [x] `T-mYMiPK-guardrail-mode-schema-model` — `CircuitBreakerSpec.mode` + schema + sync test
- [x] `T-h2XLxe-monitor-abstraction` — new `monitoring.py` module (ABC + 2 implementations)
- [x] `T-TdildW-breaker-consult-engine` — Consult Point A engine wiring + RunState fields
- [x] `T-yjtdAq-self-heal-engine` — Consult Point B engine wiring + RunState fields
- [x] `T-QyNnf5-config-cli-wiring` — monitoring config + CLI/env + `run`/`resume` wiring
- [x] `T-Wx8vUq-tests-e2e-monitoring` — full unit/integration/CliRunner test matrix
- [x] `T-H8Mmog-docs-adr-examples-review` — docs, ADR-0004, examples, learnings, reviewer + late gate

## Risks and Dependencies
- Depends on (reuses, does not modify): `breakers.py::apply_breaker_extension`/`record_trip`,
  `RunState.breaker_overrides`, `RunState.tripped_breakers` latch semantics (E-3JTmVu, E-rc7k2v).
  Confirmed unmodified: `breakers.py`/`spec.py`/`dag.py`/`runstate.py`/`artifacts.py` all show zero
  diff for this epic (`git status`/`git diff --stat` confirms, re-checked after the late-gate fix).
- D5 (bounded stderr.txt read) resolved by the early-gate reviewer pass: simplified to zero new
  file-content reads (see epic context doc's "Early gate" section + ADR-0004 for the final design).
- Late-gate reviewer found 1 Critical (self-heal retry silently discarding pre-heal cycle actuals
  from `TaskRunState.cumulative_*`) — fixed in `engine.py` with 2 new regression tests; see epic
  doc Iteration 4 for full disposition of every finding (1 Critical, 2 Warnings, 3 Suggestions).
- None blocking. Epic complete.

## Links
- Design doc: [`docs-md/ai-epics/E-XyfjuZ-agent-monitoring-self-healing.md`](../../../docs-md/ai-epics/E-XyfjuZ-agent-monitoring-self-healing.md)
- LLD: [`docs-md/lld-agent-monitoring-self-healing.md`](../../../docs-md/lld-agent-monitoring-self-healing.md)
- ADR: [`docs-md/adr/ADR-0004-agent-monitoring-guardrail-modes.md`](../../../docs-md/adr/ADR-0004-agent-monitoring-guardrail-modes.md)
- Example spec: [`specs/examples/workflow-monitoring.json`](../../../specs/examples/workflow-monitoring.json)
- Output artifacts (if any): `output/E-XyfjuZ-agent-monitoring-self-healing/` (none produced —
  all evidence lives in the ticket/epic docs and the test suite itself)
