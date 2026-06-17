"""Pydantic v2 models for the agent orchestrator."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class RepoRef(BaseModel):
    id: str
    path: str
    role: Literal["primary", "support"] = "support"


class RepoSet(BaseModel):
    description: str = ""
    repos: list[RepoRef]
    workspace_root: str


class RetryPolicy(BaseModel):
    max_attempts: int = 1
    backoff_seconds: float = 0.0


class AgentSpec(BaseModel):
    executor: Literal["claude_cli", "fake"]
    command_template: list[str] = ["claude", "-p", "{prompt}"]
    prompt_template: str = (
        "Follow the instructions in {instruction}. "
        "Input artifacts: {inputs}. Write outputs to: {outputs}. Repos: {repos}."
    )
    context_window: Literal["isolated", "shared"] = "isolated"
    extra_args: list[str] = []


class TaskSpec(BaseModel):
    id: str
    agent: str
    instruction: str
    inputs: list[str] = []
    outputs: list[str] = []
    output_manifest: str | None = None
    depends_on: list[str] = []
    retries: RetryPolicy | None = None
    timeout_seconds: int | None = None
    skip_if_outputs_exist: bool = True


class WorkflowDefaults(BaseModel):
    retries: RetryPolicy = RetryPolicy()
    timeout_seconds: int = 1800


class Trigger(BaseModel):
    type: Literal["manual", "cron", "event"]
    schedule: str | None = None
    timezone: str = "UTC"
    event: str | None = None


class WorkflowSpec(BaseModel):
    version: str
    id: str
    name: str = ""
    repo_set: str
    defaults: WorkflowDefaults = WorkflowDefaults()
    triggers: list[Trigger] = [Trigger(type="manual")]
    tasks: list[TaskSpec]

    def task(self, task_id: str) -> TaskSpec:
        for t in self.tasks:
            if t.id == task_id:
                return t
        raise KeyError(task_id)


# ---------------------------------------------------------------------------
# Runtime-only models
# ---------------------------------------------------------------------------


class TaskContext(BaseModel):
    """NFR-1 boundary: paths/ids only — NO file contents."""

    run_id: str
    task_id: str
    agent: AgentSpec
    instruction_path: str
    input_paths: list[str]
    output_paths: list[str]
    output_manifest_path: str | None = None
    dynamic_input_paths: list[str] = []
    repo_paths: dict[str, str]
    timeout_seconds: int


class TaskResult(BaseModel):
    task_id: str
    status: Literal["succeeded", "failed", "cancelled", "timed_out"]
    attempts: int
    exit_code: int | None = None
    error: str | None = None


TaskStatus = Literal[
    "pending", "running", "succeeded", "failed", "skipped", "cancelled", "timed_out"
]


class TaskRunState(BaseModel):
    status: TaskStatus = "pending"
    attempts: int = 0
    started_at: str | None = None
    ended_at: str | None = None
    outputs_present: bool = False
    dynamic_outputs: list[str] = []


class RunState(BaseModel):
    run_id: str
    workflow_id: str
    repo_set: str
    started_at: str
    updated_at: str
    status: Literal["running", "succeeded", "failed", "cancelled"] = "running"
    tasks: dict[str, TaskRunState] = {}
