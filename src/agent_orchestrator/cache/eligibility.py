"""Fail-closed structural eligibility for the result cache (E-Rc4Hk8, HLD 8.3, ADR-0019 D10).

`check_eligibility` answers "is this a plain agent-dispatch task the cache can handle?". It runs
AFTER the author-policy check (`settings.task_cache_policy`), is pure (no I/O, no logging) and
uses public model helpers only. Checks on resolved paths (control and sensitive outputs) live in
`keys.py`.

The classification is an allowlist, enforced twice:

* **Tripwires (tests U-E1 / U-E2).** Every `TaskSpec`, `WorkflowSpec` and `WorkflowDefaults`
  field must be classified in the tables below, so a field added by a sibling epic fails CI until
  someone decides what it means for the cache.
* **Runtime rules.** An unclassified field (a subclass field, or a field added after the tables
  were written) with a non-default value makes the task ineligible, so a cached run can never
  silently ignore behaviour it does not understand.
"""

from __future__ import annotations

import posixpath
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from ..models import (
    ISOLATION_NONE,
    resolve_effective_agent,
    resolve_task_isolation,
    strip_iter_suffix,
)
from ..usage import verdict_path_for
from .constants import (
    AGENT_KEY_FIELDS,
    AGENT_NON_KEY_FIELDS,
    CACHEABLE_COMMAND_BASENAMES,
    CACHEABLE_EXECUTORS,
    MAX_TEXT_CHARS,
    MODEL_FLAGS,
    REASON_AGENT_UNKNOWN,
    REASON_BREAKER_VERDICT_SOURCE,
    REASON_COMMAND_NOT_CACHEABLE,
    REASON_EMIT_TASKS,
    REASON_EXECUTOR_NOT_CACHEABLE,
    REASON_ISOLATION_WORKTREE,
    REASON_LOOP_MEMBER,
    REASON_MODEL_UNRESOLVED,
    REASON_NO_OUTPUTS,
    REASON_OUTPUT_MANIFEST,
    REASON_POST_HOOK,
    REASON_PRE_HOOK,
    REASON_ROUTER_TASK,
    REASON_RUN_INTEGRATION_ACTIVE,
    REASON_TASK_MANIFEST_PATH,
    REASON_UNKNOWN_AGENT_FIELD,
    REASON_UNKNOWN_TASK_FIELD,
    REASON_UNKNOWN_WORKFLOW_FIELD,
    REASON_VERDICT_SIDECAR_UNDECLARED,
)

if TYPE_CHECKING:
    from ..models import AgentSpec, TaskSpec, WorkflowSpec

# `--model=<id>` spelling accepted as "the model is baked into the command" (MODEL_FLAGS holds
# the separate-token spellings).
_MODEL_EQ_PREFIX = "--model="
_CONDITION_VERDICT = "verdict"
_EXECUTOR_CLAUDE_CLI = "claude_cli"
_DEFAULTS_PREFIX = "defaults."


@dataclass(frozen=True)
class Eligibility:
    """The verdict of `check_eligibility`: eligible, or a stable reason plus optional detail."""

    eligible: bool
    reason: str | None = None
    detail: str | None = None


@dataclass(frozen=True)
class Rule:
    """A RULED `TaskSpec` field: `ok(value)` must hold, else the task is ineligible with
    `reason`. `ok=None` means the value is judged by a dedicated rule further down the
    predicate (the field is still RULED, i.e. not freely settable)."""

    ok: Callable[[Any], bool] | None
    reason: str


# `TaskSpec` fields that may take ANY value: they either enter the key (agent, instruction,
# inputs, outputs, model/effort/max_turns via the effective agent) or only bound scheduling and
# execution (retries, timeout, join, ...). `cache` is the author policy, checked beforehand.
TASK_ANY_VALUE: frozenset[str] = frozenset(
    {
        "id",
        "agent",
        "model",
        "effort",
        "max_turns",
        "instruction",
        "inputs",
        "outputs",
        "depends_on",
        "retries",
        "timeout_seconds",
        "skip_if_outputs_exist",
        "join",
        "touches",
        "cache",
    }
)

