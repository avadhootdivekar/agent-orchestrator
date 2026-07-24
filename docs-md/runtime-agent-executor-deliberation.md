# Deliberation — should `ao` become a runtime agent?

> **Status: deliberation only. No code changed. No decision taken.**
> Question posed in [`meta/prompts/prompt.md`](../meta/prompts/prompt.md) "Current Ask":
> can this tool call LLM/agent APIs directly (Grok / DeepSeek / Gemini / OpenAI / Anthropic),
> get structured responses back, execute tool calls on their behalf, and make file edits —
> i.e. become a Claude-Code-equivalent runtime rather than a driver of one?
> Date: 2026-07-21 · Role: architect

---

## TL;DR

1. **Is it doable?** Yes, and it is smaller than it looks — because the expensive
   parts (retries, resume, budgets, cost breakers, parallel waves, transcript
   capture, monitoring) already exist above the `Executor` boundary. A usable
   development-cycle agent is **~19–26 dev-days**; a throwaway spike is **~2–3 days**.
2. **Is it worth it?** **Not as stated.** Building a general-purpose coding agent
   to compete with Claude Code / aider / Codex CLI is a losing treadmill and is
   *not* where this repo's value sits.
3. **But a narrower version is worth it, and for a reason the ask didn't name:**
   today `ao` is unusable by anyone without the `claude` binary and an Anthropic
   subscription. A direct-API executor is an **adoption and cost-governance
   unlock**, not a "build our own Claude" play.
4. **Recommendation: Option C** (§6) — a *narrow structured-output executor* first
   (~8–12 dev-days), which establishes the provider abstraction and pays for
   itself on DAG-glue tasks. Escalate to the full coding agent only if measured
   cost-arbitrage data justifies it.

---

## 1. What "runtime agent" actually means here

The agent loop itself is not the interesting part. It is roughly:

```
messages = [system_prompt, user_prompt]
for turn in range(max_turns):
    resp = provider.chat(messages, tools=TOOL_SCHEMAS)
    if not resp.tool_calls:
        return resp            # done
    for call in resp.tool_calls:
        messages.append(tool_result(call, TOOLS[call.name](**call.args)))
```

Everything that makes a coding agent *good* lives outside that loop: edit-application
reliability, context compaction, prompt caching, repo mapping, tool-result truncation,
and per-model prompt tuning. That distinction drives the whole estimate below.

### Where it plugs in

The repo is already shaped for this. `Executor` (`src/agent_orchestrator/executors/base.py`)
is a one-method ABC:

```python
class Executor(ABC):
    @abstractmethod
    def execute(self, ctx: TaskContext) -> TaskResult: ...
```

A runtime agent is **a third implementation alongside `ClaudeCliExecutor` and
`FakeExecutor`**. Nothing in `engine.py` (2149 lines) needs to know it exists.

```
                    ┌──────────────── unchanged ────────────────┐
  workflow.json ──▶ │ spec → dag → engine (retry/resume/budget/  │
  agents.json       │ breakers/monitor/parallel waves)           │
  reposet.json      └───────────────────┬───────────────────────┘
                                        │ TaskContext (paths only)
                          ┌─────────────┴─────────────┐
                          ▼             ▼             ▼
                   ClaudeCliExecutor  FakeExecutor  NativeAgentExecutor  ◀── NEW
                   (subprocess)       (tests)       │
                                                    ├── ProviderClient (LiteLLM or per-vendor)
                                                    ├── ToolRegistry (read/write/edit/grep/glob/bash)
                                                    └── AgentLoop (turns, repair, structured final)
```

---

## 2. What the codebase already gives for free

This is the single biggest input to the estimate. All of the following work
*unchanged* the moment a new executor returns a well-formed `TaskResult`:

| Capability | Where | Status |
|---|---|---|
| Retry/backoff, per-attempt capture | `engine._run_with_retries` | free |
| Resume from artifacts, run state persistence | `runstate.py`, `RunState` | free |
| Token/cost accounting fields | `TaskResult.input_tokens…cost_usd` | free — just populate |
| Token budgets + rate windows + `wait`/`stop` | `budget.py` | free |
| Circuit breakers incl. `task_cost_usd`/`run_cost_usd` | `breakers.py` | free |
| Agent monitoring + self-heal | `monitoring.py` | free |
| Parallel wave scheduling (`max_parallel`) | `engine.py`, ADR-0007 | free |
| Artifact IO + workspace path-traversal guards | `artifacts.py` | free, and **reusable for the tool sandbox** |
| Transcript/stdout/result capture conventions | `executors/claude_cli.py` | reuse the same filenames |
| Rate-limit / quota signalling into the engine | `TaskResult.provider_rate_limited` etc. | free — just map HTTP status |

**Corollary:** the new work is genuinely only *provider client + tool registry +
loop + capture*. That is why this is weeks, not quarters — for Tier 1. Tier 2 is a
different story (§4).

---

## 3. Answer to Q1 — scope and estimate

### Tier 0 — spike (2–3 dev-days)

One OpenAI-compatible provider, four tools (`read`/`write`/`bash`/`grep`), naive loop,
no compaction, no caching, no sandbox. Registered as `executor: "native"`. Purpose is
to de-risk the wiring and produce a real cost/quality datapoint on a real workflow —
**not** to be kept.

### Tier 1 — "basics working for a development cycle" (19–26 dev-days)

This is the level the ask describes: grep / build / execute / file edits / saves / git.

| Workstream | Days | Notes |
|---|---|---|
| Provider layer + secrets/config | 2–3 | See scope reducer below |
| Tool registry + 7 tools | 4–5 | `edit` and sandboxed `bash` are ~70% of this |
| Agent loop | 3–4 | Turn cap, tool-error feedback, malformed-tool-call repair, structured final output |
| Capture parity + usage/cost/429 mapping | 2–3 | Same `transcript.jsonl` / `stdout.txt` / `result.json` shapes as `claude_cli` |
| Config surface | 2 | `AgentSpec` fields, `agents.schema.json`, `validate`, CLI/env/config precedence |
| Safety (path jail, denylist, timeouts, secret scrubbing) | 2–3 | |
| Tests (record/replay fake provider, unit + integration + CLI e2e) + docs/ADR | 4–5 | Repo standard is CLI-level e2e, not API-level |
| **Total** | **19–26** | ≈ 4–5 calendar weeks solo; ~2–3 weeks agent-assisted |

**Scope reducer — do not hand-write N provider adapters.** Grok, DeepSeek, and
Gemini all expose OpenAI-compatible endpoints; Anthropic is the main shape outlier.
A unified layer (LiteLLM or equivalent) collapses "provider adapters" from ~2 weeks
to ~2 days *and* brings unified tool-call normalisation and per-call cost figures
that map straight into the existing `TaskResult.cost_usd`. Adopting one dependency
here is the difference between a 4-week and a 6-week Tier 1.

**Git needs no dedicated tool.** It falls out of a sandboxed `bash` tool. Resist a
bespoke git tool until a concrete failure demands one.

### Tier 2 — competitive with Claude Code / aider (2–4+ months, then permanent)

Context compaction, prompt caching, repo map, tool-result truncation strategy,
sub-agents, per-model-family edit formats, streaming, MCP, model-specific prompt
tuning. **This is a treadmill, not a project.** The incumbents ship weekly and
several are open-source and well-resourced. Assume this cost is ongoing and
unbounded, and price it as such in any decision to pursue it.

---

## 4. Answer to Q2 — is it worth it?

### The honest version

**"Why would anyone use this over Claude Code or aider?" is the wrong question,
because they are not competitors.** Claude Code and aider are *interactive,
single-session, single-repo* tools. This repo is a *durable, config-driven,
multi-task, multi-repo, resumable* orchestrator with cost governance. A user does
not choose between them — a `workflow.json` task *invokes* one.

