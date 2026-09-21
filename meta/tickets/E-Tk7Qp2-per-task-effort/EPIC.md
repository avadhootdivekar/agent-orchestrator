# EPIC: E-Tk7Qp2-per-task-effort

## Metadata
- Epic ID: `E-Tk7Qp2-per-task-effort`
- Title: Per-task model/effort/max_turns overrides + xhigh effort tier
- Owner: developer (agent)
- Created: 2026-08-28
- Last Updated: 2026-08-28
- Status: Done

## Summary
- Goal: Let a `TaskSpec` override the dispatched agent's `model`/`effort`/`max_turns`
  for that one task (task > agent > invocation fill-in), per ADR-0003 decision 2's
  tracked-but-deferred requirement. Add a new `"xhigh"` effort tier above `"high"`
  (→ 120 `--max-turns`). Wire the builtin `routed-runner` epic template's
  `task-breakdown` stage to emit per-task `effort`/`model`, defaulting fan-out tasks
  sized to ~10 minutes to `"effort": "medium"`, with the breakdown agent free to
  choose `"high"`/`"xhigh"` and/or a different model for genuinely bigger tasks.
- Scope In: `models.py` (TaskSpec fields, `EffortLevel`, `EFFORT_MAX_TURNS["xhigh"]`,
  `resolve_effective_agent`), `engine.py` dispatch wiring, `executors/fake.py`
  (`resolved_agents` recording), `specs/workflow.schema.json` +
  `specs/agents.schema.json`, the routed-runner `breakdown-contract.md.tmpl` +
  `instructions/07-task-breakdown.md` + `README.md`, `docs-md/workflow-templates-hld.md`,
  ADR-0003 shipped-note, new/updated tests.
- Scope Out: `cli.py`'s global `--model`/`--effort` flag (its `_valid_efforts` allowlist
  and the known invocation-clobbers-agents defect) — file-ownership boundary with a
  parallel agent on this branch (`ad/multi-workspace-service`); task-level overrides
  win regardless since the clobber loop only touches `AgentSpec`, never `TaskSpec`.
  `project_config.py`'s `effort` docstring (same boundary). `ao-runner-finplan`'s
  mirror template (listed as follow-up work only, not edited).

## Requirements
- FR-1: `TaskSpec` gains optional `model: str | None`, `effort: EffortLevel | None`,
  `max_turns: int | None`. `AgentSpec.effort` and `TaskSpec.effort` share one
  `EffortLevel` Literal (`low|medium|high|xhigh`) so they can't drift.
- FR-2: `EFFORT_MAX_TURNS["xhigh"] = 120`.
- FR-3: One resolution helper (`models.resolve_effective_agent(task, agent)`) applies
  task > agent precedence per-field (fill-in, not clobber); the engine calls it once
  per dispatch (`_run_with_retries`) before building `TaskContext`, so every executor
  (today: `claude_cli`, `fake`) sees already-resolved settings with zero executor-side
  changes needed.
- FR-4: `specs/workflow.schema.json` (task properties) and `specs/agents.schema.json`
  (effort enum) accept the new fields/value at the schema layer `ao validate` uses.
- FR-5: An `emit_tasks` manifest entry's `effort`/`model`/`max_turns` survive
  `read_task_manifest`'s `TaskSpec(**t)` construction and reach dispatch identically to
  a statically-declared task.
- FR-6: `breakdown-contract.md.tmpl` documents the field allowlist + sizing guidance
  (~10 min → `"effort": "medium"` default; bigger only when genuinely warranted) and
  the 5-entry pipeline shapes default `impl`/`test` passes to `"effort": "medium"`;
  `instructions/07-task-breakdown.md` states the breakdown agent owns the size/effort/
  model call per `<tid>`.

## Task List
No sub-task folders (single-developer, one-sitting scope per the assigning agent's
sizing call) — tracked directly in this EPIC + STATUS.

## Risks and Dependencies
- Depends on ADR-0003 (Accepted) decision 2 for the precedence contract; this epic is
  that decision's shipped follow-through, not a new design decision.
- The live global `--model`/`--effort` invocation-clobbers-`AgentSpec` defect (ADR-0003
  §"Live defect") is untouched — out of scope by file ownership. Per-task overrides are
  immune to it (different field, never touched by the clobber loop), so this doesn't
  block the feature, but the defect itself remains open for a future slice.
- `cli.py`'s `_valid_efforts = ("low","medium","high")` (global `--effort`/`AO_EFFORT`/
  config `effort`) does NOT accept `"xhigh"` — only the per-task/per-agent field does.
  Flagged for the file's owning agent, not fixed here.

## Links
- Design doc: `docs-md/adr/ADR-0003-settings-precedence-policy.md` (decision 2, shipped-note)
- Design doc: `docs-md/workflow-templates-hld.md` §2.8 (per-task effort/model note)
- Output artifacts (if any): none (code + docs only)