# `TaskSpec` fields that change what the task DOES beyond writing its declared outputs: only the
# default (inert) value is cacheable.
TASK_VALUE_RULED: dict[str, Rule] = {
    "emit_tasks": Rule(lambda v: not v, REASON_EMIT_TASKS),
    "task_manifest_path": Rule(lambda v: v is None, REASON_TASK_MANIFEST_PATH),
    "output_manifest": Rule(lambda v: v is None, REASON_OUTPUT_MANIFEST),
    "pre_hook": Rule(lambda v: v is None, REASON_PRE_HOOK),
    "post_hook": Rule(lambda v: v is None, REASON_POST_HOOK),
    # Judged on the RESOLVED mode (`resolve_task_isolation`), not the raw field.
    "isolation": Rule(None, REASON_ISOLATION_WORKTREE),
    # Judged by the verdict-sidecar rule below.
    "verdict_path": Rule(None, REASON_VERDICT_SIDECAR_UNDECLARED),
}

# How each `WorkflowSpec` field is covered (HLD 8.3.1). The value documents the mechanism; the
# runtime rule only needs the key set.
WORKFLOW_FIELD_COVERAGE: dict[str, str] = {
    "version": "not semantic",
    "id": "not semantic",
    "name": "not semantic",
    "repo_set": "repo paths enter the prompt via repo_paths; HEADs enter the key",
    "tasks": "per-task eligibility",
    "defaults": "its subfields, classified in DEFAULTS_FIELD_COVERAGE",
    "budget": "not semantic for outputs",
    "triggers": "not semantic for outputs",
    "scheduling": "not semantic for outputs",
    "loops": "loop-member rule; control outputs",
    "branches": "router-task rule; control outputs",
    "circuit_breakers": "breaker-verdict-source rule; control outputs",
    "general_instructions": "content in the key",
    "prompt_path": "content in the key; also a control path",
    "integration": "integration-active rule",
    "hooks": "no-hooks rule (pre_hook/post_hook must be None)",
}

DEFAULTS_FIELD_COVERAGE: dict[str, str] = {
    "retries": "not semantic",
    "timeout_seconds": "not semantic",
    "isolation": "isolation rule (resolve_task_isolation)",
    "model": "effective agent and argv",
    "cache": "author policy",
}

# Shown by the U-E1 tripwire when a TaskSpec field is unclassified (HLD 8.3.1, verbatim).
TASK_TRIPWIRE_MESSAGE = (
    "classify the field in cache/eligibility.py — ANY only if it can change neither what the "
    "agent is asked to do nor which files the task produces; otherwise RULED (default-only); an "
    "approval/human-gate field (E-Ag7Pw3) is ALWAYS RULED"
)


def _no(reason: str, detail: str | None = None) -> Eligibility:
    return Eligibility(False, reason, detail)


def _default_of(model_cls: type[BaseModel], name: str) -> Any:
    """The declared default of *name* (factory defaults are called, so mutable defaults like
    `[]` compare by value)."""
    return model_cls.model_fields[name].get_default(call_default_factory=True)


def _first_unclassified_field(
    obj: BaseModel, classified: Callable[[str], bool], prefix: str = ""
) -> str | None:
    """The first (sorted) field of ``type(obj)`` that is not classified and is not at its
    default, as ``prefix + name``; None when every such field is inert."""
    model_cls = type(obj)
    for name in sorted(model_cls.model_fields):
        if classified(name):
            continue
        if getattr(obj, name) != _default_of(model_cls, name):
            return prefix + name
    return None


