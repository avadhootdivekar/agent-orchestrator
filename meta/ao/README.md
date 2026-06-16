# meta/ao — Agent Orchestrator drives itself

This directory contains the AO workflow definitions used to develop the agent-orchestrator project using itself.

## Bootstrapping answer

**Q: Will the orchestrator break if I change its source while it's running?**

The package is installed in editable mode (`uv pip install -e .`), so Python reloads the source on each new invocation. A running workflow is safe: each `uv run agent-orchestrator …` call starts fresh; in-flight tasks use the version that was loaded at their start. You can edit source between tasks freely. The only risk is changing the engine mid-task-execution, which doesn't happen here since tasks are sequential by default.

**Recommendation**: run all orchestrator commands via `uv run agent-orchestrator …` — never `python -m` outside uv.

## Directory layout

```
meta/ao/
  README.md              ← this file
  agents.json            ← agent registry for AO workflows
  reposets.json          ← repo config (points to this repo)
  instructions/          ← shared task instruction prompts
    01-gather-requirements.md
    02-design-epic.md
    03-create-tasks.md
    04-implement-tasks.md
    05a-critic-review.md
    05b-architect-review.md
  workflows/
    epic-lifecycle-template.json   ← copy per epic, fill __epic_id__
  epics/
    {epic-id}/                     ← created per epic (see NEW-EPIC.md)
      prompt.md                    ← user provides this
      workflow.json                ← instantiated from template
      outputs/
        requirements.md
        adr.md
        hld.md
        lld.md
        tasks.json
        impl-report.md
        critic-report.md
        final-review.md
  NEW-EPIC.md            ← step-by-step guide for starting a new epic
```

## Epic lifecycle workflow (stages)

```
[User prompt]
     │
     ▼
┌─────────────────────┐
│ 1. gather-req       │  architect → requirements.md
└──────────┬──────────┘
           ▼
┌─────────────────────┐
│ 2. design-epic      │  architect (opus) → adr.md + hld.md + lld.md
└──────────┬──────────┘
           ▼
┌─────────────────────┐
│ 3. create-tasks     │  architect → tasks.json + EPIC.md stub
└──────────┬──────────┘
           ▼
┌─────────────────────┐
│ 4. implement-tasks  │  manager → dev→test→review per task (max 2 cycles)
└──────────┬──────────┘
           ▼
┌─────────────────────┐
│ 5a. critic-review   │  dev-critic → critic-report.md
└──────────┬──────────┘
           ▼
┌─────────────────────┐
│ 5b. architect-review│  architect → final-review.md
└─────────────────────┘
```

## How to start a new epic

See [`NEW-EPIC.md`](NEW-EPIC.md) for the full walkthrough.

Quick version:
```bash
# 1. Create epic directory and provide your requirements prompt
mkdir -p meta/ao/epics/my-epic/outputs
cp meta/ao/workflows/epic-lifecycle-template.json meta/ao/epics/my-epic/workflow.json
# Edit workflow.json: replace __epic_id__ with my-epic
# Edit meta/ao/epics/my-epic/prompt.md with your requirements

# 2. Validate the workflow
uv run agent-orchestrator validate meta/ao/epics/my-epic/workflow.json \
  --agents meta/ao/agents.json \
  --reposets meta/ao/reposets.json

# 3. Run it
uv run agent-orchestrator run meta/ao/epics/my-epic/workflow.json \
  --agents meta/ao/agents.json \
  --reposets meta/ao/reposets.json

# 4. Check status / resume if interrupted
uv run agent-orchestrator status meta/ao/epics/my-epic/workflow.json
uv run agent-orchestrator resume meta/ao/epics/my-epic/workflow.json \
  --agents meta/ao/agents.json \
  --reposets meta/ao/reposets.json
```
