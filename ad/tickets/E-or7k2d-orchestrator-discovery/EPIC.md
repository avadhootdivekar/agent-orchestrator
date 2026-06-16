# EPIC: E-or7k2d-orchestrator-discovery

## Metadata
- Epic ID: `E-or7k2d-orchestrator-discovery`
- Title: `Agent Orchestrator — Discovery: requirements, market survey & viability`
- Owner: `avadhoot`
- Created: `2026-06-16`
- Last Updated: `2026-06-16`
- Status: `Done` (discovery complete; decision gate resolved → Option A)

## Summary
- Goal: Decide **build vs buy** for the agent-orchestrator before any design. Consolidate requirements, survey the market, judge business viability, and check whether we can get what we want for our own repos in < 1 week and < $40/mo.
- Scope In: Requirements consolidation · market/competitor survey · pros/cons · viability verdict · build-vs-buy options.
- Scope Out (until consent): Full design, ADR/HLD/LLD, `workflow.json` schema, task breakdown, any implementation.

## Requirements (consolidated — full text in survey)
- FR-1 multi-agent orchestration · FR-2 DAG · FR-3 artifact-by-path · FR-4 JSON/YAML config-driven · FR-5 cron + event triggers · FR-6 resume/idempotent · FR-7 multi-repo, configurable repos/agents/workflows.
- NFR-1 **orchestrator context hygiene** (paths/statuses only) · NFR-2 pluggable · NFR-3 deterministic/observable · NFR-4 safe-by-default.
- Differentiators that matter: **NFR-1** + **FR-7**. FR-1..FR-6 are commodity in 2026.

## Key findings
- The exact combo we want is **already covered** by **Kestra 1.0** (declarative YAML DAG + cron/event + AI-agent tasks, Apache-2.0/free) and **Claude Code Agent Teams / Dynamic Workflows** (context-isolated coding agents, $0 incremental).
- **Not viable as a standalone business**: space is commoditized + crowded, and the differentiated sliver is being absorbed by the model vendors (Anthropic, OpenAI, Google).
- **As an internal capability: yes, < 1 week and < $40/mo** — self-host Kestra (license-free, small VM) or use Claude-native orchestration ($0 incremental).
- Recommendation: **don't build a from-scratch product**; hybrid of Claude-native engine (+ optional Kestra for declarative specs/scheduling), with custom code limited to a thin `workflow.json` spec/adapter (the missing ~5%).

## Decision gate (RESOLVED 2026-06-16)
**Chosen: Option (1) Claude-native + thin spec.** Full epic → `E-m2k9pa-orchestrator-mvp-a`.
Option (2) Kestra + adapter recorded as a **strong contender to revisit** in [`ADR-0001`](../../../docs-md/adr/ADR-0001-orchestration-approach.md). Architecture keeps the executor backend pluggable so Option B can slot in later without a rewrite.

## Task List
- [x] `D-1` Consolidate requirements from `prompt.md` + `CLAUDE.md`.
- [x] `D-2` Market survey (Tiers 1–3) + pricing.
- [x] `D-3` Viability verdict + build-vs-buy options.
- [x] `D-4` **User decision** on direction (gate) → Option A (2026-06-16).
- [x] (post-consent) Full epic expansion handed to `E-m2k9pa-orchestrator-mvp-a` (spec schema, ADR/HLD/LLD, task breakdown).

## Risks and Dependencies
- Fast-moving landscape; vendor-native features (Agent Teams/Dynamic Workflows) may change scope of any custom build.
- Decision gate is a hard dependency: no design work proceeds until the user chooses a direction.

## Links
- Market survey / full analysis: [`output/E-or7k2d-orchestrator-discovery/market-survey.md`](../../../output/E-or7k2d-orchestrator-discovery/market-survey.md)
- Intent / ask: [`ad/prompts/prompt.md`](../../prompts/prompt.md)
- Design doc: `TODO (post-consent)`
- Sprint plan: `TODO (post-consent)`
