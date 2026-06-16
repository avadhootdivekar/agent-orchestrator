# ADR-0001 — Orchestration approach: Claude-native engine + thin spec adapter

- Status: **Accepted**
- Date: 2026-06-16
- Deciders: avadhoot (user), Claude (architect role)
- Related: discovery epic [`E-or7k2d-orchestrator-discovery`](../../ad/tickets/E-or7k2d-orchestrator-discovery/EPIC.md) · build epic [`E-m2k9pa-orchestrator-mvp-a`](../../ad/tickets/E-m2k9pa-orchestrator-mvp-a/EPIC.md) · survey [`output/.../market-survey.md`](../../output/E-or7k2d-orchestrator-discovery/market-survey.md)

## Context
The agent-orchestrator must drive multi-agent, multi-repo workflows from declarative JSON/YAML specs, with cron/event triggers, artifact-by-path IO, resume/idempotency, and — critically — **orchestrator context hygiene (NFR-1)**: the orchestrator must know only paths and statuses, never payload contents.

The discovery survey established that:
- The DAG/spec/scheduling features (FR-1..FR-6) are **commodity** in 2026 (Kestra, Airflow, Dagster, Temporal, Argo, Windmill).
- The differentiators (NFR-1 context hygiene, FR-7 multi-repo agent sets) are best served by **Claude Code native** primitives (Agent Teams give each agent its own context window; Dynamic Workflows give a programmable orchestrator) at **$0 incremental cost**.
- Self-hosting **Kestra** (Apache-2.0) is a near-complete declarative alternative for < $40/mo.

## Decision
Adopt **Option A — Claude-native engine + a thin spec/adapter layer**:
- **Engine**: Claude Code native execution (subagents / Agent Teams / `claude` CLI) runs each task in an isolated context window — this is the NFR-1 mechanism, for free.
- **Custom code (the ~5% that's missing)**: a small Python package `agent_orchestrator` that loads/validates a `workflow.json` spec, builds + cycle-checks the DAG, resolves artifact paths, dispatches each node to an executor **passing only paths/ids**, tracks run-state for resume, and handles triggers. The orchestrator core has **no API to read artifact/payload contents**.
- **Executors are pluggable** behind an `Executor` interface (ABC/protocol + injection). The MVP ships `ClaudeCliExecutor` (+ `FakeExecutor` for tests).

## Consequences
- We do **not** build a from-scratch general orchestrator (no custom scheduler/UI/distributed engine). Lower effort (~1 week MVP), no maintenance of commodity features.
- Context hygiene is **architecturally enforced**: only paths/ids are interpolated into executor commands; agents read contents in their own context.
- Some scheduling/observability niceties (rich UI, durable distributed execution) are deferred to non-MVP or to a future Kestra adoption.

## Strong runner-up — Option B: Kestra + adapter (revisit trigger)
**Recorded at user request.** Self-hosted **Kestra 1.0** (Apache-2.0, declarative YAML DAGs, cron/event triggers, namespaces ≈ repo-sets, AI-agent tasks, UI/retries) fronting coding-agent CLIs is a strong alternative for < $40/mo and is the closest match to our declarative-spec intent **without owning an engine**.

We **revisit Option B** if any of these become true:
1. We need a hardened scheduler, web UI, or durable/distributed execution that the thin engine doesn't provide.
2. Cron/event triggering or observability requirements outgrow the MVP.
3. Non-Claude executors (other agent runtimes) become first-class and we want a vendor-neutral spec engine.
4. Operational burden of the custom engine exceeds the cost of running Kestra.

**Migration is low-cost by design**: because executors and the spec schema are decoupled from the engine, Option B is added as a `KestraExecutor` / spec exporter, not a rewrite. The `workflow.json` schema is intentionally close to Kestra-expressible concepts to keep that path open.

## Alternatives rejected
- **Option C — full from-scratch build as a product/business**: rejected. Survey verdict: not a viable business; commoditized + crowded + vendor-absorbed; no moat.
- **Option D — pure custom thin Python wrapper with no Claude-native engine**: subsumed — the engine *is* a thin wrapper, but it delegates execution to Claude-native isolated-context agents rather than reinventing a runner.
