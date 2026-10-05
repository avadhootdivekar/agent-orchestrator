"""T-QgQy08 AC-1..AC-8: the fail-closed structural eligibility predicate (U-E1..U-E28)."""

from __future__ import annotations

import builtins
import os
import subprocess
from typing import Any

import pytest

from agent_orchestrator.cache import constants as c
from agent_orchestrator.cache.eligibility import (
    DEFAULTS_FIELD_COVERAGE,
    TASK_ANY_VALUE,
    TASK_TRIPWIRE_MESSAGE,
    TASK_VALUE_RULED,
    WORKFLOW_FIELD_COVERAGE,
    Eligibility,
    check_eligibility,
)
from agent_orchestrator.models import (
    AgentSpec,
    CircuitBreakerSpec,
    HookRef,
    HookSpec,
    LoopSpec,
    RouterSpec,
    RouteSpec,
    TaskSpec,
    WorkflowDefaults,
    WorkflowSpec,
    _is_structural_task,
)

CLAUDE_AGENT = AgentSpec(executor="claude_cli", model="opus")


def _task(tid: str = "t", **kw: object) -> TaskSpec:
    base: dict = {"id": tid, "agent": "ag", "instruction": "i.md", "outputs": ["out/t.md"]}
    base.update(kw)
    return TaskSpec(**base)


def _wf(tasks: list[TaskSpec] | None = None, **kw: object) -> WorkflowSpec:
    return WorkflowSpec(
        version="1.0",
        id="wf",
        repo_set="rs",
        tasks=tasks or [_task()],
        **kw,  # type: ignore[arg-type]
    )


def _check(
    task: TaskSpec | None = None,
    wf: WorkflowSpec | None = None,
    agents: dict[str, AgentSpec] | None = None,
    *,
    integration_active: bool = False,
) -> Eligibility:
    task = task or _task()
    return check_eligibility(
        task,
        wf or _wf([task]),
        {"ag": CLAUDE_AGENT} if agents is None else agents,
        integration_active=integration_active,
    )


def _no(reason: str, detail: str | None = None) -> Eligibility:
    return Eligibility(False, reason, detail)


class TestTripwires:
    def test_u_e1_task_fields_are_all_classified(self) -> None:
        classified = TASK_ANY_VALUE | set(TASK_VALUE_RULED)
        assert set(TaskSpec.model_fields) == classified, TASK_TRIPWIRE_MESSAGE

    def test_any_and_ruled_are_disjoint(self) -> None:
        assert not (TASK_ANY_VALUE & set(TASK_VALUE_RULED))

    def test_tripwire_message_is_the_hld_text(self) -> None:
        assert "ANY only if it can change neither what the agent is asked to do" in (
            TASK_TRIPWIRE_MESSAGE
        )
        assert "an approval/human-gate field (E-Ag7Pw3) is ALWAYS RULED" in TASK_TRIPWIRE_MESSAGE

    def test_u_e2_workflow_and_defaults_coverage(self) -> None:
        assert set(WorkflowSpec.model_fields) <= set(WORKFLOW_FIELD_COVERAGE)
        assert set(WorkflowDefaults.model_fields) <= set(DEFAULTS_FIELD_COVERAGE)

    def test_coverage_has_no_stale_entries(self) -> None:
        assert set(WORKFLOW_FIELD_COVERAGE) == set(WorkflowSpec.model_fields)
        assert set(DEFAULTS_FIELD_COVERAGE) == set(WorkflowDefaults.model_fields)

    def test_ruled_reasons_are_known_reason_constants(self) -> None:
        known = {v for k, v in vars(c).items() if k.startswith("REASON_")}
        assert {r.reason for r in TASK_VALUE_RULED.values()} <= known


