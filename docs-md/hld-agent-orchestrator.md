# HLD — Agent Orchestrator (Option A: Claude-native + thin spec)

- Epic: [`E-m2k9pa-orchestrator-mvp-a`](../meta/tickets/E-m2k9pa-orchestrator-mvp-a/EPIC.md)
- Decision: [`ADR-0001`](adr/ADR-0001-orchestration-approach.md)
- LLD (implementable detail): [`lld-agent-orchestrator.md`](lld-agent-orchestrator.md)
- Date: 2026-06-16
- Feature design docs: [`logging-dynamic-workflows-hld.md`](logging-dynamic-workflows-hld.md), [`token-budgeting-hld.md`](token-budgeting-hld.md), [`multi-endpoint-circuit-breaker-hld.md`](multi-endpoint-circuit-breaker-hld.md) *(draft)*, [`granular-task-decomposition-hld.md`](granular-task-decomposition-hld.md) *(draft)*, [`lld-agent-monitoring-self-healing.md`](lld-agent-monitoring-self-healing.md) *(agent-based monitoring & self-healing, guardrail modes)*, [`parallel-execution-hld.md`](parallel-execution-hld.md) *(opt-in parallel task execution — bounded thread pool, serialized core; [ADR-0007](adr/ADR-0007-parallel-task-execution.md))*, **Benchmarking framework** — [`benchmarking-framework-hld.md`](benchmarking-framework-hld.md) / [`benchmark-landscape-survey.md`](benchmark-landscape-survey.md) / [ADR-0008](adr/ADR-0008-benchmark-harness-approach.md) *(standalone `ao-bench` harness comparing an `ao` workflow vs bare `claude -p`, outside the engine import graph; delivered)*
- Settings/precedence policy: [`adr/ADR-0003-settings-precedence-policy.md`](adr/ADR-0003-settings-precedence-policy.md) *(accepted)*

## 1. Goal
A small, declarative, config-driven engine that drives multi-agent, multi-repo workflows to completion from a `workflow.json` DAG spec, while keeping the **orchestrator's own context limited to paths and task statuses** (NFR-1). Execution is delegated to **Claude-native, context-isolated agents**.

## 2. Component overview

```
                         ┌─────────────────────────────────────────────┐
   workflow.json         │            agent_orchestrator (core)         │
   reposet.json   ─────► │  ┌──────────┐   ┌──────────┐   ┌──────────┐  │
   agents.json           │  │  Spec    │   │   DAG    │   │  Engine  │  │
                         │  │ Loader + │──►│  Builder │──►│ (runner) │  │
                         │  │ Validate │   │ +cycle   │   │          │  │
                         │  └──────────┘   └──────────┘   └────┬─────┘  │
                         │                                     │        │
                         │   ┌──────────────┐   ┌──────────────▼─────┐  │
                         │   │ RunStateStore│◄──│  dispatch (paths   │  │
                         │   │ (resume)     │   │  + ids ONLY)       │  │
                         │   └──────────────┘   └─────┬────────┬─────┘  │
                         │   ┌──────────────┐         │        │        │
                         │   │ ArtifactStore│◄────────┘        │        │
                         │   │ (exists/...) │                  │        │
                         │   └──────────────┘    ┌─────────────▼─────┐  │
                         │   ┌──────────────┐    │   Executor (ABC)  │  │
                         │   │  Scheduler   │    └─────────┬─────────┘  │
                         │   │ (cron/event) │              │            │
                         │   └──────────────┘              │            │
                         └─────────────────────────────────┼────────────┘
                                                            ▼
                                    ┌───────────────────────────────────────┐
                                    │ ClaudeCliExecutor → `claude -p` /       │
                                    │ subagent / Agent Teams (own context     │
                                    │ window per task — reads payloads here)  │
                                    └───────────────────────────────────────┘
```

