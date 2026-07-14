# Agent Orchestrator — Discovery: Requirements, Market Survey & Viability

> Epic: `E-or7k2d-orchestrator-discovery`
> Author: Claude (architect role) · Date: 2026-06-16
> Status: **Discovery only.** No design/LLD/task-breakup performed yet (per ask, awaiting consent).

This document answers the four discovery questions in [`meta/prompts/prompt.md`](../../meta/prompts/prompt.md):
1. Consolidated requirements.
2. Market survey — alternative tools/frameworks.
3. Does our tool add value vs competitors? Is it a viable business?
4. If not a viable *business*, can we still get the orchestration we want for our own repos in **< 1 week**, using off-the-shelf tools, preferably **< $40/month**?

---

## 1. Consolidated requirements

Distilled from `prompt.md` + `CLAUDE.md`. These describe **what we actually want**, independent of whether we build or buy.

### Functional
- **FR-1 — Multi-agent workflow orchestration.** Drive multi-step, multi-agent work to *done*.
- **FR-2 — DAG dependency model.** Tasks form a directed acyclic graph; cycles rejected; explicit requirements per node.
- **FR-3 — Artifact IO by path.** Inputs/outputs are files/directories referenced **by path**, never inlined into the engine.
- **FR-4 — Config-driven specs.** All orchestration metadata (deps, IO contracts, schedules, retries) lives in **structured JSON/YAML**, validated against a schema. Payloads (md, source, dirs) are referenced, not embedded.
- **FR-5 — Periodic + event triggers.** Cron-style schedules and event triggers are first-class.
- **FR-6 — Resume / idempotency.** A run resumes from completed artifacts; tasks are safe to retry.
- **FR-7 — Multi-repo, configurable.** Repos, workflows (graphs), and agents are all configurable; one orchestrator instance can drive *multiple distinct repo sets* with per-set multi-repo context.

### Non-functional (the differentiating ones in bold)
- **NFR-1 — Orchestrator context hygiene (KEY).** The orchestrator's own context must **never** get polluted by payload content — it should know only file names/paths and task statuses.
- **NFR-2 — Pluggable.** Agents, executors, schedulers, artifact backends behind clean interfaces (ABC/protocol + injection).
- **NFR-3 — Deterministic / observable.** Reproducible from spec + artifacts; run/task state, logs, artifacts traceable end-to-end.
- **NFR-4 — Safe by default.** Scoped, resource-bounded, cancellable task execution.

**Reading of intent:** This is not a generic data-pipeline orchestrator. It is a **spec-driven meta-orchestrator for *coding/LLM agents* across multiple repos**, whose distinguishing constraint is **context-window hygiene** (NFR-1) and **multi-repo configurability** (FR-7). FR-1..FR-6 are, by 2026, commodity features of existing orchestrators.

---

## 2. Market survey