If `ao` is repositioned as a runtime agent, it starts competing on the one axis
where it is guaranteed to lose (inner-loop coding quality) and stops differentiating
on the axes where it currently has no strong open-source equivalent:

- File-artifact IO contracts as the dependency mechanism (`inputs`/`outputs` as paths,
  with inferred DAG edges)
- Resume-from-artifacts across a partially-completed multi-day run
- Token *and USD* budgets with `wait`/`stop` semantics and circuit breakers
- Agent-based monitoring and bounded self-healing
- Cross-repo `reposet` scoping and cron/event triggers

The real comparison set is Airflow/Temporal/LangGraph/CrewAI/AutoGen — and against
*those*, this repo's differentiators are exactly the list above.

### So what is actually worth building, and why

Three arguments survive scrutiny. Only the first two are strong.

**(a) Vendor lock-in is a live, measured cost — strong.**
`ao` today cannot run without the `claude` binary and an Anthropic subscription.
That is an adoption ceiling: nobody else can install and use this. And the coupling
is not superficial — read `meta/learnings.md` and count the entries that are purely
about fighting one CLI's headless behaviour:

| Learning | The bug |
|---|---|
| `LRN-20260702-agent-cwd-workspace` | subprocess `cwd` inheritance silently wrote outputs to repo root |
| `LRN-20260702-bypasspermissions-for-bash` | `--permission-mode acceptEdits` silently denies Bash; agent exits 0 having skipped its output |
| `LRN-20260715-headless-claude-p-background-tools` | `run_in_background` is unobservable by construction under `-p` |
| `LRN-20260702-quota-exhaustion-vs-429` | quota detected by **regex over stdout** (`_CLAUDE_QUOTA_PATTERN`) |
| `LRN-20260702-stdin-devnull-subprocess` | CLI blocks forever waiting on stdin |
| `LRN-20260702-effort-maps-to-max-turns` | non-deterministic `error_max_turns` from a turn cap we don't own |

**Every one of these disappears with a direct API call.** cwd is a `Path` you pass.
Tool policy is a dict you own. Quota is an HTTP status code, not a regex over
English prose that breaks when the vendor rewords a message.

**(b) Heterogeneous per-task model routing — strong, and product-shaped.**
`AgentSpec` already carries `model` and `effort`, but can only address models behind
the one `claude` binary. Direct API means a single DAG can run its boilerplate emitter
on a cheap model, its reviewer on a mid-tier model, and its architecture task on a
frontier model. Combined with the budget and cost-breaker machinery **that already
exists**, this is a coherent and defensible product story:

> *cost-governed, resumable, multi-model agent DAGs* — the layer that decides
> *which* agent runs *which* task under *what* budget, not a better inner loop.

That is a real gap. No incumbent occupies it well.

**(c) Better structured-output control — moderate, and narrower than it sounds.**
The DAG already depends on machine-readable agent outputs: loop gate verdicts,
router verdicts, `emit_tasks` manifests, monitor decisions. Today these are produced
by *asking a general coding agent nicely to write valid JSON* and then reading the
file. Native tool-calling with an enforced JSON schema makes that contractual rather
than hopeful — and reliable even on cheap models. This is the least glamorous
argument and the most immediately useful one.

### Arguments against, stated fairly

- **Focus dilution.** Tier 2 is unbounded. Time spent on edit-application heuristics
  is time not spent on scheduling, triggers, or observability.
- **You inherit a new class of liability.** Today, when a model runs `rm -rf`, that is
  Claude Code's permission system's problem. With a native `bash` tool it becomes
  *this repo's* problem, in this repo's threat model, with this repo's blast radius.
- **The orchestration layer is being absorbed upward.** Vendor agent runtimes keep
  gaining sub-agents, hooks, background tasks, and SDKs. Any plan here should assume
  that trend continues and compete on *durability and governance*, which vendors are
  structurally less motivated to build, rather than on features they ship for free.