class TestPositive:
    def test_plain_task_is_eligible(self) -> None:
        assert _check() == Eligibility(True)

    def test_absolute_claude_path_is_eligible(self) -> None:
        agent = AgentSpec(
            executor="claude_cli",
            model="opus",
            command_template=["/usr/local/bin/claude", "-p", "{prompt}"],
        )
        assert _check(agents={"ag": agent}).eligible

    def test_model_flag_baked_into_command_template(self) -> None:
        agent = AgentSpec(
            executor="claude_cli", command_template=["claude", "--model", "opus", "-p", "{prompt}"]
        )
        assert _check(agents={"ag": agent}).eligible

    def test_model_eq_form_in_extra_args(self) -> None:
        agent = AgentSpec(executor="claude_cli", extra_args=["--model=opus"])
        assert _check(agents={"ag": agent}).eligible

    def test_short_model_flag_in_extra_args(self) -> None:
        agent = AgentSpec(executor="claude_cli", extra_args=["-m", "opus"])
        assert _check(agents={"ag": agent}).eligible

    def test_fake_executor_is_eligible(self) -> None:
        assert _check(agents={"ag": AgentSpec(executor="fake")}).eligible

    def test_task_model_resolves_a_model_less_agent(self) -> None:
        agent = AgentSpec(executor="claude_cli")
        assert _check(_task(model="opus"), agents={"ag": agent}).eligible

    def test_defaults_model_resolves_a_model_less_agent(self) -> None:
        agent = AgentSpec(executor="claude_cli")
        task = _task()
        wf = _wf([task], defaults=WorkflowDefaults(model="opus"))
        assert _check(task, wf, {"ag": agent}).eligible

    def test_review_task_with_declared_sidecar(self) -> None:
        task = _task(outputs=["out/review.md", "out/review-verdict.json"])
        assert _check(task).eligible

    def test_explicit_verdict_path_declared_as_output(self) -> None:
        task = _task(outputs=["out/a.md", "./out/v.json"], verdict_path="out/v.json")
        assert _check(task).eligible

    def test_any_value_fields_do_not_disqualify(self) -> None:
        task = _task(
            depends_on=["x"],
            timeout_seconds=5,
            skip_if_outputs_exist=False,
            join="any",
            touches=["src/**"],
            cache=True,
            effort="low",
            max_turns=3,
        )
        assert _check(task).eligible


class TestRuntimeUnknownFields:
    """AC-3 and U-E27."""

    def test_unknown_task_field_at_default_is_eligible(self) -> None:
        class MyTask(TaskSpec):
            extra: int = 0

        assert _check(MyTask(id="t", agent="ag", instruction="i", outputs=["o"])).eligible

    def test_unknown_task_field_non_default(self) -> None:
        class MyTask(TaskSpec):
            extra: int = 0

        task = MyTask(id="t", agent="ag", instruction="i", outputs=["o"], extra=1)
        assert _check(task) == _no(c.REASON_UNKNOWN_TASK_FIELD, "extra")

    def test_unknown_task_field_mutable_default_compared_by_value(self) -> None:
        class MyTask(TaskSpec):
            extra: list[str] = []

        ok = MyTask(id="t", agent="ag", instruction="i", outputs=["o"])
        assert _check(ok).eligible
        bad = MyTask(id="t", agent="ag", instruction="i", outputs=["o"], extra=["x"])
        assert _check(bad) == _no(c.REASON_UNKNOWN_TASK_FIELD, "extra")

    def test_unknown_workflow_field(self) -> None:
        class MyWorkflow(WorkflowSpec):
            extra: str = ""

        task = _task()
        base: dict[str, Any] = {"version": "1.0", "id": "wf", "repo_set": "rs", "tasks": [task]}
        assert _check(task, MyWorkflow(**base)).eligible
        assert _check(task, MyWorkflow(**base, extra="x")) == _no(
            c.REASON_UNKNOWN_WORKFLOW_FIELD, "extra"
        )

    def test_unknown_defaults_field(self) -> None:
        class MyDefaults(WorkflowDefaults):
            extra: int = 0

        task = _task()
        ok = _wf([task], defaults=MyDefaults())
        assert _check(task, ok).eligible
        bad = _wf([task], defaults=MyDefaults(extra=2))
        assert _check(task, bad) == _no(c.REASON_UNKNOWN_WORKFLOW_FIELD, "defaults.extra")

    def test_unknown_agent_field(self) -> None:
        class MyAgent(AgentSpec):
            extra: int = 0

        ok = MyAgent(executor="claude_cli", model="opus")
        assert _check(agents={"ag": ok}).eligible
        bad = MyAgent(executor="claude_cli", model="opus", extra=7)
        assert _check(agents={"ag": bad}) == _no(c.REASON_UNKNOWN_AGENT_FIELD, "extra")

    def test_agent_rule_reads_the_effective_agent(self) -> None:
        class MyAgent(AgentSpec):
            extra: int = 0

        agent = MyAgent(executor="claude_cli")
        task = _task()
        wf = _wf([task], defaults=WorkflowDefaults(model="opus"))
        # defaults.model overrides the agent via model_copy: the subclass and its (default)
        # extra field survive, and the override does not trip the rule.
        assert _check(task, wf, {"ag": agent}).eligible

    def test_forbidden_task_models_is_a_non_key_field_and_never_trips(self) -> None:
        agent = AgentSpec(executor="claude_cli", model="opus", forbidden_task_models=["haiku"])
        assert _check(agents={"ag": agent}).eligible


