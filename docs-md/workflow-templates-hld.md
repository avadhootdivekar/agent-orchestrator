# Workflow templates — HLD (E-Tpl3x9)

Status: Shipped on branch `ad/workflow-templates` (PR to main pending)
Driven by: generalizing `ao-runner-finplan/workflows/epic-runner/new-epic-run.sh` into a
first-class, config-registered scaffolding feature usable from the CLI and the dashboard.

## 1. Problem

Runner workspaces (e.g. ao-runner-finplan) scaffold per-run workflow instances with
bespoke bash: generate `workflow.json` bound to a run dir, a `prompt.md` skeleton, aux
contract files, control dirs — then `ao validate` + `ao run`. The engine has no notion of
this "instance from a template" step, so:

- every workspace reinvents the scaffolder;
- the dashboard (E-Ui7Kq2) can only launch **already-scaffolded** specs — a user cannot
  start a brand-new epic/bug run from the browser;
- prompts can only be typed in the UI when the spec declares `prompt_path`, which the
  bespoke generators don't emit.

## 2. Design

A **workflow template** is a directory containing a `template.yaml` manifest plus file
templates. Templates are *defined at install/config time* (never authored in the UI) and
*instantiated* many times, each instantiation producing a self-contained run instance
(dir with rendered `workflow.json` + `prompt.md` + aux files) that the existing
run/resume/status machinery — CLI and dashboard both — operates on unchanged.

### 2.1 Discovery (config-driven, three sources)

1. **Workspace-registered** — new `ProjectConfig` field:
   ```yaml
   # .ao/config.yaml
   templates:
     - workflows/epic-runner/template     # a template dir (contains template.yaml)
     - my-templates/                      # or a dir of template dirs (scanned 1 level)
   ```
   Paths resolved relative to the config file, same as `workflow:`/`agents:`.
2. **Built-in** — package data under `src/agent_orchestrator/templates/<name>/`
   (shipped in the wheel; v1 ships `routed-runner`).
3. **Ad-hoc path** — `ao new <path-to-template-dir> …` accepts a directory directly.

Name collisions: workspace templates shadow built-ins of the same name.
Older `ao` versions ignore the `templates:` key (pydantic `extra` tolerance) — the config
stays backward-compatible.

### 2.2 Manifest — `template.yaml`

```yaml
version: "1.0"
name: routed-runner
description: One prompt -> classified route (bug/epic/task/documentation/testing) -> done
id_pattern: "e-{rand6}-{slug}"            # instance id; {rand6} = 6 random [a-z0-9]
instance_dir: "workflows/routed-runner/runs/{id}"   # workspace-root-relative
params:                                    # mapping name -> spec; all v1 params are strings
  type:
    description: Force the route (omit to auto-classify)
    required: false
    enum: [bug, epic, task, documentation, testing]
dirs:                                      # created inside instance_dir
  - outputs
  - outputs/tasks
  - control
  - needs-input
files:                                     # rendered into instance_dir
  - source: workflow.json.tmpl             # template-dir-relative
    target: workflow.json
  - source: prompt.md.tmpl
    target: prompt.md
    keep_existing: true                    # never clobber a user-edited prompt
  - source: breakdown-contract.md.tmpl
    target: breakdown-contract.md
  - content: "{{ params.type }}\n"         # inline alternative to source
    target: outputs/forced-type.txt
    when: params.type                      # emit only when the param is set;
                                           # when unset, DELETE a stale target (pin bug)
assets:                                    # shared, materialized ONCE per workspace
  - source: instructions/                  # dir copy, template-dir-relative
    target: workflows/routed-runner/instructions/
    keep_existing: true                    # skip files that already exist (user-tuned)
required_agents: [architect, developer, tester, reviewer, manager]
```

- `source`/`content` are mutually exclusive per file entry; `when:` names a param and is
  truthy iff that param was provided non-empty. Both the bare name (`when: type`) and the
  prefixed form (`when: params.type`) are accepted; the bare form is canonical.
- Rendering: `{{ var }}` substitution (whitespace-tolerant) over these variables only:
  `id`, `slug`, `instance_dir` (workspace-relative, forward slashes), `workspace_root`,
  `params.<name>`. Unknown variable → hard error (fail fast, no silent `{{ }}` leakage).
  No conditionals/loops in v1 — branching lives in the manifest (`when:`), keeping
  templates declarative per CLAUDE.md. Literal `{{` needed in output (e.g. JSON examples
  in contract files) is escaped as `{{"{{"}}` — v1 templates avoid needing it.
- `assets` render too (same variables) but are keyed to the workspace, not the instance;
  `keep_existing: true` is the expected mode so a workspace can tune its instructions.
- Validation of a manifest failure → `TemplateError` with the offending field.

### 2.3 The rendered workflow MUST be dashboard-launchable

Templates are required (validated at `ao new` time) to render a workflow spec that:
- declares `prompt_path` pointing at the instance's `prompt.md`, and lists that path in
  the inputs of the task that consumes it → the existing UI prompt box and
  `ao run --prompt` both work with zero new engine code;
- passes `ao validate` against the workspace's agents/reposets.

### 2.4 Module API — `agent_orchestrator/templates.py`

