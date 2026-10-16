# Feature request: composable / dynamic agent definitions + model governance for `ao`

Status: DRAFT (2026-10-16). Target: agent-orchestrator (`ao` 0.1.0, 6363047). Reporter: FinPlan operator.
Evidence below was read from `ao` source (`models.py`, `config.py`) and this workspace's `specs/agents.json`
and overseer contract; items marked **(unverified)** were not run.

## 1. Problem

### 1.1 Agents are one monolithic file
`load_agents(path)` reads a single `agents.json` (`config.py:50`), validated against `agents.schema.json`.
A project cannot *add* agents without editing (and carrying a fork of) the whole file. In this workspace
12 of the entries are near-identical `command_template` copies that differ only in `--model`,
`--max-turns`, `--autocompact`, env ceilings. There is no inheritance, no includes, no per-run overlay.

### 1.2 Agents cannot be created for a run
Overseer runs emit work units dynamically (`emit_tasks`), but a unit can only *name* an agent that already
exists in `agents.json` (OV-R9: units may not carry `model`/`max_turns`/`post_hook`). If a run needs a
cheap, read-only reviewer (e.g. 40+ Haiku persona reviewers, see `fin_plan/review-pool/`), a human must
pre-edit `agents.json` and the contract's `kind_map` before the run starts.

### 1.3 Model selection is implicit and easy to get wrong
- Precedence is `task.model > workflow.defaults.model > agent.model` (`models.py:425`), then `--model X`
  is handed to the CLI. Good — but agents like `tester`, `reviewer`, `developer`, `manager` in our
  `agents.json` set **no** model at all, so they run whatever the Claude CLI default is.
- The sibling `.claude/agents/*.md` files declare `model: sonnet|opus`, which *looks* authoritative
  but is not what `ao` passes. Two sources of truth that disagree silently.
- `ao` validates only the *syntax* of a model id (`MODEL_ID_PATTERN`); "whether the model actually
  EXISTS is the CLI's call". There is no guarantee, assertion or alarm that a task ran on the model
  the author intended, and no policy to forbid a fallback to a more expensive model. **(unverified:
  CLI behaviour on an unknown/unavailable model id and on overload fallback.)**

### 1.4 No per-agent tool/sandbox contract
Every agent runs `--dangerously-skip-permissions`. A "read-only reviewer" is a convention in a prompt,
not an enforced property. Code-blind reviewers (no tools, empty cwd) cannot be expressed as an agent spec
without hand-writing a long argv.

## 2. Is it meaningful? — Yes, in this order of value

| # | Capability | Value | Cost |
|---|---|---|---|
| A | Agent **composition** (includes / extends / overlay files) | High, low risk, pure config | Small |
| B | **Model assertion + budget guard** (`require_model`, post-run verification) | High — closes the "did it really run Haiku?" gap | Small–Medium |
| C | **Agent templates / fan-out** (one spec × N personas) | High for review pools | Medium |
| D | **Run-scoped dynamic agents** (declared in the plan, validated by a gate) | Medium; powerful but needs guard rails | Medium–High |
| E | **Plugin packages** (distributable bundles of agents + instructions + hooks) | Medium; do after A–C prove out | Larger |

Recommendation: ship A+B first (additive, backwards compatible), then C, then D behind an explicit
opt-in. Treat E as a packaging layer over A.

## 3. Proposal

### A. Composable agent definitions
- `agents` may be a list: `ao run --agents base.json --agents project.json --agents run-overlay.json`
  (later wins per key; deep-merge on `AgentSpec` fields; list fields replaced unless `"$append": true`).
- Per-agent `"extends": "<agent-name>"` with field-level override. Collapses our 12 copies to one base +
  small deltas.
- Directory form: `agents.d/*.json` (lexical order), like `conf.d`.
- `ao validate --explain-agent <name>` prints the fully-resolved spec and which file each field came from.
- Config schema version bump with a clear migration note; single-file form keeps working.

### B. Model governance
- `AgentSpec.model` and `task.model` accept an `"policy"` block:
  `{"model": "claude-haiku-5-5", "require_exact": true, "allow_fallback": false}`.
- **Post-run assertion:** read the model actually used from the CLI's JSON/usage output (the same data
  `ao report-timing` / `usage.py` already group by) and compare to the resolved model. Mismatch →
  task marked `failed` (or `warn`, configurable) with `model_mismatch: wanted X got Y`.
- Pass `--fallback-model` only when the spec explicitly sets it; otherwise ensure none is applied.
- `ao validate` flags: agent with no pinned model while `workflow.defaults.model` is unset
  ("runs on CLI default"), and conflicts between an agent's `model` and its `command_template` `--model`.
- Run summary table: planned vs actual model per task, plus cost by model (extends existing usage grouping).
- Optional lint that warns when a repo's `.claude/agents/*.md` `model:` differs from the `ao` agent of the
  same name (the two-sources-of-truth trap).

### C. Agent templates / fan-out
- `"templates"` section: parameterised agent spec (`{persona}`, `{class}`) instantiated from a manifest
  or glob (`review-pool/personas/**/*.persona.md`) → N concrete agents or N tasks of one agent with
  per-task variables substituted into `prompt_template`/`inputs`.
