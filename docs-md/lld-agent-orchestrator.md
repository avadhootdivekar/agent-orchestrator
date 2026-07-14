# LLD — Agent Orchestrator (Option A)

- Epic: [`E-m2k9pa-orchestrator-mvp-a`](../meta/tickets/E-m2k9pa-orchestrator-mvp-a/EPIC.md) · HLD: [`hld-agent-orchestrator.md`](hld-agent-orchestrator.md) · ADR: [`ADR-0001`](adr/ADR-0001-orchestration-approach.md)
- Audience: a developer new to the repo should be able to implement each module from this doc alone.
- Stack: Python 3.11+, `pydantic` v2 (models/validation), `jsonschema` (spec schema), `croniter` (cron), `typer` (CLI), `pytest`/`ruff`/`mypy`.

## 0. Package layout
```
src/agent_orchestrator/
  __init__.py
  models.py          # pydantic models: RepoSet, AgentSpec, TaskSpec, WorkflowSpec, TaskContext, TaskResult, RunState
  config.py          # load + merge config files (reposet.json, agents.json), env overrides
  spec.py            # load workflow.json/yaml + JSON-Schema validation → WorkflowSpec
  dag.py             # build graph, cycle detection (Kahn), topological order, input/edge validation
  executors/
    __init__.py
    base.py          # Executor ABC + TaskContext/TaskResult
    claude_cli.py    # ClaudeCliExecutor (subprocess, paths-only)
    fake.py          # FakeExecutor (deterministic, for tests)
  artifacts.py       # ArtifactStore ABC + LocalFsArtifactStore
  runstate.py        # RunStateStore (JSON file) + resume logic
  scheduler.py       # Scheduler ABC + ManualScheduler + CronScheduler (croniter, injectable clock)
  engine.py          # Orchestrator: ties everything; run()/resume()
  errors.py          # typed exceptions
  cli.py             # typer app: validate/run/resume/status
specs/
  workflow.schema.json
  reposet.schema.json
  agents.schema.json
  examples/{workflow.json, reposet.json, agents.json, instructions/*.md}
```

## 1. Data models (`models.py`) — pydantic v2

```python
class RepoRef(BaseModel):
    id: str
    path: str                      # absolute or workspace-relative dir
    role: Literal["primary","support"] = "support"

class RepoSet(BaseModel):
    description: str = ""
    repos: list[RepoRef]
    workspace_root: str            # base dir for relative artifact paths

class RetryPolicy(BaseModel):
    max_attempts: int = 1          # >=1 ; 1 == no retry
    backoff_seconds: float = 0.0   # fixed backoff between attempts (MVP)

class AgentSpec(BaseModel):
    executor: Literal["claude_cli","fake"]            # registry of known executors
    command_template: list[str] = ["claude","-p","{prompt}"]
    prompt_template: str = (
        "Follow the instructions in {instruction}. "
        "Input artifacts: {inputs}. Write outputs to: {outputs}. Repos: {repos}."
    )
    context_window: Literal["isolated","shared"] = "isolated"
    extra_args: list[str] = []
    model: str | None = None                          # e.g. "claude-haiku-4-5-…"; injected as --model
    effort: Literal["low","medium","high"] | None = None  # → --max-turns via EFFORT_MAX_TURNS {15,30,60}
    max_turns: int | None = None                      # explicit --max-turns; overrides effort when set
    working_dir: str | None = None                    # agent process cwd; resolved+path-guarded under
                                                       # reposet.workspace_root. None -> workspace root.
```

`working_dir` makes the agent subprocess's **current working directory** configurable per agent.
The engine resolves it against `reposet.workspace_root` (path-traversal guarded) and passes the
absolute result as `TaskContext.cwd`; the executor spawns the process with that `cwd`. This is why
an agent that writes a **relative** output path from its instruction/spec (e.g. a manifest at
`output/tasks-manifest.json`) lands inside the workspace rather than wherever `ao` was invoked.
Default (`None`) resolves to the workspace root.

`effort`/`max_turns` bound the `claude` CLI's per-invocation **turn** budget (a loop-breaker), which
is orthogonal to `RetryPolicy.max_attempts` (task-level **retries**). Token budgeting is the real
cost guard, so `EFFORT_MAX_TURNS` is deliberately generous (`low=15, medium=30, high=60`); an
explicit `max_turns` (or the `ao run --max-turns` override, which sets it on every agent) wins.