- **NFR-1 inversion.** The engine's core invariant is "paths only, never contents"
  (`TaskContext` docstring: *"NFR-1 boundary: paths/ids only — NO file contents"*).
  A native agent reads file contents and holds conversation state. That is *legal* —
  it happens strictly inside the executor — but it must be stated and enforced
  deliberately, or the invariant erodes by accident.

---

## 5. What this does **not** change

Worth stating so the scope stays honest. A native executor does not, by itself,
improve: DAG expressiveness, scheduling, trigger coverage, observability/UI, spec
ergonomics, or multi-repo handling. If the current pain is in any of those, this
work is a detour.

---

## 6. Three options

### Option A — full native runtime agent

Build the general coding agent: all tools, all providers, Tier 2 quality.

- **Cost:** 19–26 days to Tier 1, then unbounded.
- **For:** maximum control; removes lock-in entirely; owns the whole stack.
- **Against:** direct competition with far better-resourced tools on their strongest
  axis; large ongoing maintenance; new security liability.
- **Verdict:** not now. This is the destination, not the next step.

### Option B — adapters for other agent CLIs

Wrap `codex` / `gemini-cli` / `aider` / `opencode` headless modes as additional
executors.

- **Cost:** ~3–5 days per CLI.
- **For:** cheapest possible multi-provider; no loop to build or maintain.
- **Against:** multiplies the exact problem — N sets of CLI quirks, N quota-message
  regexes, N permission models, N stream formats, all drifting independently. The
  learnings table in §4(a) is the evidence for how expensive *one* of these is.
- **Verdict:** **no.** Add a *specific* second CLI only on concrete user demand,
  never as the general strategy.

### Option C — narrow structured-output executor first ✅ **recommended**

Build a native executor deliberately scoped to tasks that are **"read these inputs,
emit this schema"** — not general coding. Tools limited to `read` / `glob` / `grep`
(read-only), plus a schema-enforced final output. **No `bash`, no `edit`.**

- **Cost:** **~8–12 dev-days.** Drops the two most expensive and most dangerous
  workstreams (sandboxed bash, edit-application quality) while keeping the provider
  layer, loop, capture, config surface, and tests.
- **Fits the DAG's real shape:** router verdicts, loop gates, `emit_tasks` manifests,
  classifiers, summarisers, reviewers, monitor consults. These are a large share of
  tasks in a mature workflow, and a full coding agent is overkill for every one of them.
- **Delivers the two strong arguments immediately:** breaks the `claude`-binary
  dependency for a real class of tasks, and enables cheap-model routing where cheap
  models are perfectly adequate.
- **De-risks Option A:** the provider layer, cost mapping, capture parity, and test
  harness are exactly the substrate Option A needs. Nothing is thrown away.
- **Zero new security surface:** read-only tools inside the existing workspace path
  guard. No arbitrary shell execution added to this repo's threat model.
- **Against:** does not answer "can `ao` write code without Claude Code" — coding
  tasks still route to `claude_cli`. That is a deliberate, reversible bet.

**Escalation gate.** Extend C → A only when there is measured evidence: run the same
workflow through both executors and compare cost, wall-clock, and success rate. If
cheap-model routing on glue tasks does not show a material cost reduction, Option A's
main economic argument is unproven and should not be funded.

---

## 7. Design constraints for whichever option is chosen

Grounded in this repo's own recorded failures — these are not generic advice.

1. **Contain the NFR-1 inversion.** File contents may live inside the executor only.
   `TaskContext` and `TaskResult` stay paths-and-scalars. Assert this in review.
2. **`AgentSpec` changes require `specs/agents.schema.json` changes.** It is
   `additionalProperties: false`, so any unregistered field fails `ao validate` while
   the engine would happily run it. This has already bitten twice
   (`LRN-20260702-agents-schema-must-track-agentspec`,
   `LRN-20260715-agents-schema-drift-max-turns-working-dir-missing`). Add a test
   pinning the schema's property set against `AgentSpec.model_fields`.