def _agent_command_problem(eff: AgentSpec) -> Eligibility | None:
    """Executor allowlist (D10): only the real `claude` CLI or the fake test executor, and for
    the CLI a statically resolvable model."""
    if eff.executor not in CACHEABLE_EXECUTORS:
        return _no(REASON_EXECUTOR_NOT_CACHEABLE, eff.executor)
    if eff.executor != _EXECUTOR_CLAUDE_CLI:
        return None
    base_cmd = posixpath.basename(eff.command_template[0]) if eff.command_template else ""
    if base_cmd not in CACHEABLE_COMMAND_BASENAMES:
        return _no(REASON_COMMAND_NOT_CACHEABLE, base_cmd[:MAX_TEXT_CHARS])
    tokens = [*eff.command_template, *eff.extra_args]
    has_model_flag = any(t in MODEL_FLAGS or t.startswith(_MODEL_EQ_PREFIX) for t in tokens)
    if eff.model is None and not has_model_flag:
        # The CLI's own default model cannot be keyed.
        return _no(REASON_MODEL_UNRESOLVED)
    return None


def check_eligibility(
    task: TaskSpec,
    workflow: WorkflowSpec,
    agents: Mapping[str, AgentSpec],
    *,
    integration_active: bool,
) -> Eligibility:
    """Structural eligibility of *task* (HLD 8.3.2). The first violated rule wins, in the
    order of the HLD, so the reason is stable and deterministic."""
    if integration_active:
        return _no(REASON_RUN_INTEGRATION_ACTIVE)

    # Task fields, sorted. Iterating `type(task)` means subclass fields are seen too.
    for name in sorted(type(task).model_fields):
        if name in TASK_ANY_VALUE:
            continue
        rule = TASK_VALUE_RULED.get(name)
        if rule is not None:
            if rule.ok is not None and not rule.ok(getattr(task, name)):
                return _no(rule.reason)
            continue
        if getattr(task, name) != _default_of(type(task), name):
            return _no(REASON_UNKNOWN_TASK_FIELD, name)

    unknown = _first_unclassified_field(workflow, WORKFLOW_FIELD_COVERAGE.__contains__)
    if unknown is None:
        unknown = _first_unclassified_field(
            workflow.defaults, DEFAULTS_FIELD_COVERAGE.__contains__, _DEFAULTS_PREFIX
        )
    if unknown is not None:
        return _no(REASON_UNKNOWN_WORKFLOW_FIELD, unknown)

    if resolve_task_isolation(task, workflow) != ISOLATION_NONE:
        return _no(REASON_ISOLATION_WORKTREE)
    if any(r.router_task_id == task.id for r in workflow.branches):
        return _no(REASON_ROUTER_TASK)
    base = strip_iter_suffix(task.id)
    if any(base == lp.gate_task_id or base in lp.body for lp in workflow.loops):
        return _no(REASON_LOOP_MEMBER)
    if not task.outputs:
        return _no(REASON_NO_OUTPUTS)

    agent = agents.get(task.agent)
    if agent is None:
        return _no(REASON_AGENT_UNKNOWN)
    eff = resolve_effective_agent(task, agent, workflow.defaults.model)
    # AgentSpec runtime rule: a field nobody classified would change agent behaviour without
    # changing the key.
    bad_agent_field = _first_unclassified_field(
        eff, lambda f: f in AGENT_KEY_FIELDS or f in AGENT_NON_KEY_FIELDS
    )
    if bad_agent_field is not None:
        return _no(REASON_UNKNOWN_AGENT_FIELD, bad_agent_field)
    problem = _agent_command_problem(eff)
    if problem is not None:
        return problem

    outs = {posixpath.normpath(o) for o in task.outputs}
    sidecar = verdict_path_for(task)
    if sidecar is not None and posixpath.normpath(sidecar) not in outs:
        return _no(REASON_VERDICT_SIDECAR_UNDECLARED)
    if any(
        b.condition == _CONDITION_VERDICT and b.task_id == task.id
        for b in workflow.circuit_breakers
    ):
        return _no(REASON_BREAKER_VERDICT_SOURCE)
    return Eligibility(True)
