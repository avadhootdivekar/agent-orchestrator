# EPIC: E-m2k9pa-orchestrator-mvp-a

## Metadata
- Epic ID: `E-m2k9pa-orchestrator-mvp-a`
- Title: `Agent Orchestrator MVP (Option A: Claude-native engine + thin spec)`
- Owner: `avadhoot`
- Created: `2026-06-16`
- Last Updated: `2026-06-16`
- Status: `Done` (implementation complete; all 73 tests pass)

## Summary
- Goal: Ship a small, declarative, config-driven engine that drives multi-agent, multi-repo workflows to completion from a `workflow.json` DAG, delegating execution to **Claude-native, context-isolated agents**, while the orchestrator knows only **paths + statuses** (NFR-1).
- Scope In (MVP): spec schema + loader/validation, multi-repo RepoSet + agent registry config, DAG + cycle detection + topo order, `ClaudeCliExecutor` (+ `FakeExecutor`), artifact-path resolution + existence checks, run-state persistence + resume/idempotency, retries + timeout/cancellation, manual + cron triggers, CLI (`validate/run/resume/status`), unit + integration tests, CI gates.
- Scope Out (non-MVP): Kestra executor (ADR Option B), full event/webhook system, web UI/dashboard, durable/distributed/parallel execution, non-local artifact backends (S3), coverage/security gates beyond baseline.
- Decision basis: [`ADR-0001`](../../../docs-md/adr/ADR-0001-orchestration-approach.md) · design: [`HLD`](../../../docs-md/hld-agent-orchestrator.md) · [`LLD`](../../../docs-md/lld-agent-orchestrator.md).

## Requirements (traceability)
| ID | Requirement | MVP | Tasks |
|----|-------------|-----|-------|
| FR-1 | Multi-agent workflow orchestration to done | ✅ | T-h7k3qm, T-p6m4qz |
| FR-2 | DAG dependency model; reject cycles | ✅ | T-9xc2bk |
| FR-3 | Artifact IO by path (never inlined) | ✅ | T-d3v7hn, T-k29mvp |
| FR-4 | Config-driven JSON/YAML specs, schema-validated | ✅ | T-k29mvp, T-r4t8wd |
| FR-5 | Cron + event triggers first-class | ✅ cron / ⚪ event-stub | T-c5y9tp |
| FR-6 | Resume / idempotent / deterministic | ✅ | T-w8s5lf |
| FR-7 | Multi-repo, configurable repos/workflows/agents | ✅ | T-k29mvp, T-r4t8wd |
| FR-8 | Configurable agent registry | ✅ | T-k29mvp, T-p6m4qz |
| FR-9 | CLI: validate/run/resume/status | ✅ | T-e4u8zx |
| NFR-1 | **Orchestrator context hygiene (paths/statuses only)** | ✅ | T-p6m4qz, T-h7k3qm, T-f9j2vd |
| NFR-2 | Pluggable (executor/store/scheduler behind ABCs) | ✅ | T-p6m4qz, T-d3v7hn, T-c5y9tp |
| NFR-3 | Deterministic / observable (logs + run-state trace) | ✅ | T-w8s5lf, T-h7k3qm |
| NFR-4 | Safe-by-default (timeout, bounded retries, path guard, cancellable) | ✅ | T-b2n6rk, T-d3v7hn |

## Task List (each ≤ 3 days, mapped to LLD sections)
- [x] `T-7gq3ax-project-scaffolding` — pyproject + ruff/mypy/pytest + package skeleton + CI skeleton.
- [x] `T-k29mvp-spec-schemas` — workflow/reposet/agents JSON Schemas + examples (done as design; task = lock + schema-validate in CI).
- [x] `T-r4t8wd-spec-loader-validation` — load JSON/YAML, jsonschema validate, pydantic models, cross-validation.
- [x] `T-9xc2bk-dag-cycle-topo` — DAG build, inferred edges, Kahn cycle detection, deterministic topo order, input validation.
- [x] `T-p6m4qz-executor-claude-native` — Executor ABC + ClaudeCliExecutor + FakeExecutor + context-hygiene contract.
- [x] `T-d3v7hn-artifact-store` — ArtifactStore ABC + LocalFsArtifactStore (resolve/exists + traversal guard).
- [x] `T-w8s5lf-runstate-resume` — RunState model + atomic persistence + resume/idempotency.
- [x] `T-b2n6rk-retry-timeout-safety` — retry policy + timeout/cancellation + injectable sleeper.
- [x] `T-c5y9tp-scheduler-triggers` — Scheduler ABC + Manual + Cron (injectable clock) + event stub.
- [x] `T-h7k3qm-engine-orchestration` — Orchestrator engine composing all; run()/resume(); NFR-1 invariant.
- [x] `T-e4u8zx-cli` — typer CLI validate/run/resume/status.
- [x] `T-f9j2vd-integration-ci-docs` — E2E sample workflow, resume/cycle/malformed tests, CI gates, docs sync.

## Risks and Dependencies
- Fast-moving Claude-native surface (Agent Teams / Dynamic Workflows) — keep executor pluggable (mitigated by NFR-2).
- `claude` CLI invocation shape may evolve — isolate in `ClaudeCliExecutor`, keep templates in `agents.json`.
- Dependency order: T-7gq3ax → T-r4t8wd/T-k29mvp → (T-9xc2bk, T-p6m4qz, T-d3v7hn, T-w8s5lf, T-b2n6rk, T-c5y9tp) → T-h7k3qm → T-e4u8zx → T-f9j2vd.

## Links
- ADR: [`ADR-0001`](../../../docs-md/adr/ADR-0001-orchestration-approach.md) · HLD: [`hld`](../../../docs-md/hld-agent-orchestrator.md) · LLD: [`lld`](../../../docs-md/lld-agent-orchestrator.md)
- Specs: [`specs/`](../../../specs/) · Survey: [`market-survey.md`](../../../output/E-or7k2d-orchestrator-discovery/market-survey.md)
- Discovery epic: [`E-or7k2d-orchestrator-discovery`](../E-or7k2d-orchestrator-discovery/EPIC.md)