## 3. Core domain concepts
| Concept | Meaning |
|--------|---------|
| **RepoSet** | Named binding of one orchestrator instance to **multiple repos** (id, path, role) + a workspace root. Gives FR-7 multi-repo context. One config can hold many sets. |
| **Workflow** | A `workflow.json`/`.yaml` DAG spec referencing a RepoSet: tasks, dependencies, triggers, retry/timeout defaults. |
| **Task (node)** | `id`, `agent` (ref into agent registry), `instruction` (path to a prompt/spec file — **never inlined**), `inputs`/`outputs` (artifact paths), `depends_on`, retry/timeout overrides. |
| **Artifact** | A file/dir at a known path. Edges may be declared (`depends_on`) and/or validated against input/output overlap. |
| **Agent** | Configurable executor reference in `agents.json` (executor type + command/prompt templates + context-window mode). Agents are config (FR-8). |
| **Executor** | Pluggable backend that runs a task via an agent. MVP: `ClaudeCliExecutor`, `FakeExecutor`. Future: `KestraExecutor` (ADR Option B). |
| **Run / RunState** | One execution of a workflow; per-task status persisted → resume/idempotency (FR-6). |
| **Trigger** | `manual` \| `cron` \| `event`. First-class (FR-5). |

## 4. Context-hygiene mechanism (NFR-1) — central design
- The orchestrator core **never reads artifact or instruction file contents**. It has no such method.
- On dispatch, it builds a `TaskContext` containing **only**: agent ref, input paths, output paths, instruction path, repo-set paths, run/task ids.
- The `Executor` interpolates **only paths/ids** into the agent command (e.g. `claude -p "Follow {instruction}. Inputs:{inputs}. Write outputs to:{outputs}. Repos:{repos}"`). The **agent**, in its **own context window**, reads the files.
- Payload contents therefore never enter orchestrator memory. This is enforced by construction (no content-reading API) and asserted in tests (T5/T12).

## 5. Execution flow (happy path)
1. `validate`: load config + spec, JSON-Schema validate, build DAG, detect cycles, check inputs are declared/resolvable.
2. `run`: topo-sort; for each task in dependency order:
   - if resuming and task succeeded and all outputs exist → **skip** (idempotent);
   - assert all inputs exist (else **fail: missing input**);
   - execute via `Executor` with retries/backoff + timeout (safe-by-default, cancellable);
   - verify declared outputs now exist → success, else fail;
   - persist run-state after each task.
3. Finalize run status; `status`/`resume` operate on persisted state.

Default: serial, one task at a time in topo order (`max_parallel=1`). Opt-in parallel dispatch of
up to `max_parallel` independent *ready* tasks shipped via the wave/barrier scheduler — see
[`parallel-execution-hld.md`](parallel-execution-hld.md) / [ADR-0007](adr/ADR-0007-parallel-task-execution.md).

## 6. Configurability (FR-7, FR-8)
- **Repos**: `reposet.json` defines N named sets, each with M repos → orchestrator works with multiple different repo sets.
- **Workflows**: any number of `workflow.json` files, each referencing a set.
- **Agents**: `agents.json` registry; agents are swappable by id without touching the engine.

## 7. Observability & safety
- **Observability (NFR-3)**: structured logs + the persisted run-state file are the end-to-end trace (which task, status, attempts, timings, declared outputs present).
- **Safe-by-default (NFR-4)**: per-task `timeout_seconds`, bounded retries, cancellation; executor runs are subprocess-isolated.

## 8. Layout (created as tasks land — not scaffolded empty)
```
src/agent_orchestrator/    # engine, models, executors, cli
specs/                     # *.schema.json + example workflow.json / reposet.json / agents.json
docs-md/                   # this HLD, LLD, ADRs
tests/                     # unit + integration
```

## 9. Out of scope (MVP) → non-MVP / ADR Option B
Rich web UI, durable distributed execution, non-local artifact backends (S3), full event-trigger/webhook system, Kestra executor. See epic non-MVP list + ADR-0001 revisit triggers.