```python
class TemplateError(Exception): ...

@dataclass(frozen=True)
class TemplateParam:  name: str; description: str = ""; required: bool = False
                      enum: list[str] | None = None; default: str | None = None

@dataclass(frozen=True)
class TemplateInfo:   name: str; description: str; path: str
                      source: Literal["builtin", "workspace", "path"]
                      params: list[TemplateParam]; required_agents: list[str]
                      prompt_skeleton: str | None   # rendered-with-placeholders preview

@dataclass(frozen=True)
class InstantiateResult:
    instance_dir: str        # absolute
    workflow_path: str       # absolute
    prompt_path: str | None  # absolute
    created: list[str]; skipped: list[str]   # workspace-relative, for reporting

def discover_templates(workspace_root: str, config: ProjectConfig | None) -> list[TemplateInfo]
def load_template(path_or_name: str, workspace_root: str, config: ProjectConfig | None) -> TemplateInfo
def instantiate(template: TemplateInfo, workspace_root: str, *,
                slug_or_id: str, params: dict[str, str],
                prompt_text: str | None = None,      # write into prompt.md (skip skeleton)
                ) -> InstantiateResult               # raises TemplateError
```

Instance-exists semantics: rerunning on an existing id is **idempotent** like the bash
script (regenerate non-`keep_existing` files, keep prompt/outputs); a `prompt_text` that
conflicts with an existing, *edited* prompt.md is an error (no silent overwrite).

### 2.5 CLI

- `ao templates` — list discovered templates (name, source, description, params).
- `ao new <template> <slug|id> [--param k=v]… [--prompt-file PATH] [--validate-only] [--run]`
  — scaffold; `--run` then validates + runs in-process exactly like `ao run --workflow
  <rendered>`. Prints instance paths + next-step commands. `<template>` is a name or a
  directory path.

### 2.6 Dashboard API (service + FastAPI, same thin-adapter split as E-Ui7Kq2)

- `GET  /api/templates` → `TemplateInfo[]` (serialized dataclasses).
- `POST /api/templates/{name}/instances`
  body `{slug_or_id?: str, params: {..}, prompt?: str, start: bool, options: {..}}`
  → 201 `{instance_dir, workflow_path, workflow: WorkflowInfo, launch: LaunchRecord|null}`.
  Missing `slug_or_id` → derive a slug from the first prompt line (kebab-case) or error.
  `start: true` → validate then `ProcessSupervisor.launch_run` (existing path).
  Errors: 404 unknown template · 400 bad params/slug/validation failure ·
  409 instance exists with a conflicting prompt.

### 2.7 Frontend (`ui/`)

"New run" view gains a mode toggle: **From workflow** (existing) / **From template**:
template picker (name + description) → param controls (enum → select, else text input,
required marked) → prompt textarea (prefilled with `prompt_skeleton`) → slug field
(optional) → `Create` / `Create & run`. On launch → jump to RunDetail (existing flow).

### 2.8 Built-in `routed-runner` template (finplan-free)

`src/agent_orchestrator/templates/routed-runner/`: the full routed multi-type DAG from
the finplan epic-runner (shared head git-branch-off → classify router; bug / epic / task /
documentation / testing routes; per-route push sinks; breakers incl. budget/time/fan-out;
breakdown contract for the epic route's dynamic fan-out) with **all FinPlan context
removed** — instructions speak of "the target repository / project conventions",
prompt skeleton has a generic "Project context" section, `repo_set` is `{{ params.repo_set }}`
(required param, no default). Instructions materialize as workspace assets on first use so
each workspace can tune them.

**Per-task effort/model (ADR-0003 decision 2, follow-through):** `TaskSpec` gained
optional `model`/`effort`/`max_turns` fields that win over the dispatched agent's own
values (fill-in, not clobber; resolved once at dispatch by
`models.resolve_effective_agent`), and `effort` gained an `"xhigh"` tier above `"high"`
(→ 120 `--max-turns`, `EFFORT_MAX_TURNS`). The epic route's `task-breakdown` stage uses
this: `breakdown-contract.md.tmpl`'s emitted 5-entry pipeline shapes default `impl`/`test`
passes to `"effort": "medium"` (a ~10-minute task grain), and the breakdown agent may mark
a genuinely larger `<tid>` `"high"`/`"xhigh"` and/or set an explicit `model` instead of
forcing an artificial split — see the contract's "Effort & model per task" section and
`instructions/07-task-breakdown.md`.

### 2.9 ao-runner-finplan wiring (kept out of the engine repo)

- `workflows/epic-runner/template/` — `template.yaml` + `workflow.json.tmpl` +
  `prompt.md.tmpl` (keeps the FinPlan context section) + `breakdown-contract.md.tmpl`;
  references the existing `workflows/epic-runner/instructions/` (no asset copy needed —
  they already live in the workspace).
- `.ao/config.yaml` gains `templates: [workflows/epic-runner/template]`.
- `workflow.json.tmpl` adds `prompt_path` so the dashboard prompt box works.
- `new-epic-run.sh` delegates scaffolding to `ao new` (probes for template support the
  same way `require_routing_support` probes routing; stale-`ao` message updated), keeping
  resume/extend-breaker passthrough and the interactive confirm UX.

## 3. Non-goals (v1)

- Authoring/editing templates from the UI (explicitly out — config-time only).
- Template versioning/migration of existing instances.
- Non-string param types; loops/conditionals inside file templates.
- Auth on the new endpoints (inherits the dashboard's loopback-only posture).

## 4. Testing

- Unit: manifest parsing/validation, rendering (unknown var, `when`, keep_existing,
  idempotent re-instantiate, prompt-conflict), discovery precedence (workspace shadows
  builtin), id/slug resolution.
- CLI e2e via `CliRunner`: `ao templates`, `ao new` happy path + `--run` with fake
  executors driving a scaffolded instance end-to-end.
- Service/API integration: endpoints against a temp workspace with a stub supervisor
  (same harness as E-Ui7Kq2 tests), incl. 400/404/409 paths.
- Frontend: vitest for the template form logic.
- Built-in template: `ao new routed-runner … --validate-only` in a temp workspace with
  fake agents must pass `ao validate`.