The relevant landscape falls into **three tiers**. Our requirements straddle Tier 1 (the declarative DAG/spec engine) and Tier 3 (it's *agents*, not processes, being orchestrated).

### Tier 1 — General workflow / DAG orchestrators (config- or code-driven)
| Tool | Spec model | Cron/event | Artifact IO | Notes vs our needs |
|------|-----------|-----------|-------------|--------------------|
| **Kestra 1.0** | **YAML, declarative** | ✅ both | ✅ | **Closest match.** Rebranded 1.0 as a "Declarative *Agentic* Orchestration Platform" — adds AI-Agent tasks (LLM + memory + tools + flow-calling). Apache-2.0, self-host free. Namespaces ≈ multi-repo sets. |
| Apache Airflow | Python DAGs | ✅ | ✅ | Code-first, not declarative-spec-first. Heavy. |
| Prefect | Python | ✅ | ✅ | Code-first. Cloud Pro ~$500/mo. |
| Dagster | Python (assets) | ✅ | ✅ (asset-centric) | Strong artifact/lineage model. Cloud ~$100/mo. |
| Temporal | Code (durable exec) | ✅ | via code | Durable execution, not declarative specs. Cloud ~$200/mo. |
| Argo Workflows | **YAML** on k8s | ✅ | ✅ | Declarative YAML DAG, but k8s-bound, heavyweight. |
| Windmill | Scripts → flows | ✅ | ✅ | Code-first DX, auto-UIs. Cloud from ~$10/mo, self-host free. |
| n8n | Node graph (low-code) | ✅ | partial | Automation-flavored. Cloud from ~$20/mo, community self-host free. |

### Tier 2 — In-process AI-agent frameworks (code-first)
| Framework | Orchestration model | Fit |
|-----------|--------------------|-----|
| **LangGraph** | Graph: nodes/edges, checkpoints | Most control; graph maps to our DAG, but Python-in-process, not file-spec-driven. Passed CrewAI in stars early 2026. |
| CrewAI | Role-based crews | Easiest, least control; ~18% token overhead. |
| AutoGen / AG2 | Agent conversations | Conversational multi-agent. |
| LlamaIndex Workflows / Semantic Kernel / OpenAI Agents SDK / Google ADK / Pydantic AI | Various | All orchestrate LLM calls **inside a process** — none are config-driven file-spec DAGs across repos, and none isolate orchestrator context the way NFR-1 wants. |

### Tier 3 — Coding-agent orchestrators (drive Claude Code / CLI coding agents) — **our actual niche**
| Tool | What it does | Fit to our intent |
|------|--------------|-------------------|
| **Claude Code — Agent Teams** | Built-in (GA Feb 5 2026, Opus 4.6). Team-lead session coordinates 2–16 teammates via a **shared task list**; each teammate runs in **its own context window**. | Directly satisfies FR-1 + **NFR-1** (separate context windows) natively. |
| **Claude Code — Dynamic Workflows** | Research preview, May 28 2026. "A generated JavaScript script **IS** the orchestrator" for jobs too big for one pass. | Programmable orchestration layer; complements specs. |
| **Claude Code — `/batch` + subagents** | Splits NL instruction across parallel agents, each own context, results merged. | Parallel fan-out, multi-repo per-agent context. |
| Conductor, Vibe Kanban, Gastown, Claude Squad, Antigravity | Spawn agents in **isolated git worktrees** with dashboards/diff-review/merge control. | Multi-repo (FR-7) + isolation; UI-driven, 3–10 agents. |
| ruflo (ex-claude-flow), Multiclaude, Oh-My-ClaudeCode | Community multi-agent orchestrators. | Overlapping community offerings. |

**Takeaway:** The exact combination we wrote down — *declarative JSON/YAML DAG + cron/event + artifact-by-path + context-isolated agents + multi-repo* — is **already covered**, split across **Kestra (Tier 1, declarative + agentic)** and **Claude Code Agent Teams / Dynamic Workflows (Tier 3, context-isolated coding agents)**. Both are available today; neither costs us a new license.

---

## 3. Pricing landscape (the "< $40/month" question)

| Option | Self-host | Managed cloud entry |
|--------|-----------|---------------------|
| **Kestra** (Apache-2.0) | **Free** (your infra) | paid tiers exist; OSS is enough |
| Windmill | Free | ~$10/mo |
| n8n | Free (community) | ~$20/mo |
| Airflow / Prefect core / Dagster OSS / Temporal OSS | Free | Dagster ~$100, Temporal ~$200, Prefect Pro ~$500 |
| **Claude Code native** (Agent Teams / Dynamic Workflows / subagents) | n/a | **$0 incremental** — included in existing Claude plan; you pay only token/usage you already pay |

**Answer:** Yes — comfortably under $40/mo. Self-hosting Kestra/Windmill/n8n is a license-free small-VM cost; Windmill/n8n cloud entry tiers are $10–20/mo; Claude Code's native orchestration adds **no new subscription** at all. The expensive managed orchestrators (Dagster/Temporal/Prefect cloud) are irrelevant to this budget and unnecessary for our use case.

---

## 4. Pros / cons of building our own

**Pros of building**
- Exact fit to NFR-1 (context hygiene) and FR-7 (our specific multi-repo set model) without bending a third-party tool.
- No dependency on a vendor's roadmap; full control of the spec schema.
- Educational / IP ownership if that's a goal.

**Cons of building**
- **Reinvents commodity.** FR-1..FR-6 are solved table-stakes in Kestra/Argo/Dagster/Temporal — scheduler, retries, DAG resolution, artifact IO, UI, observability are months of hardened work to match.
- **Racing the platform owner.** The differentiated sliver (context-isolated coding-agent orchestration) is exactly where **Anthropic itself** is now shipping native features (Agent Teams Feb 2026, Dynamic Workflows May 2026). A standalone tool here competes with the platform it depends on.
- **Crowded + fast-moving.** 2024–2026 saw an explosion of agent orchestrators (Tier 2 & 3); differentiation decays weekly.
- **Maintenance tail.** Scheduling, resume, concurrency safety, sandboxing (NFR-4) are ongoing burdens, not one-time builds.

---

## 5. Business-viability verdict

**Not viable as a standalone commercial product / business.** Reasons:
- The "config-driven DAG of agents" is **commoditized** — strong free OSS (Kestra, Windmill, n8n, Airflow) plus well-funded incumbents (Temporal, Prefect, Dagster/Astronomer).
- The one defensible sliver (spec-driven, context-clean, multi-repo coding-agent orchestration) is being **absorbed by the model vendors** (Anthropic Agent Teams/Dynamic Workflows, OpenAI Agents SDK, Google ADK). Building a business on the gap the platform is actively closing is a losing position.
- No obvious moat: no proprietary data, network effect, or switching cost we can hold that Kestra-OSS-plus-Claude-native doesn't already neutralize.

**However**, as an **internal capability for our own repos**, the *goal* is entirely worthwhile and cheap to achieve (Section 6).

---

## 6. Build-vs-buy — getting what we want in < 1 week, < $40/mo

Yes. Ranked options:

**Option A (recommended) — Claude Code native, thin spec adapter.**
Use **Agent Teams** (context-isolated teammates + shared task list = FR-1 + NFR-1) and/or **Dynamic Workflows** (programmable orchestrator) as the engine. Add a *thin* `workflow.json`/`workflow.yaml` per repo-set that the lead reads to know DAG edges + artifact paths + per-repo context. Cron/events come from the harness's existing scheduling primitives. **Cost: $0 incremental.** **Effort: days.** Best NFR-1 fit because context isolation is native.

**Option B — Self-hosted Kestra + coding-agent CLI tasks.**
Kestra gives declarative YAML DAGs, cron + event triggers, retries, namespaces (≈ repo-sets), and an AI-Agent task type out of the box (FR-2..FR-6, FR-7). Each node shells out to `claude -p`/coding-agent CLI; pass only paths to preserve NFR-1. **Cost: ~$0 license + small VM (< $40/mo).** **Effort: ~1 week.** Closest to the repo's stated *declarative-spec* design without writing an engine.

**Option C — Windmill / n8n self-host or cheap cloud.** Script tasks invoking coding-agent CLIs; good dashboards/scheduling. **$0–20/mo.** Weaker DAG/artifact semantics than Kestra; fine if UI/cron is the priority.

**Option D — Thin custom Python wrapper.** ~Few-hundred-line package: load `workflow.json`, topo-sort, shell out per node passing only paths (NFR-1 by construction). **< 1 week**, but duplicates what Kestra gives free *minus* scheduler/UI/retries/resume. Only justified if we need a spec schema no tool expresses, or want zero external deps.

### Recommendation
**Do not build a from-scratch general orchestrator as a product.** To actually orchestrate agents over our repos, adopt a **hybrid**: **Claude Code native Agent Teams/Dynamic Workflows (Option A)** as the execution engine, optionally fronted by **Kestra (Option B)** when we need declarative YAML specs, cron/event triggers, and a UI/scheduler we don't want to own. Reserve any **custom code to a thin spec/adapter layer** (the `workflow.json` schema + a small reader that keeps orchestrator context clean) — i.e. the genuinely missing 5%, not the commodity 95%.

---

## 7. Decision gate (awaiting user consent)

Per the ask, **no full design / LLD / task breakdown has been done.** Choose a direction before proceeding:
- **(1)** Adopt Option A (Claude-native + thin spec) — minimal build.
- **(2)** Adopt Option B (Kestra + adapter) — declarative buy-mostly.
- **(3)** Proceed with a full from-scratch build anyway (Option D scaled up) — accept the "not a business, internal tool" framing and design the engine.
- **(4)** Something else / combination.

Only after this choice will the epic expand into requirements → spec schema (`workflow.json`) → ADR/HLD/LLD → full task breakdown.

---

## Sources
- [10 AI Agent Frameworks 2026 (Medium)](https://medium.com/@atnoforgenai/10-ai-agent-frameworks-you-should-know-in-2026-langgraph-crewai-autogen-more-2e0be4055556)
- [Top 6 AI Agent Frameworks 2026 (Turing)](https://www.turing.com/resources/ai-agent-frameworks)
- [Kestra 1.0 — Declarative Agentic Orchestration](https://kestra.io/1-0) · [Kestra 1.0 release notes](https://kestra.io/blogs/release-1-0) · [Kestra GitHub](https://github.com/kestra-io/kestra)
- [Kestra vs n8n](https://openalternative.co/compare/kestra/vs/n8n) · [Kestra vs Windmill](https://openalternative.co/compare/kestra/vs/windmill)
- [Prefect Pricing 2026](https://automationatlas.io/answers/prefect-pricing-explained-2026/) · [Workflow Orchestration Landscape Mar 2026](http://npow.github.io/posts/workflow-orchestration-market-quadrant-2026/)
- [The Code Agent Orchestra (Addy Osmani)](https://addyosmani.com/blog/code-agent-orchestra/) · [Shipyard — Multi-agent orchestration for Claude Code](https://shipyard.build/blog/claude-code-multi-agent/)
- [Claude Code Agents in 2026 (CloudZero)](https://www.cloudzero.com/blog/claude-code-agents/) · [Dynamic Workflows for Parallel Agent Coordination (InfoQ)](https://www.infoq.com/news/2026/06/dynamic-workflows-claude-code/)