class TestEligibilityReasons:
    """U-E3..U-E26: one test per eligibility-owned reason."""

    def test_run_integration_active(self) -> None:
        assert _check(integration_active=True) == _no(c.REASON_RUN_INTEGRATION_ACTIVE)

    def test_emit_tasks(self) -> None:
        assert _check(_task(emit_tasks=True)) == _no(c.REASON_EMIT_TASKS)

    def test_task_manifest_path(self) -> None:
        task = _task(task_manifest_path="out/tasks.json")
        assert _check(task) == _no(c.REASON_TASK_MANIFEST_PATH)

    def test_output_manifest(self) -> None:
        assert _check(_task(output_manifest="out/m.json")) == _no(c.REASON_OUTPUT_MANIFEST)

    def test_pre_hook(self) -> None:
        task = _task(pre_hook=HookRef(use="h"))
        wf = _wf([task], hooks={"h": HookSpec(command=["true"])})
        assert _check(task, wf) == _no(c.REASON_PRE_HOOK)

    def test_post_hook(self) -> None:
        task = _task(post_hook=HookRef(use="h"))
        wf = _wf([task], hooks={"h": HookSpec(command=["true"])})
        assert _check(task, wf) == _no(c.REASON_POST_HOOK)

    def test_isolation_worktree_from_task(self) -> None:
        assert _check(_task(isolation="worktree")) == _no(c.REASON_ISOLATION_WORKTREE)

    def test_isolation_worktree_from_defaults(self) -> None:
        task = _task()
        wf = _wf([task], defaults=WorkflowDefaults(isolation="worktree"))
        assert _check(task, wf) == _no(c.REASON_ISOLATION_WORKTREE)

    def test_explicit_none_isolation_beats_worktree_default(self) -> None:
        task = _task(isolation="none")
        wf = _wf([task], defaults=WorkflowDefaults(isolation="worktree"))
        assert _check(task, wf).eligible

    def test_router_task(self) -> None:
        task = _task("route")
        router = RouterSpec(
            id="r",
            router_task_id="route",
            verdict_path="o/v.json",
            routes={"a": RouteSpec(entry=[])},
        )
        assert _check(task, _wf([task], branches=[router])) == _no(c.REASON_ROUTER_TASK)

    @pytest.mark.parametrize("tid", ["dev", "dev__iter3", "gate", "gate__iter2"])
    def test_loop_member_and_gate_including_clones(self, tid: str) -> None:
        loop = LoopSpec(
            id="l", body=["dev", "gate"], gate_task_id="gate", gate_output_path="g.json"
        )
        task = _task(tid)
        assert _check(task, _wf([task], loops=[loop])) == _no(c.REASON_LOOP_MEMBER)

    def test_task_outside_a_loop_is_unaffected(self) -> None:
        loop = LoopSpec(
            id="l", body=["dev", "gate"], gate_task_id="gate", gate_output_path="g.json"
        )
        task = _task("other")
        assert _check(task, _wf([task], loops=[loop])).eligible

    def test_no_outputs(self) -> None:
        assert _check(_task(outputs=[])) == _no(c.REASON_NO_OUTPUTS)

    def test_agent_unknown(self) -> None:
        assert _check(agents={}) == _no(c.REASON_AGENT_UNKNOWN)

    def test_executor_not_cacheable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "agent_orchestrator.cache.eligibility.CACHEABLE_EXECUTORS", {"claude_cli"}
        )
        assert _check(agents={"ag": AgentSpec(executor="fake")}) == _no(
            c.REASON_EXECUTOR_NOT_CACHEABLE, "fake"
        )

    def test_command_not_cacheable_wrapper_script(self) -> None:
        agent = AgentSpec(
            executor="claude_cli", model="opus", command_template=["./wrap.sh", "-p", "{prompt}"]
        )
        assert _check(agents={"ag": agent}) == _no(c.REASON_COMMAND_NOT_CACHEABLE, "wrap.sh")

    def test_command_not_cacheable_empty_template(self) -> None:
        agent = AgentSpec(executor="claude_cli", model="opus", command_template=[])
        assert _check(agents={"ag": agent}) == _no(c.REASON_COMMAND_NOT_CACHEABLE, "")

    def test_command_detail_is_clipped(self) -> None:
        agent = AgentSpec(
            executor="claude_cli", model="opus", command_template=["x" * 1000, "{prompt}"]
        )
        verdict = _check(agents={"ag": agent})
        assert verdict.reason == c.REASON_COMMAND_NOT_CACHEABLE
        assert verdict.detail == "x" * c.MAX_TEXT_CHARS

    def test_model_unresolved(self) -> None:
        agent = AgentSpec(executor="claude_cli")
        assert _check(agents={"ag": agent}) == _no(c.REASON_MODEL_UNRESOLVED)

    def test_verdict_sidecar_undeclared_review_md(self) -> None:
        task = _task(outputs=["out/review.md"])
        assert _check(task) == _no(c.REASON_VERDICT_SIDECAR_UNDECLARED)

    def test_verdict_sidecar_undeclared_explicit_path(self) -> None:
        task = _task(outputs=["out/a.md"], verdict_path="out/v.json")
        assert _check(task) == _no(c.REASON_VERDICT_SIDECAR_UNDECLARED)

    def test_breaker_verdict_source(self) -> None:
        task = _task()
        breaker = CircuitBreakerSpec(
            id="b", condition="verdict", action="stop", task_id="t", verdict_path="out/v.json"
        )
        assert _check(task, _wf([task], circuit_breakers=[breaker])) == _no(
            c.REASON_BREAKER_VERDICT_SOURCE
        )

    def test_other_breaker_conditions_do_not_disqualify(self) -> None:
        task = _task()
        breaker = CircuitBreakerSpec(
            id="b", condition="task_failures", action="fail", task_id="t", threshold=2
        )
        assert _check(task, _wf([task], circuit_breakers=[breaker])).eligible


