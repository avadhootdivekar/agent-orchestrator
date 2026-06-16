# Walkthrough: driving an epic to completion with agent-orchestrator

This guide takes you from zero to a fully orchestrated multi-task epic — including writing
the spec files, running with a dry-run (fake) executor, handling a task failure, and resuming
from where things stopped. It mirrors a real feature-delivery epic.

---

## Scenario

You have a **bug-fix epic** with three ordered tasks:

1. **triage** — an architect agent reads the issue description and writes a diagnosis
2. **fix** — a developer agent reads the diagnosis and writes the patch report
3. **verify** — a tester agent reads the patch report and writes the test results

Artifacts flow linearly: `output/diagnosis.md` → `output/patch-report.md` → `output/test-results.md`.

---

## Step 1 — Create your spec files

Create a working directory for this epic:

```bash
mkdir -p my-epic/specs/instructions
cd my-epic
```

### 1a. Instruction files

Instruction files are plain text/markdown. The orchestrator passes their **path** to the
agent — the agent reads them, not the engine. Content is entirely yours.

`specs/instructions/triage.md`:

```markdown
# Triage instruction

You are an architect agent. Read the bug report linked in your inputs.
Diagnose the root cause and write a concise diagnosis to the output path.
Include: root cause, affected components, proposed fix approach.
```

`specs/instructions/fix.md`:

```markdown
# Fix instruction

You are a developer agent. Read the diagnosis from your inputs.
Implement the fix and write a patch report to the output path.
Include: files changed, summary of changes, confidence level.
```

`specs/instructions/verify.md`:

```markdown
# Verify instruction

You are a tester agent. Read the patch report from your inputs.
Verify the fix is correct and write test results to the output path.
Include: test cases executed, pass/fail, any regressions.
```

### 1b. Workflow spec

`specs/workflow.json`:

```json
{
  "version": "1.0",
  "id": "bug-fix-epic",
  "name": "Bug-fix: triage → fix → verify",
  "repo_set": "my-repo",
  "defaults": {
    "retries": { "max_attempts": 2, "backoff_seconds": 10 }
  },
  "triggers": [
    { "type": "manual" }
  ],
  "tasks": [
    {
      "id": "triage",
      "agent": "architect",
      "instruction": "specs/instructions/triage.md",
      "inputs": [],
      "outputs": ["output/diagnosis.md"]
    },
    {
      "id": "fix",
      "agent": "developer",
      "instruction": "specs/instructions/fix.md",
      "inputs": ["output/diagnosis.md"],
      "outputs": ["output/patch-report.md"],
      "depends_on": ["triage"]
    },
    {
      "id": "verify",
      "agent": "tester",
      "instruction": "specs/instructions/verify.md",
      "inputs": ["output/patch-report.md"],
      "outputs": ["output/test-results.md"],
      "depends_on": ["fix"]
    }
  ]
}
```

Key points:
- `depends_on` is explicit; the engine also infers edges from input/output overlap.
- `instruction` is always a **path** relative to `workspace_root` — never inline text.
- `inputs`/`outputs` are artifact paths the engine tracks for skip/resume logic.

### 1c. RepoSet

`specs/reposet.json`:

```json
{
  "version": "1.0",
  "repo_sets": {
    "my-repo": {
      "description": "The single repo being fixed.",
      "workspace_root": "/path/to/my-epic",
      "repos": [
        { "id": "core", "path": "/path/to/my-epic", "role": "primary" }
      ]
    }
  }
}
```

Replace `/path/to/my-epic` with the absolute path to your working directory.
Alternatively, use `AO_WORKSPACE_ROOT` at run time to override `workspace_root` without
editing the file:

```bash
export AO_WORKSPACE_ROOT=$(pwd)
```

### 1d. Agents config

`specs/agents.json` — use `fake` executor for dry-runs; switch to `claude_cli` for real runs.