```python

class TaskSpec(BaseModel):
    id: str                         # unique within workflow; [a-z0-9-_]+
    agent: str                      # key into agents registry
    instruction: str                # PATH to prompt/spec file (never inlined)
    inputs: list[str] = []          # artifact paths (workspace-relative)
    outputs: list[str] = []         # artifact paths produced
    depends_on: list[str] = []      # explicit upstream task ids
    retries: RetryPolicy | None = None
    timeout_seconds: int | None = None
    skip_if_outputs_exist: bool = True

class WorkflowDefaults(BaseModel):
    retries: RetryPolicy = RetryPolicy()
    timeout_seconds: int = 1800

class Trigger(BaseModel):
    type: Literal["manual","cron","event"]
    schedule: str | None = None     # cron expr when type==cron
    timezone: str = "UTC"
    event: str | None = None        # event key when type==event

class WorkflowSpec(BaseModel):
    version: str
    id: str
    name: str = ""
    repo_set: str                   # key into reposet config
    defaults: WorkflowDefaults = WorkflowDefaults()
    triggers: list[Trigger] = [Trigger(type="manual")]
    tasks: list[TaskSpec]
```

Runtime-only models (not persisted in spec):
```python
class TaskContext(BaseModel):       # PATHS/IDS ONLY — the NFR-1 boundary object
    run_id: str
    task_id: str
    agent: AgentSpec
    instruction_path: str
    input_paths: list[str]
    output_paths: list[str]
    repo_paths: dict[str, str]      # repo id -> abs path
    timeout_seconds: int
    cwd: str = ""                   # resolved absolute agent cwd (from AgentSpec.working_dir);
                                     # "" -> OS inherits the caller's cwd. Path only — NFR-1 safe.
    # NOTE: there is intentionally NO field carrying file CONTENTS.

class TaskResult(BaseModel):
    task_id: str
    status: Literal["succeeded","failed","cancelled","timed_out"]
    attempts: int
    exit_code: int | None = None
    error: str | None = None        # short message only; not payload contents

TaskStatus = Literal["pending","running","succeeded","failed","skipped","cancelled","timed_out"]

class TaskRunState(BaseModel):
    status: TaskStatus = "pending"
    attempts: int = 0
    started_at: str | None = None
    ended_at: str | None = None
    outputs_present: bool = False

class RunState(BaseModel):
    run_id: str
    workflow_id: str
    repo_set: str
    started_at: str
    updated_at: str
    status: Literal["running","succeeded","failed","cancelled"] = "running"
    tasks: dict[str, TaskRunState] = {}
```

## 2. Spec loading + validation (`spec.py`, `config.py`)
- `load_workflow(path) -> WorkflowSpec`: read JSON or YAML (by extension) → dict → `jsonschema.validate(dict, workflow.schema.json)` → `WorkflowSpec(**dict)`. On schema failure raise `SpecValidationError` with the jsonschema path.
- `load_reposets(path) -> dict[str, RepoSet]`, `load_agents(path) -> dict[str, AgentSpec]` — same pattern against their schemas.
- **Env overrides**: paths to config files come from CLI flags or env (`AO_REPOSETS`, `AO_AGENTS`); never hardcoded. Workspace root may be overridden by env `AO_WORKSPACE_ROOT`.
- Cross-validation: every `task.agent` exists in agents registry; `workflow.repo_set` exists in reposets; every `depends_on` references a known task id. Else `SpecValidationError`.

## 3. DAG (`dag.py`)
- Build adjacency from `depends_on` (explicit) **plus** inferred edges: if task B lists an input path that equals an output path of task A, add edge A→B (and warn if not also in `depends_on`).
- **Cycle detection** via **Kahn's algorithm**: compute in-degrees; repeatedly remove zero-in-degree nodes; if any remain → `CycleError(nodes)`.
- `topological_order() -> list[str]`: deterministic (break ties by task id sort) for replayability.
- `validate_inputs()`: for each task, every input path is either produced by an upstream task's outputs or already exists on disk; otherwise `MissingInputError(task, path)` is raised at validate-time only if the input is not produced by any task (runtime existence is checked at run-time).

## 4. Executor interface (`executors/base.py`)
```python
class Executor(ABC):
    @abstractmethod
    def execute(self, ctx: TaskContext) -> TaskResult: ...
```
**Contract (enforced + tested):** an `Executor` receives only a `TaskContext` (paths/ids). It must not be handed file contents by the engine. `ClaudeCliExecutor`:
1. Render `prompt` from `agent.prompt_template` using **paths only** (`{instruction}`,`{inputs}`,`{outputs}`,`{repos}`).
2. Build argv from `agent.command_template` substituting `{prompt}` + `extra_args`.
3. `subprocess.run(argv, timeout=ctx.timeout_seconds, capture_output=True)`. On `TimeoutExpired` → `status="timed_out"`. Map non-zero exit → `failed`. Never log full stdout into orchestrator state — store only exit code + truncated error tail.

