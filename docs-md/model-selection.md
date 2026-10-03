# Model selection (per workflow, per task, per overseer unit)

Which model a task runs on is resolved in ONE place, `models.resolve_effective_agent`:

| precedence | source | notes |
|---|---|---|
| 1 (highest) | `task.model` | static task or `emit_tasks`-injected task |
| 2 | `workflow.defaults.model` | applies to every task that names no model, injected ones included |
| 3 | `agent.model` | agent spec, incl. the CLI/env/config fill-in (`--model`, `AO_MODEL`, `.ao/config.yaml`) |
| 4 | a `--model` baked into the agent's `command_template` | |

A task- or workflow-level model **replaces** a model baked into the agent's `command_template`
(`strip_model_flag`), so e.g. `architect-opus` can be pointed at Sonnet for one task.

A model is any well-formed id or alias: `opus`, `sonnet[1m]`, `claude-opus-4-8`,
`claude-sonnet-4-5`, `claude-sonnet-5-5`, `claude-haiku-4-5-20251001`. Only the character set is
checked (`models.MODEL_ID_PATTERN`; no leading `-`, so an agent-authored manifest can't smuggle a
CLI flag); whether the model exists is the `claude` CLI's call. An agent's
`forbidden_task_models` is enforced against task models **and** the workflow default, at
`ao validate` and again at `emit_tasks` injection.

## Custom workflows

```json
{ "defaults": { "model": "claude-sonnet-4-5" },
  "tasks": [ { "id": "design", "agent": "architect", "model": "claude-opus-4-8", "...": "..." } ] }
```

Tasks added at run time through an `emit_tasks` manifest may carry `model` too, or inherit
`defaults.model`.

## overseer-runner

Wave units may now carry `model` (previously forbidden by OV-R9). It is chosen by one
deterministic rule the checkpoint agent must follow (**OV-R9m**):

`brief.model` > `kind_map[kind].model` (in `overseer-config.json`) > `default_unit_model` > none
(omit; the agent's own model).

Template params: `default_unit_model` and `allowed_models` (comma-separated allowlist; empty =
any well-formed id). `overseer_model` still pins the checkpoint tasks themselves.

Caveat: a global `--model`/`AO_MODEL` sets `agent.model`, which task/workflow models beat, so it
no longer clobbers per-unit choices; see ADR-0003 for the remaining agent-level behaviour.

## Dashboard

The run detail shows a "Models used" summary (tasks per effective model) above the task table;
the per-task Model column, Now-running rows and the graph hover card already show it. The
template launcher renders the new overseer params automatically.