```json
{
  "version": "1.0",
  "agents": {
    "architect": {
      "executor": "claude_cli",
      "command_template": ["claude", "-p", "{prompt}"],
      "prompt_template": "Act as the architect agent. Follow instructions at {instruction}. Inputs: {inputs}. Write outputs to: {outputs}. Repos: {repos}.",
      "context_window": "isolated"
    },
    "developer": {
      "executor": "claude_cli",
      "command_template": ["claude", "-p", "{prompt}"],
      "prompt_template": "Act as the developer agent. Follow instructions at {instruction}. Inputs: {inputs}. Write outputs to: {outputs}. Repos: {repos}.",
      "context_window": "isolated"
    },
    "tester": {
      "executor": "claude_cli",
      "command_template": ["claude", "-p", "{prompt}"],
      "prompt_template": "Act as the tester agent. Follow instructions at {instruction}. Inputs: {inputs}. Write outputs to: {outputs}. Repos: {repos}.",
      "context_window": "isolated"
    }
  }
}
```

> **Prompt template variables** — only paths and IDs are interpolated, never file contents:
> - `{instruction}` — the instruction file path
> - `{inputs}` — space-separated list of input artifact paths
> - `{outputs}` — space-separated list of output artifact paths
> - `{repos}` — `id=path` pairs for each repo in the set

---

## Step 2 — Validate

Always validate before running. This catches schema violations, unknown agent/repo keys,
missing instruction paths, and cycles — with no execution cost.

```bash
ao validate \
  --workflow  specs/workflow.json \
  --reposets  specs/reposet.json \
  --agents    specs/agents.json
```

Expected output:

```
OK: all specs valid
```

If something is wrong, the error message pinpoints the problem:

```
ERROR: SpecValidationError: task 'fix' depends_on unknown task 'trage'
```

---

## Step 3 — Dry-run with the fake executor

Before firing real agents, swap the executor to `fake` in `specs/agents.json`:

```json
{
  "version": "1.0",
  "agents": {
    "architect": { "executor": "fake" },
    "developer": { "executor": "fake" },
    "tester":    { "executor": "fake" }
  }
}
```

The fake executor is deterministic: it returns `succeeded` and **touches** (creates empty
files for) all declared outputs. No real agent runs, no API calls made.

```bash
export AO_WORKSPACE_ROOT=$(pwd)

ao run \
  --workflow  specs/workflow.json \
  --reposets  specs/reposet.json \
  --agents    specs/agents.json
```

Expected output:

```
Run:    bug-fix-epic-20260616T083000Z
Status: succeeded

Task                           Status          Attempts
-------------------------------------------------------
triage                         succeeded       1
fix                            succeeded       1
verify                         succeeded       1
```

Check that the output files were produced:

```bash
ls output/
# diagnosis.md  patch-report.md  test-results.md
```

---

## Step 4 — Real run with claude_cli

Switch `executor` back to `claude_cli` in `specs/agents.json` (restore the full config from
step 1d). Ensure `claude` CLI is installed and authenticated.

```bash
ao run \
  --workflow  specs/workflow.json \
  --reposets  specs/reposet.json \
  --agents    specs/agents.json
```

The engine:
1. Checks `output/diagnosis.md` doesn't already exist → runs `triage`
2. Waits for `triage` to write `output/diagnosis.md` → then runs `fix`
3. Waits for `fix` to write `output/patch-report.md` → then runs `verify`

If `triage` already ran in a prior session and `output/diagnosis.md` exists, it is
**skipped automatically** — no re-work.

---

## Step 5 — Handling a failure and resuming

Suppose `verify` fails (e.g. the tester agent exits non-zero). The engine records the
failure and exits with code 1:

```
Run:    bug-fix-epic-20260616T090000Z
Status: failed

Task                           Status          Attempts
-------------------------------------------------------
triage                         succeeded       1
fix                            succeeded       1
verify                         failed          2
```

The run ID (`bug-fix-epic-20260616T090000Z`) is persisted to disk. Fix whatever caused the
failure (update the instruction, fix a bug, clear a stale output), then resume:

```bash
ao resume \
  --run-id   bug-fix-epic-20260616T090000Z \
  --workflow  specs/workflow.json \
  --reposets  specs/reposet.json \
  --agents    specs/agents.json
```

On resume:
- `triage` is **skipped** — `output/diagnosis.md` exists and its status is `succeeded`
- `fix` is **skipped** — same reason
- `verify` is **re-run** — status was `failed`

```
Run:    bug-fix-epic-20260616T090000Z
Status: succeeded

Task                           Status          Attempts
-------------------------------------------------------
triage                         skipped         0
fix                            skipped         0
verify                         succeeded       1
```

---

## Step 6 — Check status at any time

```bash
ao status \
  --run-id   bug-fix-epic-20260616T090000Z \
  --workflow  specs/workflow.json \
  --reposets  specs/reposet.json \
  --agents    specs/agents.json
```

This reads persisted run state from disk and prints the table — no execution.

---

## Multi-repo variant

When your epic spans multiple repos, add them all to the same `repo_set`:

```json
{
  "version": "1.0",
  "repo_sets": {
    "my-multi-repo": {
      "workspace_root": "/work",
      "repos": [
        { "id": "api",      "path": "/work/api",      "role": "primary" },
        { "id": "frontend", "path": "/work/frontend",  "role": "support" },
        { "id": "infra",    "path": "/work/infra",     "role": "support" }
      ]
    }
  }
}
```

Reference it in your workflow:

```json
{
  "id": "cross-repo-epic",
  "repo_set": "my-multi-repo",
  ...
}
```

Each agent receives `{repos}` = `api=/work/api frontend=/work/frontend infra=/work/infra` in
its prompt — enough for the agent to know where every repo lives without the engine reading
any of their contents.

---

## Adding a cron trigger

To run the workflow automatically every morning at 07:00 UTC, add a cron trigger alongside
the manual one:

```json
"triggers": [
  { "type": "manual" },
  { "type": "cron", "schedule": "0 7 * * *", "timezone": "UTC" }
]
```

The scheduler is pluggable; the CLI `ao run` is the manual entry point. Cron-triggered
execution requires a process manager or wrapper that calls `ao run` on schedule.

---

## Environment variable shorthand

Rather than typing `--reposets` and `--agents` on every command, export them:

```bash
export AO_REPOSETS=$(pwd)/specs/reposet.json
export AO_AGENTS=$(pwd)/specs/agents.json
export AO_WORKSPACE_ROOT=$(pwd)

ao validate --workflow specs/workflow.json
ao run      --workflow specs/workflow.json
ao resume   --run-id <RUN_ID> --workflow specs/workflow.json
ao status   --run-id <RUN_ID> --workflow specs/workflow.json
```

---

## Summary of the epic lifecycle

```
Write specs (workflow + reposet + agents + instructions)
  │
  ▼
ao validate          ← catches schema/cross-ref/cycle errors with no execution cost
  │
  ▼
ao run (fake)        ← dry-run: verifies DAG, I/O wiring, artifact creation
  │
  ▼
ao run (claude_cli)  ← real execution; tasks run in DAG order
  │
  ├─ success ──► done, check output/ for artifacts
  │
  └─ failure ──► fix the cause ──► ao resume --run-id <ID>
                                      │
                                      └─ succeeds ──► done
```

---

## Reference

| Item | Location |
|------|----------|
| Workflow JSON Schema | `specs/workflow.schema.json` |
| RepoSet JSON Schema | `specs/reposet.schema.json` |
| Agents JSON Schema | `specs/agents.schema.json` |
| Working 3-task example | `specs/examples/workflow.json` |
| Multi-repo reposet example | `specs/examples/reposet.json` (see `experiment-set`) |
| CLI `--help` | `ao --help` / `ao run --help` |
| HLD / LLD | `docs-md/hld-agent-orchestrator.md`, `docs-md/lld-agent-orchestrator.md` |