`FakeExecutor(behaviors: dict[task_id, "succeed"|"fail"|"timeout"], write_outputs: bool=True)`: deterministic; optionally `touch`es declared output paths so resume/idempotency tests are real. No subprocess, no clock dependence.

## 5. ArtifactStore (`artifacts.py`)
```python
class ArtifactStore(ABC):
    def resolve(self, path: str) -> str: ...      # workspace-relative -> absolute
    def exists(self, path: str) -> bool: ...
```
`LocalFsArtifactStore(workspace_root)`: `resolve` joins + normalizes and **rejects traversal outside workspace_root** (`ArtifactPathError`); `exists` is `os.path.exists`. (Path-traversal guard = NFR-4 safety.)

## 6. RunState + resume (`runstate.py`)
- Persist to `{workspace_root}/.orchestrator/runs/{run_id}/state.json` (configurable dir). Write **after every task transition** (atomic write: temp file + `os.replace`).
- `new_run(workflow) -> RunState` (run_id = `f"{workflow.id}-{utc_compact_timestamp}"`).
- `resume(run_id)`: load state; a task is **skipped** iff `status=="succeeded"` AND every declared output `exists()`. Otherwise it is re-queued (`pending`).
- **Idempotency**: even on a fresh run, if `task.skip_if_outputs_exist` and all outputs exist → mark `skipped`.

## 7. Scheduler (`scheduler.py`)
```python
class Scheduler(ABC):
    def next_fire(self, trigger: Trigger, now: datetime) -> datetime | None: ...
```
- `ManualScheduler`: returns `None` (fired on demand).
- `CronScheduler(clock: Callable[[], datetime] = utcnow)`: uses `croniter` to compute next fire after `now` in `trigger.timezone`. **Clock is injectable** so tests use a fixed clock (deterministic per CLAUDE.md).
- `event` triggers: MVP exposes the interface + a file-watch stub (`EventScheduler` returns when a sentinel path appears); full event system is non-MVP.

## 8. Engine (`engine.py`)
```python
class Orchestrator:
    def __init__(self, executor_factory, artifact_store, runstate_store, logger): ...
    def run(self, workflow: WorkflowSpec, reposets, agents, run_state=None) -> RunState:
        graph = build_dag(workflow)            # raises CycleError
        order = graph.topological_order()
        state = run_state or self.runstate_store.new_run(workflow)
        repo_paths = resolve_repo_paths(reposets[workflow.repo_set])
        for tid in order:
            task = workflow.task(tid)
            if self._should_skip(task, state): mark(state, tid, "skipped"); continue
            self._assert_inputs_exist(task)     # MissingInputError -> task failed, run failed
            result = self._run_with_retries(task, workflow.defaults, agents, repo_paths, state)
            self._verify_outputs(task, result, state)
            self.runstate_store.save(state)
            if state.tasks[tid].status not in ("succeeded","skipped"):
                state.status = "failed"; break  # stop on first hard failure (MVP)
        finalize(state); self.runstate_store.save(state); return state
```
- `_run_with_retries`: loop `1..max_attempts`; build `TaskContext` (**paths only**); `executor.execute(ctx)`; on success break; else sleep `backoff_seconds` (injectable sleeper for tests).
- `_verify_outputs`: after a "succeeded" executor result, all declared outputs must `exists()`; else downgrade to `failed` with `error="declared outputs missing"`.
- The engine **never** reads `instruction`/`inputs`/`outputs` *contents* — only passes paths and checks existence. (NFR-1 invariant, asserted in T12.)

## 9. Claude quota exhaustion handling (`engine.py`, `executors/claude_cli.py`)

Claude's usage-quota exhaustion (session/daily/weekly limit) is **categorically distinct** from a
provider-level 429 rate limit and is handled with a dedicated wait-and-retry loop rather than
counting as a task failure.

### Detection (`claude_cli.py`)

```python
# *** SINGLE SOURCE OF TRUTH — edit ONLY here when the quota message changes ***
_CLAUDE_QUOTA_PATTERN = re.compile(
    r"you(?:'ve?)?\s+hit\s+your\s+\w+\s+limit", re.IGNORECASE
)
```

`parse_usage_and_429()` checks this pattern **before** any 429 detection. On a match it sets
`TaskResult.claude_quota_exhausted = True` and returns immediately (no retry-after extraction,
no JSON parse). `subprocess.run` uses `stdin=subprocess.DEVNULL` so the subprocess never blocks
waiting for user input.