3. **`executor` is a closed enum in two places** — `models.py:64` and
   `specs/agents.schema.json:23`. Both must change together.
4. **Generalise `claude_quota_exhausted`.** It is `TaskResult`-only (never persisted
   in `RunState`), so renaming to `quota_exhausted` is a contained ~6-site change in
   `src/` plus tests — do it *with* this work, not after, while it is still cheap.
5. **Reuse the workspace path guard** already used for `AgentSpec.working_dir`; do not
   write a second path-jail implementation.
6. **Capture parity is non-negotiable.** Emit the same `transcript.jsonl` /
   `stdout.txt` / `stderr.txt` / `result.json` under `ctx.output_dir`, so budgets,
   breakers, monitoring, and every existing debugging habit keep working untouched.
7. **Never bundle content via repo-relative paths.** `[tool.hatch.build.targets.wheel]`
   ships only `src/agent_orchestrator`; anything a runtime needs (system prompts, tool
   descriptions) must be a Python string constant in the package
   (`LRN-20260715-wheel-packaging-excludes-specs-bake-templates`).
8. **Determinism in tests.** A record/replay fake provider reading HTTP fixtures,
   temperature 0, fixed clocks — matching the existing `FakeExecutor` conventions and
   the project's fixed-seed testing rule. Note `LRN-20260715-dispatchexecutor-fake-uncontrollable-via-cli`:
   make the new executor *configurable through the CLI path* from day one, so its
   failure modes are e2e-testable without engine-API construction.
9. **Secrets via env/config only.** N providers means N keys. Never in spec files;
   scrub them from transcripts before write.

---

## 8. Open questions — need answers before any build

1. **What is the actual driving pain?** Cost, lock-in, structured-output
   unreliability, or the inability to hand `ao` to someone without a Claude
   subscription? Each points at a different first increment, and (a)/(b)/(c) in §4
   are not equally urgent.
2. **Is there a real second user?** If `ao` remains single-operator with a Claude
   subscription, argument (a) collapses to an aesthetic preference and Option C's
   priority drops sharply.
3. **Do we have a cost baseline?** `RunUsageTotals` / `cumulative_cost_usd` are
   already recorded. **Pull the numbers from recent runs before deciding** — if glue
   tasks are not a meaningful share of spend, the cost-arbitrage argument is dead on
   arrival and this whole document resolves to "don't".
4. **Is a `bash`-executing native agent acceptable in this threat model?** This gates
   A vs C more than any engineering consideration. Worth a `dev-security` pass.

---

## 9. Recommendation

**Do Option C, gated on Q3.** Pull the cost data first; it is already in the run
states and costs nothing to check. If glue-task spend is material:

- **Increment 1** (~8–12 days): `NativeAgentExecutor`, read-only tools, schema-enforced
  structured output, unified provider layer, full capture parity, CLI-configurable,
  record/replay tests. Ship with an ADR (next free number: **ADR-0008**).
- **Increment 2** (gated on measured results): evaluate write tools + sandboxed bash
  — i.e. Option A Tier 1 — with a `dev-security` review as an explicit entry gate.
- **Never**: position `ao` as a Claude Code / aider competitor. The pitch is
  *"orchestrate and govern whichever agents you already use"*, and every design
  decision should be tested against that sentence.

---

## Appendix — checklist (per `meta/prompts/prompt.md` §7)

| # | Item | Status |
|---|---|---|
| 1 | Given tasks all complete | ✅ Deliberation delivered; explicitly plan-only, no code changed |
| 2 | Build and unit tests pass | **NA** — no source changed (one new markdown doc) |
| 3 | Regression vs. quantitative baseline | **NA** — no source changed |
| 4 | Test coverage maintained/increased | **NA** — no source changed |
| 5.1 | Design doc updated | ✅ This document |
| 5.2 | ADR updated | **Deferred by design** — ADR-0008 is written *if* Option C is approved; recording a decision now would pre-empt the open questions in §8 |
| 5.3 | Examples/playground updated | **NA** — no behaviour changed |