- Built-in concurrency group per template (`max_parallel: 4`), so a 49-persona pool cannot stampede
  regardless of how many units are emitted.

### D. Run-scoped dynamic agents (opt-in)
- Overseer/`emit_tasks` manifests may include an `"agents"` block of *additions only* (never edits an
  existing agent), validated against an allowlist policy in the run config:
  `allowed_models`, `allowed_tools`, `max_turns_cap`, `forbid_flags` (e.g. no `--dangerously-skip-permissions`
  unless the base agent already had it), command-template must extend an existing template.
- Violations fail the checkpoint (same mechanism as the OV-R* hard rules), not the whole run.
- Dynamic agents are written to the run directory and appear in the audit trail / UI.
- Security note: today's `validate_model_id` exists specifically so an agent-authored manifest cannot
  smuggle argv; D must keep that property (no free-form `command_template` from a manifest).

### E. Plugin packages (later)
- A plugin = directory with `plugin.json` (name, version, `agents`, `instructions`, `hooks`, `kind_map`
  fragments, required `ao` version) installable via `ao plugin add <path|git-url>` into a project or user
  scope; resolved through the same overlay mechanism as A. Pin by version/hash; `ao plugin list/verify`.
- Our first candidate plugin: "review-pool" (Haiku persona reviewers + consolidator instruction).

## 4. Other features worth having (found while analysing this)

1. **Enforced tool/permission profiles** per agent: `tools: ["Read","Grep","Glob"]`, `permission_mode`,
   `strict_mcp`, `empty_cwd: true`, `setting_sources: []` → translates to the right CLI flags, so
   "read-only" and "code-blind" are properties, not prompt wishes.
2. **Unit `model`/`effort` override in overseer units, gated by policy** (relax OV-R9 for `model` only
   when the value is in `allowed_models`) — avoids needing a new agent for every model choice.
3. **Output-contract validation hook** for fan-out reviewers: built-in `json_schema` post-hook
   (schema path + retry-once-with-validator-message) instead of each project writing its own check.
4. **Reduce-step primitive:** "run these N tasks, then one consolidator that receives only their output
   paths" — a first-class DAG pattern with partial-failure semantics (proceed with ≥K of N, record gaps).
5. **Cost/model guard rails at run level:** `max_cost_by_model`, and an alert when spend on a
   more-expensive model than planned exceeds a threshold.
6. **`ao agents list|show|diff`** commands and UI panel: resolved spec, source file, model, tools.
7. **Dry-run planner** (`ao plan`): show the exact argv, model, cwd and tool set each task *would* use,
   without spawning anything.
8. **Stable agent identity in usage reports** (agent + resolved model + effort already grouped in
   `usage.py`; surface "planned model" next to it).

## 5. Acceptance criteria (A+B slice)

1. Two `--agents` files merge deterministically; `--explain-agent` shows provenance for every field.
2. An agent with `"extends"` produces a spec identical to the hand-expanded one (golden test).
3. A task whose resolved model is `claude-haiku-5-5` but whose CLI output reports another model ends
   `failed` with `model_mismatch`; a matching run is `ok`. Covered by a fake-executor test and one
   real-CLI smoke test.
4. `ao validate` warns on unpinned model and on `command_template --model` vs `AgentSpec.model` conflict.
5. Existing single `agents.json` workflows run unchanged (no warnings promoted to errors by default).
6. `ao report-timing`/usage shows planned vs actual model.

## 6. Open questions

- Does the Claude CLI expose the executing model in `--output-format json` for `-p` runs reliably enough
  to assert against? (needed for B)
- What does the CLI do on an unknown alias, an unavailable model, or overload — error, or silent
  fallback? Needs an experiment before B's guarantees can be stated.
- Should dynamic agents (D) be allowed in non-overseer workflows, or only through `emit_tasks`?
- Versioning/trust model for plugins (E): signed? hash-pinned? who may install into a shared service.

## 7. Workaround available today (no ao change)
Add `pool-reviewer` to `specs/agents.json` with `"model": "claude-haiku-5-5"`, add a matching `kind_map`
entry + instruction file in the overseer config, and confirm via `ao report-timing` that tasks ran on Haiku.


---
## Triage (2026-10-16, agent-orchestrator maintainers' side)
| Item | Verdict | Ticket |
|---|---|---|
| A composition | Accept, pick first | T-ag1a01 |
| 4.6 agents list/show/diff | Accept with A | T-ag1b02 |
| B model assertion + 4.8 | Accept, narrower (extend existing model policy, no new `policy` block) | T-ag2a03 |
| 4.1 tool profiles | Accept | T-ag3b04 |
| 4.3 json_schema hook | Accept | T-ag4c05 |
| 4.4 reduce step | Accept | T-ag5d06 |
| 4.5 max_cost_by_model | Accept | T-ag6e07 |
| C templates/fan-out | Partial: only the concurrency cap | T-ag7f08 |
| 4.2 unit model override | Model part already done (brief.model + allowed_models, OV-R9m); effort remains | T-ag8g09 |
| 4.7 ao plan | Maybe, gap-analysis first | T-ag9h10 |
| D dynamic agents | Declined for now | - |
| E plugin packages | Deferred | - |