### Engine loop (`engine.py`)

When `result.claude_quota_exhausted` is `True`:

1. Any token-budget estimate already charged for the task is **reversed** (so the budget is
   not debited for work that never ran).
2. `_quota_exhausted_since` is set to `now` on the first hit; subsequent hits within the same
   episode accumulate elapsed time against it.
3. The engine sleeps `quota_poll_seconds` (default 15 min) and then **re-queues the same task**
   (`cursor -= 1`). The retry counter is **not incremented** — quota exhaustion is not a task
   failure.
4. If `now - _quota_exhausted_since >= quota_max_wait_seconds` (default 6 h), the run fails
   with `event="quota.max_wait_exceeded"`.
5. `_quota_exhausted_since` is reset to `None` after each **successfully completed task**, so
   the max-wait window is per-exhaustion-episode only and does not accumulate across a run.

`_run_with_retries` returns immediately on `claude_quota_exhausted` to avoid burning retry
attempts on a transient quota state.

### Configuration

All quota settings follow the standard precedence: **CLI flag > env var > `.ao/config.yaml` > built-in default**.

| Setting | CLI flag | Env var | Config key | Default |
|---------|----------|---------|------------|---------|
| Max wait per episode | `--quota-max-wait` | `AO_QUOTA_MAX_WAIT_SECONDS` | `quota_max_wait_seconds` | 21600 (6 h) |
| Poll interval | `--quota-poll-interval` | `AO_QUOTA_POLL_SECONDS` | `quota_poll_seconds` | 900 (15 min) |

## 10. Errors (`errors.py`)
`OrchestratorError` (base) → `SpecValidationError`, `CycleError`, `MissingInputError`, `ArtifactPathError`, `ExecutorError`, `ConfigError`. Each carries structured fields (ids/paths), no payload contents.

## 11. Per-project config file (`project_config.py`)

`ProjectConfig` (pydantic) is loaded from `.ao/config.yaml` or `ao.yaml` by walking up from cwd
to the git root. All runtime execution settings are exposed in the config file at the lowest
priority (CLI > env > config > built-in):

```yaml
# .ao/config.yaml
workflow:  path/to/workflow.json
reposets:  path/to/reposet.json
agents:    path/to/agents.json

max_attempts: 3          # AO_MAX_ATTEMPTS
max_turns: 30            # AO_MAX_TURNS
model: claude-sonnet-4-6 # AO_MODEL
effort: medium           # AO_EFFORT  (low | medium | high)

quota_max_wait_seconds: 21600   # AO_QUOTA_MAX_WAIT_SECONDS
quota_poll_seconds: 900         # AO_QUOTA_POLL_SECONDS
```

`_resolve_run_settings()` in `cli.py` implements the three-layer merge (CLI > env > config) for
`run` and `resume` commands, loading the project config only once per invocation.

## 12. CLI (`cli.py`, typer)
```
ao validate  --workflow ...  [--reposets ...] [--agents ...]
ao run       --workflow ...  [--reposets ...] [--agents ...]
ao resume    --run-id <id>   [--workflow ...] [--reposets ...] [--agents ...]
ao status    --run-id <id>   [--workflow ...] [--reposets ...] [--agents ...]
ao prune     --workspace <path> [--older-than N] [--dry-run]
```

`run` and `resume` accept the full set of runtime overrides:

| Flag | Env var | Description |
|------|---------|-------------|
| `--model` | `AO_MODEL` | Claude model for all agents |
| `--effort` | `AO_EFFORT` | `low` / `medium` / `high` |
| `--max-attempts` | `AO_MAX_ATTEMPTS` | Max task attempts (overrides workflow defaults) |
| `--max-turns` | `AO_MAX_TURNS` | Max turns per claude invocation |
| `--quota-max-wait` | `AO_QUOTA_MAX_WAIT_SECONDS` | Max wait for quota exhaustion episode |
| `--quota-poll-interval` | `AO_QUOTA_POLL_SECONDS` | Poll interval during quota wait |

- `validate` → exit 0 / non-zero with the failing schema path or cycle.
- `run`/`resume` print a compact per-task status table from RunState (no payloads).
- All file locations come from flags/env; no hardcoded paths.

## 13. Determinism & testing notes
- Topo order tie-break by sorted id; cron uses injected clock; retries use injected sleeper; FakeExecutor is deterministic. All assertions replayable (CLAUDE.md testing rules).
- Context-hygiene test (T5/T12): a `RecordingExecutor` asserts the `TaskContext` it received contains no field equal to any artifact's file content; a static test greps the engine module for forbidden content-reading calls on artifact paths.