class TestStructuralConsistency:
    """U-E28: every task the isolation resolver treats as structural is ineligible here."""

    def test_every_structural_task_is_ineligible(self) -> None:
        emitter = _task("emit", emit_tasks=True)
        router_t = _task("route")
        gate = _task("gate")
        clone = _task("gate__iter4")
        plain = _task("plain")
        loop = LoopSpec(id="l", body=["gate"], gate_task_id="gate", gate_output_path="g.json")
        router = RouterSpec(
            id="r",
            router_task_id="route",
            verdict_path="o/v.json",
            routes={"a": RouteSpec(entry=[])},
        )
        wf = _wf([emitter, router_t, gate, clone, plain], loops=[loop], branches=[router])
        seen_structural = 0
        for task in wf.tasks:
            verdict = _check(task, wf)
            if _is_structural_task(task, wf):
                seen_structural += 1
                assert not verdict.eligible, task.id
        assert seen_structural == 4
        assert _check(plain, wf).eligible


class TestDeterminismAndPurity:
    def test_first_reason_follows_hld_order_and_is_stable(self) -> None:
        # Violates (in HLD order): integration, ruled fields, isolation, no outputs, agent.
        task = _task(outputs=[], pre_hook=HookRef(use="h"), emit_tasks=True, isolation="worktree")
        results = {
            check_eligibility(task, _wf([task]), {}, integration_active=False) for _ in range(100)
        }
        # Sorted ruled-field order puts emit_tasks before pre_hook.
        assert results == {_no(c.REASON_EMIT_TASKS)}
        assert check_eligibility(task, _wf([task]), {}, integration_active=True) == _no(
            c.REASON_RUN_INTEGRATION_ACTIVE
        )

    def test_ruled_fields_come_before_structural_rules(self) -> None:
        task = _task(outputs=[], isolation="worktree")
        assert check_eligibility(task, _wf([task]), {}, integration_active=False) == _no(
            c.REASON_ISOLATION_WORKTREE
        )

    def test_no_outputs_beats_unknown_agent(self) -> None:
        task = _task(outputs=[])
        assert check_eligibility(task, _wf([task]), {}, integration_active=False) == _no(
            c.REASON_NO_OUTPUTS
        )

    def test_is_pure_no_io(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom(*_a: object, **_k: object) -> None:
            raise AssertionError("eligibility must not do I/O")

        task, wf, agents = _task(), _wf(), {"ag": CLAUDE_AGENT}
        with monkeypatch.context() as patched:
            patched.setattr(builtins, "open", boom)
            patched.setattr(os, "stat", boom)
            patched.setattr(subprocess, "run", boom)
            verdict = check_eligibility(task, wf, agents, integration_active=False)
        assert verdict.eligible

    def test_result_is_a_frozen_dataclass(self) -> None:
        with pytest.raises(AttributeError):
            Eligibility(True).eligible = False  # type: ignore[misc]
