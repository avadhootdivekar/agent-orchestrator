# EPIC: E-79one2-agent-composition-and-model-governance

## Metadata
- Epic ID: `E-79one2-agent-composition-and-model-governance`
- Title: `Agent composition and model governance`
- Owner: `TODO (unassigned)`
- Created: `2026-10-16`
- Last Updated: `2026-10-16`
- Status: `Draft`

## Summary
- Goal: Make agents composable, make model/tool choices enforceable and auditable, and support cheap high-fan-out reviewer pools.
- Scope In: Agent layering and introspection; model assertion and lints; tool profiles; schema post-hook; reduce primitive; cost-by-model guards; concurrency cap; effort override; dry-run (backlog).
- Scope Out: Run-scoped dynamic agents (declined for now, security: argv smuggling; per-unit model plus `allowed_models` covers the need); plugin packages (deferred until composition is proven); agent templating (replaced by concurrency cap).

## Requirements
- FR-1: Layered/extendable agent definitions with provenance.
- FR-2: Model assertion (planned vs actual) and validate lints.
- FR-3: Enforced tool/sandbox profiles.
- FR-4: Fan-out reviewers have schema validation, a reduce step and bounded concurrency.
- FR-5: Cost guard rails by model.
- NFR-1: Backwards compatible; existing single-file workflows unchanged.
- NFR-2: Security: manifests cannot smuggle argv (`validate_model_id` property preserved).

## Task List
- [ ] `T-ag1a01-composable-agent-definitions` — Layered agent files, `extends`, `agents.d/` (priority: P1)
- [ ] `T-ag1b02-agents-list-show-explain` — `ao agents list|show|diff`, `ao validate --explain-agent` (priority: P1)
- [ ] `T-ag2a03-model-assertion-and-validate-lints` — Planned vs actual model assertion and lint (priority: P1)
- [ ] `T-ag3b04-agent-tool-and-sandbox-profiles` — Enforced tool/permission profiles per agent (priority: P1)
- [ ] `T-ag4c05-json-schema-output-posthook` — Built-in `json_schema` post-hook with retry-once (priority: P2)
- [ ] `T-ag5d06-fanout-reduce-primitive` — First-class 'N tasks then one consolidator' with partial-failure semantics (priority: P2)
- [ ] `T-ag6e07-cost-by-model-guardrails` — `max_cost_by_model` and overspend-vs-plan alert (priority: P2)
- [ ] `T-ag7f08-per-kind-agent-concurrency-cap` — Per-agent / per-kind concurrency group (priority: P2)
- [ ] `T-ag8g09-overseer-unit-effort-override` — Gated per-unit `effort` override in overseer briefs (priority: P3)
- [ ] `T-ag9h10-ao-plan-dry-run` — `ao plan` dry-run planner (backlog) (priority: P3 (backlog))

## Risks and Dependencies
- Suggested order: T-ag1a01 + T-ag1b02, then T-ag2a03, then T-ag3b04 + T-ag4c05, then T-ag5d06 + T-ag6e07 + T-ag7f08; rest backlog.
- Declined/deferred items (C agent templating, D dynamic agents, E plugins) are recorded in `SOURCE-REQUEST.md` triage section.

## Links
- Source request (FinPlan operator, 2026-10-16): `SOURCE-REQUEST.md` in this epic folder.
- Related: `docs-md/model-selection.md`, `docs-md/overseer-runner-hld.md`, epic `E-YAAGhk-overseer-runner-template`.
